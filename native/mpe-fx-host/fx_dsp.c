/*
 * fx_dsp — see fx_dsp.h for the shape and the level law.
 *
 * RT rules for everything below `fx_engine_init`: no allocation, no syscalls,
 * no locks, no transcendental functions per sample. The coefficients that need
 * `exp` are computed per rate, not per frame.
 */

#define _GNU_SOURCE
#include "fx_dsp.h"

#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

/* Below this distance the depth ramp lands rather than approaching forever —
 * which also keeps the smoother from walking into denormals. */
#define DEPTH_SNAP 1e-5f

/* ------------------------------------------------------------------------- */
/* Chorus                                                                     */
/* ------------------------------------------------------------------------- */

/* A classic synth ensemble chorus: one modulated tap per channel, a slow
 * triangle LFO, the right channel swept in antiphase so the stereo image
 * widens instead of the pitch wobbling as one. Fixed voicing — these are the
 * numbers that read as "chorus" rather than "flanger" (shorter delay, comb
 * colouring) or "vibrato" (no dry). No feedback path, so the wet signal is
 * always an interpolation between two input samples from the last ~9 ms and
 * cannot exceed them.
 *
 * Per stereo frame while active: one triangle step (add, compare, abs, fma),
 * then per channel one delay-time fma, a float->int split, a write, two reads
 * and a lerp — about 25 float ops plus 6 memory ops. No trig, no division. */
#define CHORUS_CENTER_MS 7.0f
#define CHORUS_SWEEP_MS 2.0f   /* ± around the centre: 5..9 ms */
#define CHORUS_RATE_HZ 0.6f

/* Power of two ≥ (centre + sweep) at FX_MAX_RATE plus two taps of headroom:
 * 9 ms × 192 kHz = 1728 samples. */
#define CHORUS_BUF 2048u
#define CHORUS_MASK (CHORUS_BUF - 1u)

typedef struct {
    float buf[FX_CHANNELS][CHORUS_BUF];
    uint32_t w;
    float phase;      /* 0..1 */
    float phase_inc;
    float center;     /* samples */
    float sweep;      /* samples */
} chorus_state;

static int chorus_init(void *state, float rate)
{
    chorus_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    s->w = 0;
    s->phase = 0.0f;
    s->phase_inc = CHORUS_RATE_HZ / rate;
    s->center = CHORUS_CENTER_MS * 0.001f * rate;
    s->sweep = CHORUS_SWEEP_MS * 0.001f * rate;
    return 0;
}

static inline float chorus_tap(const float *buf, uint32_t w, float delay)
{
    uint32_t whole = (uint32_t)delay;
    float frac = delay - (float)whole;
    float a = buf[(w - whole) & CHORUS_MASK];
    float b = buf[(w - whole - 1u) & CHORUS_MASK];
    return a + frac * (b - a);
}

static void chorus_process(void *state, const float *const in[FX_CHANNELS],
                           float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    chorus_state *s = state;
    uint32_t w = s->w;
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {
        /* Read the inputs before any write: `wet` may share memory with them. */
        s->buf[0][w] = in[0][i];
        s->buf[1][w] = in[1][i];

        float tri = 4.0f * fabsf(phase - 0.5f) - 1.0f;  /* -1..1 */
        phase += s->phase_inc;
        if (phase >= 1.0f) {
            phase -= 1.0f;
        }
        wet[0][i] = chorus_tap(s->buf[0], w, s->center + s->sweep * tri);
        wet[1][i] = chorus_tap(s->buf[1], w, s->center - s->sweep * tri);
        w = (w + 1u) & CHORUS_MASK;
    }
    s->w = w;
    s->phase = phase;
}

/* Bypass: keep the delay line current, skip the LFO and the reads. The LFO
 * resumes where it stopped, which is as continuous as it needs to be. */
static void chorus_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    chorus_state *s = state;
    uint32_t w = s->w;
    for (uint32_t i = 0; i < n; i++) {
        s->buf[0][w] = in[0][i];
        s->buf[1][w] = in[1][i];
        w = (w + 1u) & CHORUS_MASK;
    }
    s->w = w;
}

/* ------------------------------------------------------------------------- */
/* Shared helpers                                                             */
/* ------------------------------------------------------------------------- */

/* Filter memories below this are set to exactly zero. Without it a recursive
 * filter fed silence decays through the subnormal range (slow on some CPUs)
 * and never reaches the exact silence the self-test asks for. -300 dBFS is
 * far below anything audible. */
#define FX_FLUSH 1e-15f

static inline float flush(float x)
{
    return fabsf(x) < FX_FLUSH ? 0.0f : x;
}

/* What a recursive filter reads. One NaN or Inf from upstream would otherwise
 * sit in its memory for good, and the engine's clamp would turn the effect
 * into permanent silence. NaN fails the comparison and becomes 0. */
static inline float finite_or_zero(float x)
{
    return fabsf(x) <= 3.0e38f ? x : 0.0f;
}

/* One-pole coefficient for a corner at `hz`. Startup / rate change only. */
static float pole_alpha(float hz, float rate)
{
    return (float)(1.0 - exp(-2.0 * M_PI * (double)hz / (double)rate));
}

/* A sine-like LFO without trig: the chorus's triangle, rounded at its turns by
 * x(1.5 - 0.5x^2), which maps [-1, 1] onto itself monotonically with zero
 * slope at the ends. Returns -1..1 for phase 0..1. */
static inline float lfo_shape(float phase)
{
    float tri = 4.0f * fabsf(phase - 0.5f) - 1.0f;
    return tri * (1.5f - 0.5f * tri * tri);
}

static inline float wrap01(float p)
{
    return p >= 1.0f ? p - 1.0f : p;
}

/* ------------------------------------------------------------------------- */
/* Tube drive                                                                 */
/* ------------------------------------------------------------------------- */

/* Signal path, per channel:
 *
 *   x -> low-shelf cut -> x G -> [4x up] -> asymmetric soft clip -> [4x down]
 *     -> / G -> DC block -> low-pass -> x makeup -> wet
 *
 * Fixed voicing (v1):
 *   - Pre: a first-order low shelf, -6 dB below ~180 Hz, so the bass does not
 *     dominate the clipping (tighter, less mud) — the overdrive-pedal habit.
 *   - Drive G = 0.5 (1 + 31 a^2) for knob a: 0.5 (clean) .. 16 (+30 dB).
 *   - Shaper: f(u) = (s(u + 0.2) - s(0.2)) / 1.5, s the cubic soft clip
 *     s(v) = 1.5v - 0.5v^3 on [-1, 1], flat outside. The 0.2 bias makes it
 *     asymmetric — positive swings clip at +0.47, negative at -0.86 — which
 *     is where the even (second) harmonic comes from. |f(u)| <= |u| always,
 *     since s' <= 1.5.
 *   - Post: a DC blocker at 20 Hz (the asymmetry makes DC) whose gain is
 *     <= 1 at every frequency, and a one-pole low-pass at 7 kHz to take the
 *     fizz off.
 *
 * Oversampling: two cascaded 2x half-band FIR stages (polyphase, so the zero
 * taps cost nothing), steep at the base rate (55 taps, pass 0-20 kHz, stop
 * from 28 kHz at 48 kHz in, ~71 dB) and short at 2x (19 taps, ~69 dB). The
 * same filters decimate on the way down. Linear phase; the wet path is about
 * 0.6 ms late, which is why the mix fades in rather than switching.
 *
 * Level. Dividing by G puts the shaper back at unity small-signal gain, but a
 * clipped signal comes out much quieter than it went in. Automatic makeup:
 *   g = min( sqrt(ms_in / ms_out),  max(1, peak_in * G / 0.864) )
 * The first term is RMS matching (both mean squares over 25 ms, both channels
 * linked so the image does not move). The second is the ceiling that keeps it
 * safe: the shaper's output never exceeds 0.864 / G, so with the makeup no
 * larger than that the output peak cannot exceed the input's peak over the
 * last ~24 ms (peak_in), and a quiet signal (no clipping) gets no makeup at
 * all. Without the ceiling, the tail after a loud note is boosted by the
 * stale makeup for ~100 ms — the self-test's step check shows that mutant
 * failing. g is then smoothed over 2 ms.
 *
 * Bypass runs the whole chain with the output discarded, so every filter
 * memory is current on re-entry.
 *
 * Per stereo frame, active (each add, multiply, compare, abs, divide and
 * sqrt counted as one op; loads and stores not counted):
 *   per channel: shelf and drive 6, up A 43 (14 symmetric pairs), up B 2 x 16,
 *   shaper 4 x 9, down B 2 x 17, down A 44, post (/G, DC, LP, flush) 14,
 *   detector/peak/makeup inputs 5                                    ~= 214
 *   shared: drive law 5 (one divide), detectors 10, peak window 7,
 *   makeup law 11 (one divide, one sqrt)                              ~=  33
 *   => ~460 ops per stereo frame, ~80% of it the four half-band filters.
 *   It runs while bypassed too (see above). Laptop: ~135 ns/frame
 *   (fx-selftest --bench); the Pi figure is the separate measurement.
 */

#define DRIVE_HB_A_K 14
#define DRIVE_HB_B_K 5
#define DRIVE_HB_A_N (2 * DRIVE_HB_A_K)
#define DRIVE_HB_B_N (2 * DRIVE_HB_B_K)

/* Half-band side taps at offsets +-1, +-3, ... (the centre is 0.5 and every
 * other even offset is zero). Kaiser-windowed sinc, scaled so each filter's DC
 * gain is exactly 1; passband ripple under 0.003 dB. */
static const float HB_A[DRIVE_HB_A_K] = {
    3.168793913e-01f, -1.018697963e-01f, 5.681984054e-02f, -3.632471685e-02f,
    2.430002641e-02f, -1.638837999e-02f, 1.091191039e-02f, -7.064643949e-03f,
    4.385128692e-03f, -2.567566597e-03f, 1.386326986e-03f, -6.644775745e-04f,
    2.606995491e-04f, -6.374253246e-05f,
};
static const float HB_B[DRIVE_HB_B_K] = {
    3.054892830e-01f, -7.249931792e-02f, 2.084293995e-02f, -4.007239548e-03f,
    1.743344741e-04f,
};

#define DRIVE_BIAS 0.2f
#define DRIVE_S_BIAS 0.296f        /* s(0.2) = 0.3 - 0.004, exactly */
#define DRIVE_SAT 0.864f           /* largest |f|: (1 + s(0.2)) / 1.5 */
#define DRIVE_SHELF_HZ 180.0f
#define DRIVE_LP_HZ 7000.0f
#define DRIVE_DC_HZ 20.0f
#define DRIVE_DETECT_MS 25.0f
#define DRIVE_MAKEUP_MS 2.0f
#define DRIVE_PEAK_BLOCK_MS 4.0f
#define DRIVE_PEAK_BLOCKS 6        /* window: the current block + 5 before */
#define DRIVE_EPS 1e-10f           /* mean-square floor: silence -> ratio 1 */

/* Newest-first history: each value is written twice, N apart, so the last N
 * values are always contiguous at buf + pos. No shifting, no masking. */
typedef struct {
    float buf[2 * DRIVE_HB_A_N];
    int pos;
} hb_hist;

static inline const float *hist_push(hb_hist *h, int n, float v)
{
    h->pos = h->pos == 0 ? n - 1 : h->pos - 1;
    h->buf[h->pos] = v;
    h->buf[h->pos + n] = v;
    return h->buf + h->pos;
}

/* 2x up: one input, two outputs (the even phase from the side taps, the odd
 * phase the centre tap — a pure delay). */
static inline void hb_up(hb_hist *h, const float *c, int k, float x, float out[2])
{
    const float *w = hist_push(h, 2 * k, x);
    float acc = 0.0f;
    for (int i = 0; i < k; i++) {
        acc += c[i] * (w[k - 1 - i] + w[k + i]);
    }
    out[0] = 2.0f * acc;
    out[1] = w[k - 1];
}

/* 2x down: two inputs (even, odd), one output. */
static inline float hb_down(hb_hist *even, hb_hist *odd, const float *c, int k,
                            float v0, float v1)
{
    const float *e = hist_push(even, 2 * k, v0);
    const float *o = hist_push(odd, 2 * k, v1);
    float acc = 0.0f;
    for (int i = 0; i < k; i++) {
        acc += c[i] * (e[k - 1 - i] + e[k + i]);
    }
    return acc + 0.5f * o[k];
}

static inline float drive_shape(float u)
{
    float v = u + DRIVE_BIAS;
    v = v > 1.0f ? 1.0f : (v < -1.0f ? -1.0f : v);
    return (v * (1.5f - 0.5f * v * v) - DRIVE_S_BIAS) * (1.0f / 1.5f);
}

typedef struct {
    hb_hist up_a, up_b, dn_b_even, dn_b_odd, dn_a_even, dn_a_odd;
    float shelf;        /* low-pass memory for the shelf */
    float dc_x, dc_y;   /* DC blocker */
    float lp;           /* post low-pass */
} drive_chan;

typedef struct {
    drive_chan ch[FX_CHANNELS];
    float a_shelf, a_lp, dc_p, dc_k, a_detect, a_makeup;
    float ms_in, ms_out, makeup;
    float peak_blocks[DRIVE_PEAK_BLOCKS - 1];
    float peak_now;
    uint32_t block_len, block_pos;
} drive_state;

enum { DRIVE_NO_OS = 1, DRIVE_NO_CEILING = 2, DRIVE_NO_FLUSH = 4 };

static int drive_init(void *state, float rate)
{
    drive_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    s->a_shelf = pole_alpha(DRIVE_SHELF_HZ, rate);
    s->a_lp = pole_alpha(DRIVE_LP_HZ, rate);
    s->dc_p = (float)exp(-2.0 * M_PI * (double)DRIVE_DC_HZ / (double)rate);
    s->dc_k = 0.5f * (1.0f + s->dc_p);
    s->a_detect = (float)(1.0 - exp(-1.0 / (DRIVE_DETECT_MS * 0.001 * (double)rate)));
    s->a_makeup = (float)(1.0 - exp(-1.0 / (DRIVE_MAKEUP_MS * 0.001 * (double)rate)));
    s->makeup = 1.0f;
    s->block_len = (uint32_t)(DRIVE_PEAK_BLOCK_MS * 0.001f * rate);
    if (s->block_len < 1u) {
        s->block_len = 1u;
    }
    return 0;
}

/* The base-rate sample through shaper and back. `mode` is a compile-time
 * constant at every call site, so the mutant branches vanish from the build
 * that ships. */
static inline float drive_core(drive_chan *c, float u, int mode)
{
    if (mode & DRIVE_NO_OS) {
        return drive_shape(u);
    }
    float a[2], b[4];
    hb_up(&c->up_a, HB_A, DRIVE_HB_A_K, u, a);
    hb_up(&c->up_b, HB_B, DRIVE_HB_B_K, a[0], b);
    hb_up(&c->up_b, HB_B, DRIVE_HB_B_K, a[1], b + 2);
    for (int j = 0; j < 4; j++) {
        b[j] = drive_shape(b[j]);
    }
    float d0 = hb_down(&c->dn_b_even, &c->dn_b_odd, HB_B, DRIVE_HB_B_K, b[0], b[1]);
    float d1 = hb_down(&c->dn_b_even, &c->dn_b_odd, HB_B, DRIVE_HB_B_K, b[2], b[3]);
    return hb_down(&c->dn_a_even, &c->dn_a_odd, HB_A, DRIVE_HB_A_K, d0, d1);
}

static inline void drive_run(drive_state *s, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount,
                             uint32_t n, int mode)
{
    for (uint32_t i = 0; i < n; i++) {
        const float a = amount[i];
        const float drive = 0.5f * (1.0f + 31.0f * a * a);
        const float inv_drive = 1.0f / drive;
        float x[FX_CHANNELS], y[FX_CHANNELS];
        float sq_in = 0.0f, sq_out = 0.0f;
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            drive_chan *c = &s->ch[ch];
            x[ch] = finite_or_zero(in[ch][i]);   /* before any write: wet may alias in */
            sq_in += x[ch] * x[ch];
            float p = fabsf(x[ch]);
            s->peak_now = p > s->peak_now ? p : s->peak_now;

            c->shelf += s->a_shelf * (x[ch] - c->shelf);
            float v = drive_core(c, (x[ch] - 0.5f * c->shelf) * drive, mode) * inv_drive;
            float dc = s->dc_k * (v - c->dc_x) + s->dc_p * c->dc_y;
            c->dc_x = v;
            c->dc_y = dc;
            c->lp += s->a_lp * (dc - c->lp);
            if (!(mode & DRIVE_NO_FLUSH)) {
                c->shelf = flush(c->shelf);
                c->dc_y = flush(c->dc_y);
                c->lp = flush(c->lp);
            }
            y[ch] = c->lp;
            sq_out += y[ch] * y[ch];
        }

        s->ms_in += s->a_detect * (sq_in - s->ms_in);
        s->ms_out += s->a_detect * (sq_out - s->ms_out);
        if (!(mode & DRIVE_NO_FLUSH)) {
            s->ms_in = flush(s->ms_in);
            s->ms_out = flush(s->ms_out);
        }

        float peak = s->peak_now;
        for (int b = 0; b < DRIVE_PEAK_BLOCKS - 1; b++) {
            peak = s->peak_blocks[b] > peak ? s->peak_blocks[b] : peak;
        }
        if (++s->block_pos >= s->block_len) {
            for (int b = DRIVE_PEAK_BLOCKS - 2; b > 0; b--) {
                s->peak_blocks[b] = s->peak_blocks[b - 1];
            }
            s->peak_blocks[0] = s->peak_now;
            s->peak_now = 0.0f;
            s->block_pos = 0;
        }

        float target = sqrtf((s->ms_in + DRIVE_EPS) / (s->ms_out + DRIVE_EPS));
        if (!(mode & DRIVE_NO_CEILING)) {
            float ceiling = peak * drive * (1.0f / DRIVE_SAT);
            ceiling = ceiling > 1.0f ? ceiling : 1.0f;
            target = target < ceiling ? target : ceiling;
        }
        s->makeup += s->a_makeup * (target - s->makeup);

        if (wet != NULL) {
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                wet[ch][i] = s->makeup * y[ch];
            }
        }
    }
}

static const float ZERO_AMOUNT[FX_CHUNK];

static void drive_process(void *state, const float *const in[FX_CHANNELS],
                          float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(state, in, wet, amount, n, 0);
}

static void drive_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    drive_run(state, in, NULL, ZERO_AMOUNT, n, 0);
}

/* ------------------------------------------------------------------------- */
/* Phaser                                                                     */
/* ------------------------------------------------------------------------- */

/* Four first-order all-pass stages swept together by a slow LFO, mixed 50/50
 * with the dry signal (wet_max 0.5), so where the chain's phase reaches 180
 * degrees the two cancel: two notches sweeping up and down. The knob is the
 * mix.
 *
 * Fixed voicing: stage corner swept 300 Hz .. 3 kHz on a log scale, 0.4 Hz,
 * the right channel a quarter cycle ahead for width; feedback -0.2.
 *
 * Feedback is solved without a delay (zero-delay feedback): y = A(x + g y),
 * so y = A x / (1 - g A) exactly. At full mix the response is
 *   H = (1 + A / (1 - gA)) / 2 = (1 + (1 - g)A) / (2 (1 - gA))
 * and for -0.618 < g <= 0 its magnitude is largest where A = 1, at
 * (2 - g) / (2 (1 - g)) — 0.917 for g = -0.2. So the phaser never boosts any
 * frequency: that is its level guarantee for steady input (RMS); peaks are not
 * guaranteed, an all-pass moves phase and can raise the crest factor. Positive
 * feedback would resonate above unity — the hot-feedback mutant shows the
 * self-test catching exactly that.
 *
 * The corner -> coefficient map needs tan(); it is tabulated per rate (257
 * points over one LFO cycle) and linearly interpolated per frame.
 *
 * Per stereo frame, active: LFO wraps 4; per channel: table lerp 7, a^4 2,
 * state fold 6, feedback solve 7 (one divide), four stages x 6 (incl. flush)
 * 24 => 46; total ~96 ops. Runs while bypassed too. Laptop: ~25 ns/frame.
 */
#define PHASER_STAGES 4
#define PHASER_LO_HZ 300.0
#define PHASER_HI_HZ 3000.0
#define PHASER_RATE_HZ 0.4f
#define PHASER_STEREO_OFFSET 0.25f
#define PHASER_FEEDBACK -0.2f
#define PHASER_TABLE 256

typedef struct {
    float table[PHASER_TABLE + 1];
    float z[FX_CHANNELS][PHASER_STAGES];
    float phase, phase_inc;
} phaser_state;

enum { PHASER_NO_FLUSH = 1, PHASER_HOT = 2 };

static int phaser_init(void *state, float rate)
{
    phaser_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    for (int k = 0; k <= PHASER_TABLE; k++) {
        double shape = (double)lfo_shape((float)k / (float)PHASER_TABLE);   /* -1..1 */
        double hz = sqrt(PHASER_LO_HZ * PHASER_HI_HZ) *
                    pow(PHASER_HI_HZ / PHASER_LO_HZ, 0.5 * shape);
        double t = tan(M_PI * hz / (double)rate);
        s->table[k] = (float)((t - 1.0) / (t + 1.0));
    }
    s->phase = 0.0f;
    s->phase_inc = PHASER_RATE_HZ / rate;
    return 0;
}

static inline float phaser_coef(const phaser_state *s, float phase)
{
    float pos = phase * (float)PHASER_TABLE;
    uint32_t k = (uint32_t)pos;
    if (k >= PHASER_TABLE) {
        k = PHASER_TABLE - 1;
    }
    float frac = pos - (float)k;
    return s->table[k] + frac * (s->table[k + 1] - s->table[k]);
}

static inline void phaser_run(phaser_state *s, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], uint32_t n, int mode)
{
    const float g = (mode & PHASER_HOT) ? 0.6f : PHASER_FEEDBACK;
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {
        float x[FX_CHANNELS] = { finite_or_zero(in[0][i]), finite_or_zero(in[1][i]) };
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            float a = phaser_coef(s, ch == 0 ? phase : wrap01(phase + PHASER_STEREO_OFFSET));
            float *z = s->z[ch];
            /* Chain output = G v + S, G = a^4, S folded from the memories. */
            float a2 = a * a;
            float G = a2 * a2;
            float S = ((z[0] * a + z[1]) * a + z[2]) * a + z[3];
            float y = (G * x[ch] + S) / (1.0f - g * G);
            float v = x[ch] + g * y;
            for (int k = 0; k < PHASER_STAGES; k++) {
                float o = a * v + z[k];
                z[k] = v - a * o;
                if (!(mode & PHASER_NO_FLUSH)) {
                    z[k] = flush(z[k]);
                }
                v = o;
            }
            if (wet != NULL) {
                wet[ch][i] = v;
            }
        }
        phase = wrap01(phase + s->phase_inc);
    }
    s->phase = phase;
}

static void phaser_process(void *state, const float *const in[FX_CHANNELS],
                           float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    phaser_run(state, in, wet, n, 0);
}

static void phaser_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    phaser_run(state, in, NULL, n, 0);
}

/* ------------------------------------------------------------------------- */
/* Tremolo                                                                    */
/* ------------------------------------------------------------------------- */

/* Amplitude LFO; the right channel half a cycle behind the left, so at full
 * depth it is an auto-pan (one channel rises as the other falls) and a mono
 * sum stays level. The knob is the depth: wet is the input times a gain in
 * [0, 1], wet_max 1, so out = in (1 - d + d gain) — never louder, peak or RMS.
 * Fixed voicing: 5 Hz, the rounded-triangle shape (no trig).
 *
 * Bypass advances the LFO so it stays in time with an always-on one.
 *
 * Per stereo frame, active: LFO shape 2 x 8, gain 2 x 3, wraps 4, apply 2
 * => ~28 ops. Bypassed: the phase steps only, 2 ops. Laptop: ~5 ns/frame.
 */
#define TREMOLO_RATE_HZ 5.0f
#define TREMOLO_STEREO_OFFSET 0.5f

typedef struct {
    float phase, phase_inc;
} tremolo_state;

static int tremolo_init(void *state, float rate)
{
    tremolo_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    s->phase = 0.0f;
    s->phase_inc = TREMOLO_RATE_HZ / rate;
    return 0;
}

static inline void tremolo_run(tremolo_state *s, const float *const in[FX_CHANNELS],
                               float *const wet[FX_CHANNELS], uint32_t n, float top)
{
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {
        float l = in[0][i];
        float r = in[1][i];
        float gl = top * (0.5f + 0.5f * lfo_shape(phase));
        float gr = top * (0.5f + 0.5f * lfo_shape(wrap01(phase + TREMOLO_STEREO_OFFSET)));
        wet[0][i] = l * gl;
        wet[1][i] = r * gr;
        phase = wrap01(phase + s->phase_inc);
    }
    s->phase = phase;
}

static void tremolo_process(void *state, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(state, in, wet, n, 1.0f);
}

static void tremolo_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    (void)in;
    tremolo_state *s = state;
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {     /* the same steps as process */
        phase = wrap01(phase + s->phase_inc);
    }
    s->phase = phase;
}

/* ------------------------------------------------------------------------- */
/* The table                                                                  */
/* ------------------------------------------------------------------------- */

/* The wire's effect ids. Append only: an id is a number the policy module
 * sends, and renumbering would silently change what an old sender selects.
 * patch_browser/fx_host_protocol.py names the same ids in the same order;
 * tests/test_fx_host.py reads this table and fails if they drift. */
const fx_effect_desc FX_EFFECTS[] = {
    { "chorus", 0.5f, 1.0f, sizeof(chorus_state), chorus_init, chorus_process, chorus_feed },
    { "drive", 1.0f, 0.05f, sizeof(drive_state), drive_init, drive_process, drive_feed },
    { "phaser", 0.5f, 1.0f, sizeof(phaser_state), phaser_init, phaser_process, phaser_feed },
    { "tremolo", 1.0f, 1.0f, sizeof(tremolo_state), tremolo_init, tremolo_process, tremolo_feed },
};
const int FX_EFFECT_COUNT = (int)(sizeof(FX_EFFECTS) / sizeof(FX_EFFECTS[0]));

#ifdef FX_SELFTEST
static void drive_no_os(void *st, const float *const in[FX_CHANNELS],
                        float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(st, in, wet, amount, n, DRIVE_NO_OS);
}
static void drive_no_ceiling(void *st, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(st, in, wet, amount, n, DRIVE_NO_CEILING);
}
static void drive_no_flush(void *st, const float *const in[FX_CHANNELS],
                           float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(st, in, wet, amount, n, DRIVE_NO_FLUSH);
}
static void drive_no_flush_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    drive_run(st, in, NULL, ZERO_AMOUNT, n, DRIVE_NO_FLUSH);
}
static void phaser_no_flush(void *st, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    phaser_run(st, in, wet, n, PHASER_NO_FLUSH);
}
static void phaser_no_flush_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    phaser_run(st, in, NULL, n, PHASER_NO_FLUSH);
}
static void phaser_hot(void *st, const float *const in[FX_CHANNELS],
                       float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    phaser_run(st, in, wet, n, PHASER_HOT);
}
static void tremolo_overshoot(void *st, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(st, in, wet, n, 1.25f);
}

const fx_effect_desc FX_TEST_MUTANTS[FX_MUT_COUNT] = {
    [FX_MUT_DRIVE_NO_OVERSAMPLING] = { "drive-no-os", 1.0f, 0.05f, sizeof(drive_state),
                                       drive_init, drive_no_os, drive_feed },
    [FX_MUT_DRIVE_NO_CEILING] = { "drive-no-ceiling", 1.0f, 0.05f, sizeof(drive_state),
                                  drive_init, drive_no_ceiling, drive_feed },
    [FX_MUT_DRIVE_NO_FLUSH] = { "drive-no-flush", 1.0f, 0.05f, sizeof(drive_state),
                                drive_init, drive_no_flush, drive_no_flush_feed },
    [FX_MUT_PHASER_NO_FLUSH] = { "phaser-no-flush", 0.5f, 1.0f, sizeof(phaser_state),
                                 phaser_init, phaser_no_flush, phaser_no_flush_feed },
    [FX_MUT_PHASER_HOT_FEEDBACK] = { "phaser-hot", 0.5f, 1.0f, sizeof(phaser_state),
                                     phaser_init, phaser_hot, phaser_feed },
    [FX_MUT_TREMOLO_OVERSHOOT] = { "tremolo-overshoot", 1.0f, 1.0f, sizeof(tremolo_state),
                                   tremolo_init, tremolo_overshoot, tremolo_feed },
};

int fx_test_no_clamp;
#endif

/* ------------------------------------------------------------------------- */
/* Engine                                                                     */
/* ------------------------------------------------------------------------- */

static float one_pole_alpha(double ms, float rate)
{
    if (!(ms > 0.0) || !(rate > 0.0f)) {
        return 0.0f;
    }
    return (float)(1.0 - exp(-1.0 / ((ms / 1000.0) * (double)rate)));
}

static float xfade_step_for(double ms, float rate)
{
    double frames = (ms / 1000.0) * (double)rate;
    return frames >= 1.0 ? (float)(1.0 / frames) : 1.0f;
}

int fx_engine_set_rate(fx_engine *e, float rate)
{
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    for (int k = 0; k < e->count; k++) {
        memset(e->state[k], 0, e->table[k].state_bytes);
        if (e->table[k].init(e->state[k], rate) != 0) {
            return -1;
        }
    }
    e->rate = rate;
    e->depth_alpha = one_pole_alpha(e->depth_ms, rate);
    e->xfade_step = xfade_step_for(e->xfade_ms, rate);
    return 0;
}

int fx_engine_init(fx_engine *e, const fx_effect_desc *table, int count, float rate,
                   double depth_ms, double xfade_ms)
{
    memset(e, 0, sizeof(*e));
    if (count < 1 || count > FX_MAX_EFFECTS) {
        return -1;
    }
    e->table = table;
    e->count = count;
    e->depth_ms = depth_ms;
    e->xfade_ms = xfade_ms;
    e->active = 0;
    e->incoming = -1;
    for (int k = 0; k < count; k++) {
        e->state[k] = calloc(1, table[k].state_bytes);
        if (e->state[k] == NULL) {
            fx_engine_free(e);
            return -1;
        }
    }
    if (fx_engine_set_rate(e, rate) != 0) {
        fx_engine_free(e);
        return -1;
    }
    return 0;
}

void fx_engine_free(fx_engine *e)
{
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        free(e->state[k]);
        e->state[k] = NULL;
    }
    e->count = 0;
}

/* NaN goes to silence, not to a rail: fmaxf/fminf would turn NaN into -1.0,
 * which is full-scale DC into whatever is on the listener's head. */
static inline float clamp_out(float x)
{
#ifdef FX_SELFTEST
    if (fx_test_no_clamp) {
        return x;
    }
#endif
    if (x > 1.0f) {
        return 1.0f;
    }
    if (x < -1.0f) {
        return -1.0f;
    }
    if (x != x) {
        return 0.0f;
    }
    return x;
}

static void feed_idle(fx_engine *e, const float *const dry[FX_CHANNELS], uint32_t n,
                      int skip_a, int skip_b)
{
    for (int k = 0; k < e->count; k++) {
        if (k == skip_a || k == skip_b || e->table[k].feed == NULL) {
            continue;
        }
        e->table[k].feed(e->state[k], dry, n);
    }
}

void fx_engine_process(fx_engine *e, const float *const in[FX_CHANNELS],
                       float *const out[FX_CHANNELS], uint32_t nframes,
                       int effect, float depth)
{
    float dry_buf[FX_CHANNELS][FX_CHUNK];
    float wet_a_buf[FX_CHANNELS][FX_CHUNK];
    float wet_b_buf[FX_CHANNELS][FX_CHUNK];
    float depth_ramp[FX_CHUNK];
    const float *const dry[FX_CHANNELS] = { dry_buf[0], dry_buf[1] };
    float *const wet_a[FX_CHANNELS] = { wet_a_buf[0], wet_a_buf[1] };
    float *const wet_b[FX_CHANNELS] = { wet_b_buf[0], wet_b_buf[1] };

    float target = depth;
    if (!(target > 0.0f)) {        /* also catches NaN */
        target = 0.0f;
    } else if (target > 1.0f) {
        target = 1.0f;
    }

    for (uint32_t off = 0; off < nframes; off += FX_CHUNK) {
        uint32_t n = nframes - off < FX_CHUNK ? nframes - off : FX_CHUNK;

        /* Copied before anything is written, so `out` may alias `in`. */
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            if (in[ch] != NULL) {
                memcpy(dry_buf[ch], in[ch] + off, n * sizeof(float));
            } else {
                memset(dry_buf[ch], 0, n * sizeof(float));
            }
        }

        /* One swap at a time; a request that arrives mid-fade waits for the
         * fade in progress to land and then starts its own. */
        if (e->incoming < 0 && effect >= 0 && effect < e->count && effect != e->active) {
            e->incoming = effect;
            e->xfade_pos = 0.0f;
        }

        if (e->depth == 0.0f && target == 0.0f) {
            /* Bypass. Output is the input, bit for bit (within the clamp);
             * every effect keeps its history so re-entry is click-free. A
             * pending swap lands immediately — nothing is audible to fade. */
            if (e->incoming >= 0) {
                e->active = e->incoming;
                e->incoming = -1;
                e->xfade_pos = 0.0f;
            }
            feed_idle(e, dry, n, -1, -1);
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                if (out[ch] == NULL) {
                    continue;
                }
                for (uint32_t i = 0; i < n; i++) {
                    out[ch][off + i] = clamp_out(dry_buf[ch][i]);
                }
            }
            continue;
        }

        float d = e->depth;
        for (uint32_t i = 0; i < n; i++) {
            if (e->depth_alpha <= 0.0f || fabsf(target - d) <= DEPTH_SNAP) {
                d = target;
            } else {
                d += e->depth_alpha * (target - d);
            }
            depth_ramp[i] = d;
        }
        e->depth = d;

        const int a = e->active;
        const int b = e->incoming;
        e->table[a].process(e->state[a], dry, wet_a, depth_ramp, n);
        if (b >= 0) {
            e->table[b].process(e->state[b], dry, wet_b, depth_ramp, n);
        }
        feed_idle(e, dry, n, a, b);

        const float wa = e->table[a].wet_max;
        const float wb = b >= 0 ? e->table[b].wet_max : 0.0f;
        const float fa = 1.0f / e->table[a].mix_full_at;
        const float fb = b >= 0 ? 1.0f / e->table[b].mix_full_at : 1.0f;
        float pos = e->xfade_pos;
        for (uint32_t i = 0; i < n; i++) {
            float x = 0.0f;
            if (b >= 0) {
                pos += e->xfade_step;
                if (pos > 1.0f) {
                    pos = 1.0f;
                }
                x = pos;
            }
            /* fa = 1 (the knob is the mix) leaves d unchanged, bit for bit. */
            const float da = fminf(1.0f, depth_ramp[i] * fa);
            const float db = fminf(1.0f, depth_ramp[i] * fb);
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                float dry_s = dry_buf[ch][i];
                float mix = dry_s + da * wa * (wet_a_buf[ch][i] - dry_s);
                if (b >= 0) {
                    float mix_b = dry_s + db * wb * (wet_b_buf[ch][i] - dry_s);
                    mix += x * (mix_b - mix);
                }
                if (out[ch] != NULL) {
                    out[ch][off + i] = clamp_out(mix);
                }
            }
        }
        if (b >= 0 && pos >= 1.0f) {
            e->active = b;
            e->incoming = -1;
            pos = 0.0f;
        }
        e->xfade_pos = pos;
    }
}

/* ------------------------------------------------------------------------- */
/* Control protocol                                                           */
/* ------------------------------------------------------------------------- */

/* Deliberately not OSC, for the same reasons as the live monitor's `gain`
 * line: the sender stays a one-line sendto with no dependency, and a malformed
 * datagram from anything else on loopback must be ignorable without a parser. */
int fx_parse_control(const char *text, int *effect, float *depth)
{
    while (*text == ' ' || *text == '\t') {
        text++;
    }
    if (strncmp(text, "fx", 2) != 0) {
        return 0;
    }
    const char *rest = text + 2;
    if (*rest != ' ' && *rest != '\t') {
        return 0;
    }
    while (*rest == ' ' || *rest == '\t') {
        rest++;
    }
    if (*rest < '0' || *rest > '9') {   /* no sign, no hex, no empty */
        return 0;
    }
    char *end = NULL;
    errno = 0;
    long id = strtol(rest, &end, 10);
    if (end == rest || errno == ERANGE || id > FX_MAX_EFFECTS) {
        return 0;
    }
    if (*end != ' ' && *end != '\t') {
        return 0;
    }
    rest = end;
    errno = 0;
    float value = strtof(rest, &end);
    if (end == rest || errno == ERANGE || !isfinite(value)) {
        return 0;
    }
    while (*end == ' ' || *end == '\t' || *end == '\n' || *end == '\r') {
        end++;
    }
    if (*end != '\0') {
        return 0;
    }
    *effect = (int)id;
    *depth = value;
    return 1;
}
