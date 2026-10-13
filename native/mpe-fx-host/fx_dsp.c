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
 * Level. Half dry and half a copy 5..9 ms late measured -3.1 dB on the keys
 * signal and -3.8 dB on noise at full depth (the two halves are decorrelated,
 * so they add in power, not amplitude), and up to -9.5 dB on an 80 Hz tone,
 * which sits on the first comb notch. The engine's static level curve
 * (fx_dsp.h) gives most of the power-sum loss back: correlation 0, scale
 * CHORUS_LEVEL_SCALE. It is a fixed gain per knob position, so the chorus's
 * tone is the #149 tone, including the comb's softening of the highs: round 3
 * put a dynamic makeup and a peak limiter on this mix, and the limiter, acting
 * on every peak, measured two-tone IMD -32 dB at full (#149: -75 dB).
 *
 * Per stereo frame while active: one triangle step (add, compare, abs, fma),
 * then per channel one delay-time fma, a float->int split, a write, two reads
 * and a lerp — about 25 float ops plus 6 memory ops. No trig, no division. */
#define CHORUS_CENTER_MS 7.0f
#define CHORUS_SWEEP_MS 2.0f   /* ± around the centre: 5..9 ms */
#define CHORUS_RATE_HZ 0.6f
/* Static level curve (fx_dsp.h): a decorrelated copy, given back to within
 * CHORUS_LEVEL_SCALE of its power-sum loss. Calibrated on the keys signal. */
#define CHORUS_LEVEL_CORR 0.0f
#define CHORUS_LEVEL_SCALE 0.9f

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

/* Peak limiter gain for one frame: instant attack, `release` recovery toward
 * 1. `need` is the largest gain that keeps every channel at or under its
 * ceiling this frame (1 if none is over). Returning min(need, recovered)
 * makes the ceiling a guarantee, sample by sample, and the release keeps the
 * gain steady across a waveform instead of bending each peak (that would be a
 * waveshaper, i.e. distortion). */
static inline float limiter_gain(float h, float need, float release)
{
    float r = h + release * (1.0f - h);
    return need < r ? need : r;
}

static inline float limiter_need(float need, float y, float ceil)
{
    float a = fabsf(y);
    if (a > ceil) {         /* false for NaN: it goes on to the clamp */
        float q = ceil / a;
        need = q < need ? q : need;
    }
    return need;
}

/* ------------------------------------------------------------------------- */
/* Tube drive                                                                 */
/* ------------------------------------------------------------------------- */

/* Signal path, per channel:
 *
 *   x -> low-shelf cut -> x G -> [4x up] -> asymmetric soft clip -> [4x down]
 *     -> / G -> DC block -> presence shelf -> low-pass -> x makeup -> limiter
 *     -> wet
 *
 * Fixed voicing (round 3, after the first ear test: "at full keep having an
 * overdrive and a presence, but be a bit smoother, not as overly crunched"):
 *   - Pre: a first-order low shelf, -6 dB below ~180 Hz, so the bass does not
 *     dominate the clipping (tighter, less mud) — the overdrive-pedal habit.
 *   - Drive G = 0.5 (1 + 15 a^2) for knob a: 0.5 (clean) .. 8 (+24 dB over
 *     that; +18 dB over unity). Was .. 16: the top is 6 dB lower.
 *   - Shaper: f(u) = a(u + 0.2) - a(0.2), a(v) = v / sqrt(1 + v^2). The
 *     algebraic sigmoid has no corner anywhere (the old cubic went flat at
 *     |v| = 1 with a jump in curvature, which is what turns a hard-driven
 *     wave square and its harmonics slow to fall off): the knee is soft, and
 *     h3, h5, h7 fall about twice as fast in dB. The bias is 0.2, the #149
 *     value (round 3 tried 0.1; round 4, after the second ear test, "maybe a
 *     little too smooth", put it back): the asymmetry that gives the even
 *     harmonics. |f(u)| <= |u|, since a' <= 1; |f| < 1 + a(0.2) = sat.
 *   - Post: a DC blocker at 20 Hz (the asymmetry makes DC), then presence: a
 *     first-order high shelf, +4 dB above ~1.8 kHz, into a one-pole low-pass
 *     at 6.5 kHz. Net, about +2 dB across 2-5 kHz, level at 8 kHz, -2 dB at
 *     12 kHz: the bite stays, the fizz goes.
 *
 * Oversampling: two cascaded 2x half-band FIR stages (polyphase, so the zero
 * taps cost nothing), steep at the base rate (55 taps, pass 0-20 kHz, stop
 * from 28 kHz at 48 kHz in, ~71 dB) and short at 2x (19 taps, ~69 dB). The
 * same filters decimate on the way down. Linear phase; the wet path is about
 * 0.6 ms late, which is why the mix fades in rather than switching. With the
 * smoother shaper and the lower top, a 10 kHz sine at full drive aliases at
 * -52 dB (was -26 dB): the old cubic's harmonics 17-21 folded at the 4x rate
 * itself; the sigmoid's are too small to matter. No higher factor needed.
 *
 * Level. Dividing by G puts the shaper back at unity small-signal gain, but a
 * clipped signal comes out much quieter than it went in, and the tone filters
 * take some off a signal that does not clip. Automatic makeup:
 *   g = min( sqrt(ms_in / ms_out),  max(2.0, 2 * peak_in * G / sat) )
 * The first term is RMS matching (both mean squares over 25 ms, both channels
 * linked so the image does not move). The second is the ceiling that keeps a
 * stale makeup from pumping a quiet tail: it follows the input's peak over
 * the last ~24 ms (peak_in) and the drive (the factor 2 gives back the
 * pre-shelf's 6 dB), with a floor of +6 dB for the tone filters' loss.
 * Without it, the tail after a loud note is boosted by the stale makeup for
 * ~100 ms — the self-test's step check shows that mutant failing. g is then
 * smoothed over 2 ms.
 *
 * Limiter. Instant attack, 10 ms release, ceiling peak_in (never below
 * -120 dBFS): whatever the makeup does, no output sample exceeds the input's
 * peak over the last ~24 ms. That is now a guarantee, not an estimate (the
 * old drive measured up to +1 dB over its input's peak).
 *
 * Bypass runs the whole chain with the output discarded, so every filter
 * memory is current on re-entry.
 *
 * Per stereo frame, active (each add, multiply, compare, abs, divide and
 * sqrt counted as one op; loads and stores not counted):
 *   per channel: shelf and drive 6, up A 43 (14 symmetric pairs), up B 2 x 16,
 *   shaper 4 x 8 (one sqrt, one divide), down B 2 x 17, down A 44,
 *   post (/G, DC, presence, LP, flush) 20, detector/peak/makeup inputs 5,
 *   makeup and limiter 4                                             ~= 220
 *   shared: drive law 5 (one divide), detectors 10, peak window 7,
 *   makeup law 13 (one divide, one sqrt), limiter gain 4              ~=  39
 *   => ~480 ops per stereo frame (was ~460), ~75% of it the four half-band
 *   filters. It runs while bypassed too (see above). Laptop: the same
 *   ~120-135 ns/frame as before (fx-selftest --bench, interleaved with the
 *   old build); the Pi figure is the separate measurement.
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
#define DRIVE_F_BIAS 0.1961161351f   /* a(0.2) = 0.2 / sqrt(1.04) */
#define DRIVE_SAT (1.0f + DRIVE_F_BIAS)
#define DRIVE_MAX 8.0f               /* G at the top of the knob */
#define DRIVE_PRES_DB 4.0
#define DRIVE_PRES_HZ 1800.0f
#define DRIVE_LP_HZ 6500.0f
#define DRIVE_SHELF_HZ 180.0f
#define DRIVE_SHELF_BACK 2.0f      /* the -6 dB shelf, given back to the ceiling */
#define DRIVE_DC_HZ 20.0f
#define DRIVE_DETECT_MS 25.0f
#define DRIVE_MAKEUP_MS 2.0f
#define DRIVE_PEAK_BLOCK_MS 4.0f
#define DRIVE_PEAK_BLOCKS 6        /* window: the current block + 5 before */
#define DRIVE_EPS 1e-10f           /* mean-square floor: silence -> ratio 1 */
/* Floor of the makeup ceiling: the tone filters take level off a signal too
 * quiet to clip, and the peak ceiling alone would hold the makeup at 1 there.
 * +6 dB. The limiter below keeps what this lifts under the input's peak. */
#define DRIVE_LIN_MAKEUP 1.9953f
#define DRIVE_RELEASE_MS 10.0f
/* The limiter's ceiling never goes below -120 dBFS. Without the floor, input
 * silence makes the ceiling 0 and the limiter zeroes whatever the filters
 * still hold, which hides a memory decaying through the subnormals instead of
 * being flushed (the long-run check must still see that). */
#define DRIVE_LIMIT_FLOOR 1e-6f

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
    v = v > 1.0e4f ? 1.0e4f : (v < -1.0e4f ? -1.0e4f : v);   /* keeps v*v finite */
    return v / sqrtf(1.0f + v * v) - DRIVE_F_BIAS;
}

#ifdef FX_SELFTEST
/* The round-3 bias (0.1), kept only so the self-test can show its even-
 * harmonic check failing it. a(0.1) = 0.0995037190. */
static inline float drive_shape_round3(float u)
{
    float v = u + 0.1f;
    v = v > 1.0e4f ? 1.0e4f : (v < -1.0e4f ? -1.0e4f : v);
    return v / sqrtf(1.0f + v * v) - 0.0995037190f;
}

/* The round-2 shaper, kept only so the self-test can show its smoothness
 * check failing it: (s(u + 0.2) - s(0.2)) / 1.5, s(v) = 1.5v - 0.5v^3 on
 * [-1, 1] and flat outside. */
static inline float drive_shape_old(float u)
{
    float v = u + 0.2f;
    v = v > 1.0f ? 1.0f : (v < -1.0f ? -1.0f : v);
    return (v * (1.5f - 0.5f * v * v) - 0.296f) * (1.0f / 1.5f);
}
#endif

typedef struct {
    hb_hist up_a, up_b, dn_b_even, dn_b_odd, dn_a_even, dn_a_odd;
    float shelf;        /* low-pass memory for the shelf */
    float dc_x, dc_y;   /* DC blocker */
    float pres;         /* low-pass memory for the presence shelf */
    float lp;           /* post low-pass */
} drive_chan;

typedef struct {
    drive_chan ch[FX_CHANNELS];
    float a_shelf, a_pres, k_pres, a_lp, dc_p, dc_k, a_detect, a_makeup;
    float ms_in, ms_out, makeup, a_release, limit;
    float peak_blocks[DRIVE_PEAK_BLOCKS - 1];
    float peak_now;
    uint32_t block_len, block_pos;
} drive_state;

enum {
    DRIVE_NO_OS = 1, DRIVE_NO_CEILING = 2, DRIVE_NO_FLUSH = 4, DRIVE_NO_MAKEUP = 8,
    DRIVE_OLD_SHAPER = 16, DRIVE_ROUND3_BIAS = 32
};

static int drive_init(void *state, float rate)
{
    drive_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    s->a_shelf = pole_alpha(DRIVE_SHELF_HZ, rate);
    s->a_pres = pole_alpha(DRIVE_PRES_HZ, rate);
    s->k_pres = (float)(pow(10.0, DRIVE_PRES_DB / 20.0) - 1.0);

    s->a_lp = pole_alpha(DRIVE_LP_HZ, rate);
    s->dc_p = (float)exp(-2.0 * M_PI * (double)DRIVE_DC_HZ / (double)rate);
    s->dc_k = 0.5f * (1.0f + s->dc_p);
    s->a_detect = (float)(1.0 - exp(-1.0 / (DRIVE_DETECT_MS * 0.001 * (double)rate)));
    s->a_makeup = (float)(1.0 - exp(-1.0 / (DRIVE_MAKEUP_MS * 0.001 * (double)rate)));
    s->makeup = 1.0f;
    s->a_release = (float)(1.0 - exp(-1.0 / (DRIVE_RELEASE_MS * 0.001 * (double)rate)));
    s->limit = 1.0f;
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
#ifdef FX_SELFTEST
        if (mode & DRIVE_OLD_SHAPER) {
            b[j] = drive_shape_old(b[j]);
            continue;
        }
        if (mode & DRIVE_ROUND3_BIAS) {
            b[j] = drive_shape_round3(b[j]);
            continue;
        }
#endif
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
        const float drive = 0.5f * (1.0f + (2.0f * DRIVE_MAX - 1.0f) * a * a);   /* 0.5 .. 8 */
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
            c->pres += s->a_pres * (dc - c->pres);
            float bright = dc + s->k_pres * (dc - c->pres);
            c->lp += s->a_lp * (bright - c->lp);
            if (!(mode & DRIVE_NO_FLUSH)) {
                c->shelf = flush(c->shelf);
                c->dc_y = flush(c->dc_y);
                c->pres = flush(c->pres);
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
            float ceiling = DRIVE_SHELF_BACK * peak * drive * (1.0f / DRIVE_SAT);
            ceiling = ceiling > DRIVE_LIN_MAKEUP ? ceiling : DRIVE_LIN_MAKEUP;
            target = target < ceiling ? target : ceiling;
        }
        if (mode & DRIVE_NO_MAKEUP) {
            target = 1.0f;
        }
        s->makeup += s->a_makeup * (target - s->makeup);

        float need = 1.0f;
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            y[ch] *= s->makeup;
            need = limiter_need(need, y[ch], peak > DRIVE_LIMIT_FLOOR ? peak : DRIVE_LIMIT_FLOOR);
        }
        if (mode & DRIVE_NO_CEILING) {
            need = 1.0f;
        }
        s->limit = limiter_gain(s->limit, need, s->a_release);
        if (wet != NULL) {
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                wet[ch][i] = s->limit * y[ch];
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

/* Eight first-order all-pass stages swept together by a slow LFO, with
 * band-limited feedback, mixed with the dry signal (wet_max 0.6). Where the
 * chain's phase reaches 180 degrees the two cancel: four notches sweeping up
 * and down, with resonant peaks between them. The knob is the mix.
 *
 * Fixed voicing (round 5: "too subtle"; round 4 had four stages, 300 Hz ..
 * 3 kHz and feedback -0.2, which made two notches only about 17 dB deep):
 *   - Stage corner swept 100 Hz .. 6 kHz on a log scale (5.9 octaves, was
 *     3.3), 0.4 Hz, the right channel a quarter cycle ahead for width.
 *   - Feedback +0.6, taken from the chain's output through a band-pass (one-
 *     pole high-pass at 150 Hz, one-pole low-pass at 3 kHz) and fed back one
 *     sample later: y = A(x + g B(z^-1 y)). The resonance sits in the band
 *     where the notches are. Full-band feedback, the textbook form, also
 *     resonates at DC and at Nyquist, where an even all-pass chain is always
 *     in phase: that is a +4 dB shelf on everything above ~10 kHz, which made
 *     white noise 3 dB louder than the keys signal, so a static level gain
 *     that kept noise under its input left the keys 3-4 dB down. The
 *     full-band mutant shows the self-test catching it.
 *   - Mix 0.6 wet, not 0.5: with feedback g the in-band wet at a notch is
 *     -1/(1+g) of the dry, and w = (1+g)/(2+g) = 0.62 puts the cancellation
 *     back toward a null (measured with the LFO frozen: the deepest notch
 *     35-68 dB down across the sweep, three to four notches at once; round
 *     4's were 17 dB, one or two).
 *
 * Stability: the loop gain is |g B A| <= g < 1 at every frequency (A is an
 * all-pass, |B| <= 1), so the loop cannot run away; the self-test runs it two
 * minutes at full depth and checks it decays to exact silence, against a
 * mutant with g = 1.1 that must grow.
 *
 * Level. The resonant peaks lift what they cross by up to about +5 dB (a
 * tone the sweep passes over swells: a phaser's sound) and the notches take
 * energy out. Over whole sweeps the keys signal loses 1.55 dB at half mix and
 * 0.88 dB at full, white noise 1.1-1.2 dB. The engine's static level curve
 * (fx_dsp.h) gives part of it back: correlation PHASER_LEVEL_CORR, scale
 * PHASER_LEVEL_SCALE. Alone, the phaser could come to within 0.93 dB of its
 * input on keys at every knob position (scale 0.79), but in series with the
 * flanger the two curves stack and phaser + flanger at full went +0.28 dB
 * over the input on keys. The scale is held at 0.50, where that pair is
 * +0.02 dB and the phaser alone is -1.17 dB at worst (55 %) and -0.41 dB at
 * full on keys. It is a fixed gain per knob position: no detector, no limiter
 * (round 3's dynamic makeup under a limiter measured two-tone IMD -33 dB at
 * full, the "distortion that comes and goes with the sweep"). Peaks are not
 * guaranteed: an all-pass moves phase and raises the crest factor, and the
 * resonance adds to that (+6.0 dB over the input's peak at full on noise and
 * tones; keys -0.3 dB). The engine's clamp holds ±1.
 *
 * The corner -> coefficient map needs tan(); it is tabulated per rate (257
 * points over one LFO cycle) and linearly interpolated per frame.
 *
 * Per stereo frame, active: LFO wraps 4; per channel: table lerp 7, feedback
 * in 2, eight stages x 6 (incl. flush) 48, feedback band-pass 7 and its
 * flushes 4 => 68; total ~140 ops (was ~96). Runs while bypassed too.
 * Laptop: ~37-40 ns/frame (was ~31).
 */
#define PHASER_STAGES 8
#define PHASER_LO_HZ 100.0
#define PHASER_HI_HZ 6000.0
#define PHASER_RATE_HZ 0.4f
#define PHASER_STEREO_OFFSET 0.25f
#define PHASER_FEEDBACK 0.6f
#define PHASER_FB_HP_HZ 150.0f
#define PHASER_FB_LP_HZ 3000.0f
#define PHASER_WET 0.6f
#define PHASER_TABLE 256
#define PHASER_LEVEL_CORR 0.60f
#define PHASER_LEVEL_SCALE 0.50f

typedef struct {
    float table[PHASER_TABLE + 1];
    float z[FX_CHANNELS][PHASER_STAGES];
    float hp[FX_CHANNELS], lp[FX_CHANNELS];   /* feedback band-pass memories */
    float a_hp, a_lp;
    float phase, phase_inc;
} phaser_state;

enum { PHASER_NO_FLUSH = 1, PHASER_HOT = 2, PHASER_FULL_BAND = 4, PHASER_RUNAWAY = 8 };

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
    s->a_hp = pole_alpha(PHASER_FB_HP_HZ, rate);
    s->a_lp = pole_alpha(PHASER_FB_LP_HZ, rate);
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
    const float g = (mode & PHASER_RUNAWAY) ? 1.1f : (mode & PHASER_HOT) ? 0.95f : PHASER_FEEDBACK;
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {
        float x[FX_CHANNELS] = { finite_or_zero(in[0][i]), finite_or_zero(in[1][i]) };
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            float a = phaser_coef(s, ch == 0 ? phase : wrap01(phase + PHASER_STEREO_OFFSET));
            float *z = s->z[ch];
            /* The band-passed output of the last frame, fed back. */
            float v = x[ch] + g * s->lp[ch];
            for (int k = 0; k < PHASER_STAGES; k++) {
                float o = a * v + z[k];
                z[k] = v - a * o;
                if (!(mode & PHASER_NO_FLUSH)) {
                    z[k] = flush(z[k]);
                }
                v = o;
            }
            if (mode & PHASER_FULL_BAND) {
                s->lp[ch] = v;
            } else {
                s->hp[ch] += s->a_hp * (v - s->hp[ch]);
                s->lp[ch] += s->a_lp * ((v - s->hp[ch]) - s->lp[ch]);
            }
            if (!(mode & PHASER_NO_FLUSH)) {
                s->hp[ch] = flush(s->hp[ch]);
                s->lp[ch] = flush(s->lp[ch]);
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
/* Flanger                                                                    */
/* ------------------------------------------------------------------------- */

/* A short swept delay with feedback, mixed with the dry signal (wet_max 0.6):
 * a comb filter whose teeth sweep, the jet sound. The knob is the mix.
 *
 * Signal path, per channel:
 *
 *   s[n] = x[n] + g B(s[n - D])        the delay line's input
 *   wet  = s[n - D]
 *
 * so wet/x = z^-D / (1 - g B z^-D). Fixed voicing:
 *   - D swept 0.5 .. 8 ms (linearly, the rounded-triangle LFO), 0.2 Hz, the
 *     right channel a quarter cycle ahead: the combs move apart across the
 *     image instead of the whole sound flanging as one.
 *   - Feedback +0.6 through a band-pass (one-pole high-pass at 200 Hz, one-
 *     pole low-pass at 6 kHz). The high-pass keeps the bass out of the loop:
 *     with full-band feedback every short delay is in phase at low
 *     frequencies, so the bass is boosted by up to 1/(1-g) = +8 dB whenever
 *     the sweep is near its short end. The low-pass darkens each pass the way
 *     a tape or bucket-brigade flanger's does, so the high resonances do not
 *     fizz.
 *   - Mix 0.6 wet: (1+g)/(2+g) = 0.62 makes the in-band notches nulls.
 *
 * Stability: loop gain |g B| <= g < 1 (the linear interpolation's gain is at
 * most 1 too), so it cannot run away; the self-test runs two minutes at full
 * depth and checks it decays to exact silence, against a mutant with g = 1.1
 * that must grow.
 *
 * Level. Over a whole sweep the keys signal loses 2.6 dB at 75 % mix and
 * 2.1 dB at full, white noise 3.5 dB at full (the combs' notches take more
 * than their peaks give). The static level curve (fx_dsp.h),
 * FLANGER_LEVEL_CORR and FLANGER_LEVEL_SCALE, calibrated on keys, gives most
 * of it back: keys within 0.92 dB of the input at every knob position (worst
 * at 65 %, -0.41 dB at full), and no test signal over its input (the 3 kHz
 * tone is the one at the limit, +0.02 dB at full). Peaks are not guaranteed:
 * a resonant peak sweeping over a tone swells it, +6.0 dB over the input's
 * peak at full on noise and tones (keys +0.5 dB). The engine's clamp holds
 * ±1, so a loud pure tone at full depth is clipped there.
 *
 * The delay line is written in bypass too (feed runs the whole loop with the
 * output discarded), so coming back in reads real audio, and re-entry matches
 * an instance that never stopped.
 *
 * Per stereo frame, active: phase step 3; per channel: LFO shape 8 (and a
 * wrap 2 for the right), delay time 2, float->int split 2, two reads and a
 * lerp 3, band-pass 7, feedback 2, flushes 6, finite check 2 => ~34; total
 * ~70 ops plus 6 memory ops. No trig, no division. Runs while bypassed too.
 * Laptop: ~21-24 ns/frame.
 */
#define FLANGER_MIN_MS 0.5f
#define FLANGER_MAX_MS 8.0f
#define FLANGER_RATE_HZ 0.2f
#define FLANGER_STEREO_OFFSET 0.25f
#define FLANGER_FEEDBACK 0.6f
#define FLANGER_FB_HP_HZ 200.0f
#define FLANGER_FB_LP_HZ 6000.0f
#define FLANGER_WET 0.6f
#define FLANGER_LEVEL_CORR 0.46f
#define FLANGER_LEVEL_SCALE 1.285f

/* Power of two >= FLANGER_MAX_MS at FX_MAX_RATE plus two taps of headroom:
 * 8 ms x 192 kHz = 1536 samples. */
#define FLANGER_BUF 2048u
#define FLANGER_MASK (FLANGER_BUF - 1u)

typedef struct {
    float buf[FX_CHANNELS][FLANGER_BUF];
    float hp[FX_CHANNELS], lp[FX_CHANNELS];   /* feedback band-pass memories */
    float a_hp, a_lp;
    uint32_t w;
    float phase, phase_inc;
    float center, sweep;   /* samples */
} flanger_state;

enum { FLANGER_NO_FLUSH = 1, FLANGER_HOT = 2, FLANGER_RUNAWAY = 4 };

static int flanger_init(void *state, float rate)
{
    flanger_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    s->w = 0;
    s->phase = 0.0f;
    s->phase_inc = FLANGER_RATE_HZ / rate;
    s->center = 0.5f * (FLANGER_MIN_MS + FLANGER_MAX_MS) * 0.001f * rate;
    s->sweep = 0.5f * (FLANGER_MAX_MS - FLANGER_MIN_MS) * 0.001f * rate;
    s->a_hp = pole_alpha(FLANGER_FB_HP_HZ, rate);
    s->a_lp = pole_alpha(FLANGER_FB_LP_HZ, rate);
    return 0;
}

/* s[n - delay], delay >= 1 sample, read before s[n] is written. */
static inline float flanger_tap(const float *buf, uint32_t w, float delay)
{
    uint32_t whole = (uint32_t)delay;
    float frac = delay - (float)whole;
    float a = buf[(w - whole) & FLANGER_MASK];
    float b = buf[(w - whole - 1u) & FLANGER_MASK];
    return a + frac * (b - a);
}

static inline void flanger_run(flanger_state *s, const float *const in[FX_CHANNELS],
                               float *const wet[FX_CHANNELS], uint32_t n, int mode)
{
    const float g = (mode & FLANGER_RUNAWAY) ? 1.1f : (mode & FLANGER_HOT) ? 0.95f
                                                                            : FLANGER_FEEDBACK;
    uint32_t w = s->w;
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {
        /* Read the inputs before any write: `wet` may share memory with them. */
        float x[FX_CHANNELS] = { finite_or_zero(in[0][i]), finite_or_zero(in[1][i]) };
        float tap[FX_CHANNELS];
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            float shape = lfo_shape(ch == 0 ? phase : wrap01(phase + FLANGER_STEREO_OFFSET));
            tap[ch] = flanger_tap(s->buf[ch], w, s->center + s->sweep * shape);
            s->hp[ch] += s->a_hp * (tap[ch] - s->hp[ch]);
            s->lp[ch] += s->a_lp * ((tap[ch] - s->hp[ch]) - s->lp[ch]);
            float v = x[ch] + g * s->lp[ch];
            if (!(mode & FLANGER_NO_FLUSH)) {
                s->hp[ch] = flush(s->hp[ch]);
                s->lp[ch] = flush(s->lp[ch]);
                v = flush(v);
            }
            s->buf[ch][w] = v;
        }
        if (wet != NULL) {
            wet[0][i] = tap[0];
            wet[1][i] = tap[1];
        }
        phase = wrap01(phase + s->phase_inc);
        w = (w + 1u) & FLANGER_MASK;
    }
    s->w = w;
    s->phase = phase;
}

static void flanger_process(void *state, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    flanger_run(state, in, wet, n, 0);
}

static void flanger_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    flanger_run(state, in, NULL, n, 0);
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
 * Level. It has no level stage, on purpose. Its level falls by construction:
 * the mean-square gain is (1 - d/2)^2 + (d/2)^2 E[s^2], E[s^2] = 0.486 for
 * this LFO, so -1.1, -2.3, -3.4 and -4.3 dB at 25, 50, 75 and 100 %. Lifting
 * that back means gains above 1 on the LFO's crests, i.e. peaks up to +4.3 dB
 * over the input, and its peak guarantee is the one it has. The self-test
 * holds it to that computed level instead of to -1 dB.
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
    { "chorus", 0.5f, 1.0f, CHORUS_LEVEL_CORR, CHORUS_LEVEL_SCALE, sizeof(chorus_state),
      chorus_init, chorus_process, chorus_feed },
    { "drive", 1.0f, 0.05f, 0.0f, 0.0f, sizeof(drive_state), drive_init, drive_process,
      drive_feed },
    { "phaser", PHASER_WET, 1.0f, PHASER_LEVEL_CORR, PHASER_LEVEL_SCALE, sizeof(phaser_state),
      phaser_init, phaser_process, phaser_feed },
    { "tremolo", 1.0f, 1.0f, 0.0f, 0.0f, sizeof(tremolo_state), tremolo_init, tremolo_process,
      tremolo_feed },
    { "flanger", FLANGER_WET, 1.0f, FLANGER_LEVEL_CORR, FLANGER_LEVEL_SCALE,
      sizeof(flanger_state), flanger_init, flanger_process, flanger_feed },
};
const int FX_EFFECT_COUNT = (int)(sizeof(FX_EFFECTS) / sizeof(FX_EFFECTS[0]));

/* The run order, as ids: Drive -> Phaser -> Flanger -> Chorus -> Tremolo. The
 * drive first, so it shapes the plain signal rather than a swept or doubled
 * one; the two sweeps next to each other, ahead of the chorus's doubling; the
 * tremolo last, so its level wobble is not then shaped by anything.
 * patch_browser/fx_host_protocol.py's CHAIN_ORDER names the same ids;
 * tests/test_fx_host.py reads this array and fails if they drift. */
const int FX_CHAIN_ORDER[] = { 1, 2, 4, 0, 3 };
const int FX_CHAIN_LEN = (int)(sizeof(FX_CHAIN_ORDER) / sizeof(FX_CHAIN_ORDER[0]));

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
static void drive_no_makeup(void *st, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(st, in, wet, amount, n, DRIVE_NO_MAKEUP);
}
static void drive_old_shaper(void *st, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(st, in, wet, amount, n, DRIVE_OLD_SHAPER);
}
static void drive_round3_bias(void *st, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    drive_run(st, in, wet, amount, n, DRIVE_ROUND3_BIAS);
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
static void phaser_full_band(void *st, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    phaser_run(st, in, wet, n, PHASER_FULL_BAND);
}
static void phaser_runaway(void *st, const float *const in[FX_CHANNELS],
                           float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    phaser_run(st, in, wet, n, PHASER_RUNAWAY);
}
static void phaser_runaway_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    phaser_run(st, in, NULL, n, PHASER_RUNAWAY);
}
static void flanger_no_flush(void *st, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    flanger_run(st, in, wet, n, FLANGER_NO_FLUSH);
}
static void flanger_no_flush_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    flanger_run(st, in, NULL, n, FLANGER_NO_FLUSH);
}
static void flanger_hot(void *st, const float *const in[FX_CHANNELS],
                        float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    flanger_run(st, in, wet, n, FLANGER_HOT);
}
static void flanger_runaway(void *st, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    flanger_run(st, in, wet, n, FLANGER_RUNAWAY);
}
static void flanger_runaway_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    flanger_run(st, in, NULL, n, FLANGER_RUNAWAY);
}
static void tremolo_overshoot(void *st, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(st, in, wet, n, 1.25f);
}

static void tremolo_shallow_top(void *st, const float *const in[FX_CHANNELS],
                                float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(st, in, wet, n, 0.8f);
}

const fx_effect_desc FX_TEST_MUTANTS[FX_MUT_COUNT] = {
    [FX_MUT_DRIVE_NO_OVERSAMPLING] = { "drive-no-os", 1.0f, 0.05f, 0.0f, 0.0f, sizeof(drive_state),
                                       drive_init, drive_no_os, drive_feed },
    [FX_MUT_DRIVE_NO_CEILING] = { "drive-no-ceiling", 1.0f, 0.05f, 0.0f, 0.0f, sizeof(drive_state),
                                  drive_init, drive_no_ceiling, drive_feed },
    [FX_MUT_DRIVE_NO_MAKEUP] = { "drive-no-makeup", 1.0f, 0.05f, 0.0f, 0.0f, sizeof(drive_state),
                                 drive_init, drive_no_makeup, drive_feed },
    [FX_MUT_DRIVE_OLD_SHAPER] = { "drive-old-shaper", 1.0f, 0.05f, 0.0f, 0.0f, sizeof(drive_state),
                                  drive_init, drive_old_shaper, drive_feed },
    [FX_MUT_DRIVE_ROUND3_BIAS] = { "drive-round3-bias", 1.0f, 0.05f, 0.0f, 0.0f,
                                   sizeof(drive_state), drive_init, drive_round3_bias,
                                   drive_feed },
    [FX_MUT_DRIVE_NO_FLUSH] = { "drive-no-flush", 1.0f, 0.05f, 0.0f, 0.0f, sizeof(drive_state),
                                drive_init, drive_no_flush, drive_no_flush_feed },
    [FX_MUT_PHASER_NO_FLUSH] = { "phaser-no-flush", PHASER_WET, 1.0f, PHASER_LEVEL_CORR,
                                 PHASER_LEVEL_SCALE, sizeof(phaser_state),
                                 phaser_init, phaser_no_flush, phaser_no_flush_feed },
    [FX_MUT_PHASER_HOT_FEEDBACK] = { "phaser-hot", PHASER_WET, 1.0f, PHASER_LEVEL_CORR,
                                     PHASER_LEVEL_SCALE, sizeof(phaser_state),
                                     phaser_init, phaser_hot, phaser_feed },
    [FX_MUT_PHASER_FULL_BAND] = { "phaser-full-band", PHASER_WET, 1.0f, PHASER_LEVEL_CORR,
                                  PHASER_LEVEL_SCALE, sizeof(phaser_state),
                                  phaser_init, phaser_full_band, phaser_feed },
    [FX_MUT_PHASER_RUNAWAY] = { "phaser-runaway", PHASER_WET, 1.0f, PHASER_LEVEL_CORR,
                                PHASER_LEVEL_SCALE, sizeof(phaser_state),
                                phaser_init, phaser_runaway, phaser_runaway_feed },
    [FX_MUT_FLANGER_NO_FLUSH] = { "flanger-no-flush", FLANGER_WET, 1.0f, FLANGER_LEVEL_CORR,
                                  FLANGER_LEVEL_SCALE, sizeof(flanger_state),
                                  flanger_init, flanger_no_flush, flanger_no_flush_feed },
    [FX_MUT_FLANGER_HOT_FEEDBACK] = { "flanger-hot", FLANGER_WET, 1.0f, FLANGER_LEVEL_CORR,
                                      FLANGER_LEVEL_SCALE, sizeof(flanger_state),
                                      flanger_init, flanger_hot, flanger_feed },
    [FX_MUT_FLANGER_RUNAWAY] = { "flanger-runaway", FLANGER_WET, 1.0f, FLANGER_LEVEL_CORR,
                                 FLANGER_LEVEL_SCALE, sizeof(flanger_state),
                                 flanger_init, flanger_runaway, flanger_runaway_feed },
    [FX_MUT_TREMOLO_OVERSHOOT] = { "tremolo-overshoot", 1.0f, 1.0f, 0.0f, 0.0f, sizeof(tremolo_state),
                                   tremolo_init, tremolo_overshoot, tremolo_feed },
    [FX_MUT_TREMOLO_SHALLOW_TOP] = { "tremolo-shallow-top", 1.0f, 1.0f, 0.0f, 0.0f,
                                     sizeof(tremolo_state), tremolo_init, tremolo_shallow_top,
                                     tremolo_feed },
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

#ifdef FX_SELFTEST
int fx_test_no_level;
int fx_test_level_hot;
int fx_test_level_wobble;
int fx_test_reverse_order;
static uint32_t g_wobble_t;
#endif

/* The static level curve (fx_dsp.h, "Level curve"), tabulated over depth.
 * Startup and rate change only: it uses pow. */
static void level_table(fx_engine *e, int k)
{
    const fx_effect_desc *d = &e->table[k];
    for (int j = 0; j <= FX_LEVEL_POINTS; j++) {
        double depth = (double)j / FX_LEVEL_POINTS;
        double w = (double)d->wet_max * fmin(1.0, depth / (double)d->mix_full_at);
        double ms = 1.0 - 2.0 * w * (1.0 - w) * (1.0 - (double)d->level_corr);
        double g = d->level_scale > 0.0f && ms > 0.0 ? pow(ms, -0.5 * (double)d->level_scale)
                                                      : 1.0;
        e->level[k][j] = j == 0 ? 1.0f : (float)g;   /* exactly 1 at depth 0 */
    }
}

/* g_k(d), linearly interpolated: d is the smoothed depth, so the gain moves
 * only as fast as the knob's smoothing lets it. */
static inline float level_at(const fx_engine *e, int k, float d)
{
    float pos = d * (float)FX_LEVEL_POINTS;
    int j = (int)pos;
    if (j >= FX_LEVEL_POINTS) {
        j = FX_LEVEL_POINTS - 1;
    }
    float frac = pos - (float)j;
    float g = e->level[k][j] + frac * (e->level[k][j + 1] - e->level[k][j]);
#ifdef FX_SELFTEST
    if (fx_test_no_level) {
        g = 1.0f;
    }
    if (fx_test_level_hot && d > 0.0f) {
        g *= 1.0593f;
    }
    if (fx_test_level_wobble && e->table[k].level_scale > 0.0f) {
        /* +-0.5 dB at ~0.4 Hz: what a gain that follows the sweep does. */
        float ph = (float)(g_wobble_t % 120000u) / 120000.0f;
        g *= 1.0f + 0.06f * (4.0f * fabsf(ph - 0.5f) - 1.0f);
    }
#endif
    return g;
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
    /* The run order: FX_CHAIN_ORDER's ids that this table has, then any it
     * does not name, in id order. */
    int n = 0;
    int placed[FX_MAX_EFFECTS] = { 0 };
    for (int i = 0; i < FX_CHAIN_LEN; i++) {
        int k = FX_CHAIN_ORDER[i];
        if (k >= 0 && k < count && !placed[k]) {
            e->order[n++] = k;
            placed[k] = 1;
        }
    }
    for (int k = 0; k < count; k++) {
        if (!placed[k]) {
            e->order[n++] = k;
        }
    }
    for (int k = 0; k < count; k++) {
        level_table(e, k);
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

static inline void feed_one(fx_engine *e, int k, const float *const x[FX_CHANNELS], uint32_t n)
{
    if (e->table[k].feed != NULL) {
        e->table[k].feed(e->state[k], x, n);
    }
}

static inline float unit_clamp(float v)
{
    if (!(v > 0.0f)) {        /* also catches NaN */
        return 0.0f;
    }
    return v > 1.0f ? 1.0f : v;
}

/* One stage's depth over `n` frames: its smoothed d_k stepped toward `target`
 * into `ramp`. Land on the target once close, or once a step no longer moves
 * d: near 1.0 a float cannot hold a step of alpha * 1e-5, and the ramp used to
 * stop ~5e-5 short of a full knob for good. */
static inline void depth_ramp_for(fx_engine *e, int k, float target, float *ramp, uint32_t n)
{
    float d = e->depth[k];
    if (d == target) {
        for (uint32_t i = 0; i < n; i++) {
            ramp[i] = d;
        }
        return;
    }
    for (uint32_t i = 0; i < n; i++) {
        const float next = d + e->depth_alpha * (target - d);
        if (e->depth_alpha <= 0.0f || fabsf(target - d) <= DEPTH_SNAP || next == d) {
            d = target;
        } else {
            d = next;
        }
        ramp[i] = d;
    }
    e->depth[k] = d;
}

void fx_engine_process(fx_engine *e, const float *const in[FX_CHANNELS],
                       float *const out[FX_CHANNELS], uint32_t nframes,
                       uint32_t mask, float depth)
{
    fx_engine_process_levels(e, in, out, nframes, mask, depth, NULL);
}

void fx_engine_process_levels(fx_engine *e, const float *const in[FX_CHANNELS],
                              float *const out[FX_CHANNELS], uint32_t nframes,
                              uint32_t mask, float depth, const float *levels)
{
    /* The signal as it passes down the chain, and one stage's wet. */
    float x_buf[FX_CHANNELS][FX_CHUNK];
    float wet_buf[FX_CHANNELS][FX_CHUNK];
    float depth_ramp[FX_CHUNK];
    float on_ramp[FX_CHUNK];
    const float *const x_in[FX_CHANNELS] = { x_buf[0], x_buf[1] };
    float *const wet[FX_CHANNELS] = { wet_buf[0], wet_buf[1] };

    /* Each effect's target depth: the shared depth times its level. A level
     * of 1.0 leaves the shared depth as it is, bit for bit. */
    const float shared = unit_clamp(depth);
    float target[FX_MAX_EFFECTS];
    for (int k = 0; k < e->count; k++) {
        target[k] = levels == NULL ? shared : shared * unit_clamp(levels[k]);
    }

    for (uint32_t off = 0; off < nframes; off += FX_CHUNK) {
        uint32_t n = nframes - off < FX_CHUNK ? nframes - off : FX_CHUNK;

        /* Copied before anything is written, so `out` may alias `in`. */
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            if (in[ch] != NULL) {
                memcpy(x_buf[ch], in[ch] + off, n * sizeof(float));
            } else {
                memset(x_buf[ch], 0, n * sizeof(float));
            }
        }

        /* Any stage enabled or still fading out, at a depth that is not 0. */
        int live = 0;
        for (int k = 0; k < e->count; k++) {
            const int audible = ((mask >> k) & 1u) || e->on[k] > 0.0f;
            live |= audible && !(e->depth[k] == 0.0f && target[k] == 0.0f);
        }

        if (!live) {
            /* Bypass. Output is the input, bit for bit (within the clamp);
             * every effect keeps its history so re-entry is click-free. The
             * enable ramps land on their targets: nothing is audible to fade.
             * Each depth takes its target: an audible stage's is 0 already,
             * and a stage enabled later starts from where the knob and its
             * level are. */
            for (int k = 0; k < e->count; k++) {
                e->on[k] = ((mask >> k) & 1u) ? 1.0f : 0.0f;
                e->depth[k] = target[k];
                feed_one(e, k, x_in, n);
            }
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                if (out[ch] == NULL) {
                    continue;
                }
                for (uint32_t i = 0; i < n; i++) {
                    out[ch][off + i] = clamp_out(x_buf[ch][i]);
                }
            }
#ifdef FX_SELFTEST
            g_wobble_t += n;
#endif
            continue;
        }

        for (int s = 0; s < e->count; s++) {
#ifdef FX_SELFTEST
            const int k = e->order[fx_test_reverse_order ? e->count - 1 - s : s];
#else
            const int k = e->order[s];
#endif
            const float want = ((mask >> k) & 1u) ? 1.0f : 0.0f;
            float c = e->on[k];
            if (c == 0.0f && want == 0.0f) {
                /* Off: its history follows the signal at its place, and its
                 * depth takes its target, as in bypass. */
                e->depth[k] = target[k];
                feed_one(e, k, x_in, n);
                continue;
            }
            if (e->depth[k] == 0.0f && target[k] == 0.0f) {
                /* On at depth 0 (its level is 0, or the knob is): its output
                 * would be its input, so it is skipped as an off stage is,
                 * and its enable ramp lands. Coming up from here is the
                 * depth's ramp, not a step. */
                e->on[k] = want;
                feed_one(e, k, x_in, n);
                continue;
            }
            for (uint32_t i = 0; i < n; i++) {
                if (c < want) {
                    c = fminf(want, c + e->xfade_step);
                } else if (c > want) {
                    c = fmaxf(want, c - e->xfade_step);
                }
                on_ramp[i] = c;
            }
            e->on[k] = c;
            depth_ramp_for(e, k, target[k], depth_ramp, n);

            const fx_effect_desc *desc = &e->table[k];
            desc->process(e->state[k], x_in, wet, depth_ramp, n);
            const float wmax = desc->wet_max;
            const float full = 1.0f / desc->mix_full_at;
            for (uint32_t i = 0; i < n; i++) {
                /* full = 1 (the knob is the mix) leaves d unchanged, bit for bit. */
                const float dk = depth_ramp[i];
                const float w = fminf(1.0f, dk * full) * wmax;
                const float g = level_at(e, k, dk);
                const float ck = on_ramp[i];
                for (int ch = 0; ch < FX_CHANNELS; ch++) {
                    const float xs = x_buf[ch][i];
                    const float y = g * (xs + w * (wet_buf[ch][i] - xs));
                    x_buf[ch][i] = ck >= 1.0f ? y : xs + ck * (y - xs);
                }
            }
        }
#ifdef FX_SELFTEST
        g_wobble_t += n;
#endif

        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            if (out[ch] == NULL) {
                continue;
            }
            for (uint32_t i = 0; i < n; i++) {
                out[ch][off + i] = clamp_out(x_buf[ch][i]);
            }
        }
    }
}

/* ------------------------------------------------------------------------- */
/* Control protocol                                                           */
/* ------------------------------------------------------------------------- */

/* Deliberately not OSC, for the same reasons as the live monitor's `gain`
 * line: the sender stays a one-line sendto with no dependency, and a malformed
 * datagram from anything else on loopback must be ignorable without a parser. */
/* A decimal integer with no sign, no hex and no empty, followed by a blank. */
static int parse_uint(const char **cursor, long max, long *value)
{
    const char *rest = *cursor;
    while (*rest == ' ' || *rest == '\t') {
        rest++;
    }
    if (*rest < '0' || *rest > '9') {
        return 0;
    }
    char *end = NULL;
    errno = 0;
    long v = strtol(rest, &end, 10);
    if (end == rest || errno == ERANGE || v > max) {
        return 0;
    }
    if (*end != ' ' && *end != '\t') {
        return 0;
    }
    *cursor = end;
    *value = v;
    return 1;
}

/* A finite float, then a blank or the end. Leaves the cursor after it. */
static int parse_float(const char **cursor, float *value)
{
    const char *rest = *cursor;
    while (*rest == ' ' || *rest == '\t') {
        rest++;
    }
    char *end = NULL;
    errno = 0;
    float v = strtof(rest, &end);
    if (end == rest || errno == ERANGE || !isfinite(v)) {
        return 0;
    }
    if (*end != '\0' && *end != ' ' && *end != '\t' && *end != '\n' && *end != '\r') {
        return 0;
    }
    *cursor = end;
    *value = v;
    return 1;
}

static int only_blanks(const char *rest)
{
    while (*rest == ' ' || *rest == '\t' || *rest == '\n' || *rest == '\r') {
        rest++;
    }
    return *rest == '\0';
}

int fx_parse_control(const char *text, uint32_t *mask, float *depth,
                     float levels[FX_MAX_EFFECTS])
{
    while (*text == ' ' || *text == '\t') {
        text++;
    }
    enum { SINGLE, CHAIN, CHAIN_LEVELS } kind;
    const char *rest;
    /* "chainl" before "chain": the one is a prefix of the other. */
    if (strncmp(text, "chainl", 6) == 0) {
        kind = CHAIN_LEVELS;
        rest = text + 6;
    } else if (strncmp(text, "chain", 5) == 0) {
        kind = CHAIN;
        rest = text + 5;
    } else if (strncmp(text, "fx", 2) == 0) {
        kind = SINGLE;
        rest = text + 2;
    } else {
        return 0;
    }
    if (*rest != ' ' && *rest != '\t') {
        return 0;
    }
    long v = 0;
    if (!parse_uint(&rest, kind == SINGLE ? FX_MAX_EFFECTS - 1 : (1L << FX_MAX_EFFECTS) - 1,
                    &v)) {
        return 0;
    }
    float value = 0.0f;
    if (!parse_float(&rest, &value)) {
        return 0;
    }
    /* A level the sender did not name is 1.0. */
    float got[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        got[k] = 1.0f;
    }
    int n = 0;
    if (kind == CHAIN_LEVELS) {
        while (!only_blanks(rest)) {
            if (n == FX_MAX_EFFECTS || !parse_float(&rest, &got[n])) {
                return 0;
            }
            n++;
        }
    } else if (!only_blanks(rest)) {
        return 0;
    }
    *mask = kind == SINGLE ? (1u << v) : (uint32_t)v;
    *depth = value;
    memcpy(levels, got, sizeof(got));
    return 1;
}
