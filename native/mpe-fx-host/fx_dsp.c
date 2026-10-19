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
 * (fx_dsp.h; CHORUS_LEVEL below the effects) gives the loss back, to the
 * input's level on the keys signal. It is a fixed gain per knob position, so the chorus's
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

/* sin(pi x / 2) for x in [-1, 1], no trig: the odd Taylor polynomial to x^7,
 * its last coefficient adjusted so the ends land on exactly +-1. Off a true
 * sine by at most 1.4e-5 (-97 dB). */
static inline float half_sine(float x)
{
    const float x2 = x * x;
    return x * (1.5707963f + x2 * (-0.6459641f + x2 * (0.0796926f + x2 * -0.0045248f)));
}

/* A true sine LFO, no trig: the triangle above is linear in phase, so
 * half_sine of it is exactly cos(2 pi phase) up to the polynomial. Returns
 * -1..1 for phase 0..1, starting at +1. The rounded triangle (lfo_shape) is
 * the same curve's cubic approximation, 2 % off at worst. */
static inline float lfo_sine(float phase)
{
    return half_sine(4.0f * fabsf(phase - 0.5f) - 1.0f);
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
 *     that; +18 dB over unity). Was .. 16: the top is 6 dB lower. Round 5
 *     (2026-10-15, per-effect levels made the top "not aggressive enough"):
 *     over the top third G below ~600 Hz is multiplied by up to 4, to 32 at
 *     full (+30 dB over unity), the treble much less, so it barely aliases
 *     more;
 *     below a = 2/3 nothing changes (DRIVE_TOP_*).
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
 *   => ~490 ops per stereo frame (was ~480 before the top third), ~75% of it the four half-band
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
#define DRIVE_MAX 8.0f               /* G at the top of the base law */
/* The top third (2026-10-15, "not aggressive enough at the top"). With
 * t = (a - DRIVE_TOP_FROM) / (1 - DRIVE_TOP_FROM), 0..1 over the knob's top
 * third, and e = (DRIVE_TOP_BOOST - 1) t^2, 0..3, the shaper is fed
 *   G_base (pre + e LP(pre)),   LP a one-pole at DRIVE_TOP_LP_HZ,
 * and its output divided by G_base (1 + e). Well below 600 Hz that is
 * G_base x 4 = 32 at full (+12 dB of drive over the old top; the harmonics
 * it adds are the same with the corner anywhere from 0.5 to 2 kHz); the top
 * end that would alias gets little of it. The plain x4 aliased -26 dB at
 * 10 kHz (limit -35). The corner was 2 kHz until the review of #185 probed
 * 12.75-17.25 kHz: there it read 5-7 dB worse than the old top, at 600 Hz
 * 1.7-2.6 dB (fx_selftest.c, the aliasing check; 500 Hz moved enough of the
 * extra drive into the bass split's range that its correction no longer fit).
 * Below a = 2/3, e = 0: the old law exactly. t^2 matches value and slope at
 * 2/3, no kink. One more one-pole per channel. */
#define DRIVE_TOP_FROM (2.0f / 3.0f)
#define DRIVE_TOP_BOOST 4.0f
#define DRIVE_TOP_LP_HZ 600.0f
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
    float top_lp;       /* low-pass memory for the top third's extra gain */
    float dc_x, dc_y;   /* DC blocker */
    float pres;         /* low-pass memory for the presence shelf */
    float lp;           /* post low-pass */
} drive_chan;

typedef struct {
    drive_chan ch[FX_CHANNELS];
    float a_shelf, a_top, a_pres, k_pres, a_lp, dc_p, dc_k, a_detect, a_makeup;
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
    s->a_top = pole_alpha(DRIVE_TOP_LP_HZ, rate);
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
        float t = (a - DRIVE_TOP_FROM) * (1.0f / (1.0f - DRIVE_TOP_FROM));
        t = t > 0.0f ? (t < 1.0f ? t : 1.0f) : 0.0f;
#ifdef FX_SELFTEST
        if (fx_test_no_top) {
            t = 0.0f;
        }
#endif
        const float base = 0.5f * (1.0f + (2.0f * DRIVE_MAX - 1.0f) * a * a);   /* 0.5 .. 8 */
        const float extra = (DRIVE_TOP_BOOST - 1.0f) * t * t;                     /* 0 .. 3 */
        const float drive = base * (1.0f + extra);
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
            const float pre = x[ch] - 0.5f * c->shelf;
            c->top_lp += s->a_top * (pre - c->top_lp);
            float v = drive_core(c, (pre + extra * c->top_lp) * base, mode) * inv_drive;
            float dc = s->dc_k * (v - c->dc_x) + s->dc_p * c->dc_y;
            c->dc_x = v;
            c->dc_y = dc;
            c->pres += s->a_pres * (dc - c->pres);
            float bright = dc + s->k_pres * (dc - c->pres);
            c->lp += s->a_lp * (bright - c->lp);
            if (!(mode & DRIVE_NO_FLUSH)) {
                c->shelf = flush(c->shelf);
                c->top_lp = flush(c->top_lp);
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
 *     that kept noise under its input left the keys 3-4 dB down. (Under
 *     the unity fit, 2026-10-13, a full-band mutant read keys -0.15 dB and
 *     noise +0.7 dB, inside every bound, so it is no longer a control.)
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
 * (fx_dsp.h; PHASER_LEVEL below the effects) gives it back, to the input's
 * level on the keys signal at every knob position (2026-10-13; until then
 * it was held short, -1.17 dB at worst, so the phaser and flanger in series
 * stayed under the input). It is a fixed gain per knob position: no detector, no limiter
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

typedef struct {
    float table[PHASER_TABLE + 1];
    float z[FX_CHANNELS][PHASER_STAGES];
    float hp[FX_CHANNELS], lp[FX_CHANNELS];   /* feedback band-pass memories */
    float a_hp, a_lp;
    float phase, phase_inc;
} phaser_state;

enum { PHASER_NO_FLUSH = 1, PHASER_HOT = 2, PHASER_RUNAWAY = 8 };

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
            s->hp[ch] += s->a_hp * (v - s->hp[ch]);
            s->lp[ch] += s->a_lp * ((v - s->hp[ch]) - s->lp[ch]);
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
 * than their peaks give). The static level curve (fx_dsp.h; FLANGER_LEVEL
 * below the effects), calibrated on keys, gives it back to the input's level
 * on keys at every knob position (2026-10-13; it was -0.92 dB at worst). Peaks are not guaranteed:
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
 * [TREMOLO_FLOOR, 1], wet_max 1, so out = in (1 - d + d gain) before the
 * level curve. The curve gives back the RMS the dips take (+3.4 dB at full),
 * so the peaks, at the top of each swell, rise by that much over the input.
 * Fixed voicing: 5 Hz, a sine.
 *
 * Smoother (2026-10-13, asked for as "a smoother tremolo"). Two changes:
 *   - Shape: a sine (lfo_sine) instead of the rounded triangle. The rounded
 *     triangle was already within 2 % of a sine, so this alone is nearly
 *     inaudible: its third harmonic was -38 dB, and its worst per-sample gain
 *     slope (3.1e-4 at 48 kHz) was 4 % under a full-depth sine's (3.3e-4).
 *   - Floor: the gain's trough is TREMOLO_FLOOR (-12 dB), not silence. This is
 *     the audible change. Before, at full depth each channel went fully quiet
 *     for a fifth of every cycle (gain under 0.1, -20 dB, 19.6 % of the time):
 *     a gate opening and shutting five times a second, which is the chop. Now
 *     the level swells between 0 and -12 dB and never closes, and the slope
 *     falls to 0.75 of the sine's (2.45e-4 per sample).
 * fx_selftest.c checks the trough, the slope and the shape's purity, each
 * against the shape that fails it (the rounded triangle, a sine with no
 * floor).
 *
 * Bypass advances the LFO so it stays in time with an always-on one.
 *
 * Level. It has no level stage, on purpose. Its level falls by construction:
 * gain = a + b s, a = (1 + F) / 2, b = (1 - F) / 2 for the floor F and the
 * sine s (E[s^2] = 1/2), so the mean-square gain at depth d is
 * (1 - d (1 - a))^2 + (d b)^2 / 2: -0.8, -1.7, -2.6, -3.4 dB at 25, 50, 75 and
 * 100 % (was -1.1, -2.3, -3.4, -4.3). Lifting that back means gains above 1
 * on the LFO's crests, i.e. peaks over the input, and its peak guarantee is
 * the one it has. The self-test holds it to that computed level instead of to
 * -1 dB.
 *
 * Per stereo frame, active: LFO shape 2 x 9, gain 2 x 3, wraps 4, apply 2
 * => ~30 ops. Bypassed: the phase steps only, 2 ops. Laptop: ~5 ns/frame.
 */
#define TREMOLO_RATE_HZ 5.0f
#define TREMOLO_STEREO_OFFSET 0.5f
#define TREMOLO_FLOOR 0.25f   /* -12 dB: the trough at full depth */

typedef struct {
    float phase, phase_inc;
} tremolo_state;

enum { TREMOLO_ROUNDED = 1, TREMOLO_NO_FLOOR = 2 };

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

/* `mode` is a compile-time constant at every call site. */
static inline float tremolo_gain(float phase, float top, int mode)
{
    const float shape = (mode & TREMOLO_ROUNDED) ? lfo_shape(phase) : lfo_sine(phase);
    const float floor = (mode & (TREMOLO_ROUNDED | TREMOLO_NO_FLOOR)) ? 0.0f : TREMOLO_FLOOR;
    return top * (floor + (1.0f - floor) * (0.5f + 0.5f * shape));
}

static inline void tremolo_run(tremolo_state *s, const float *const in[FX_CHANNELS],
                               float *const wet[FX_CHANNELS], uint32_t n, float top, int mode)
{
    float phase = s->phase;
    for (uint32_t i = 0; i < n; i++) {
        float l = in[0][i];
        float r = in[1][i];
        wet[0][i] = l * tremolo_gain(phase, top, mode);
        wet[1][i] = r * tremolo_gain(wrap01(phase + TREMOLO_STEREO_OFFSET), top, mode);
        phase = wrap01(phase + s->phase_inc);
    }
    s->phase = phase;
}

static void tremolo_process(void *state, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(state, in, wet, n, 1.0f, 0);
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
/* Rotary speaker                                                             */
/* ------------------------------------------------------------------------- */

/* A Leslie-style rotary speaker: a horn and a drum spinning in front of two
 * microphones, one per channel, on opposite sides of the cabinet.
 *
 * Signal path, per channel (the input's own channel: the left input plays
 * into the left microphone's view, so a stereo patch stays stereo; a real
 * Leslie is fed mono, and a mono sum would collapse the patch and lose 3 dB on
 * whatever is not common to both sides):
 *
 *   x -> crossover (800 Hz) -> low  -> drum line -> tap at the drum's delay ------- x drum gain --+
 *                           -> high -> driver LP (5.5 kHz) -> horn line -> tap at the horn's delay
 *                              -> directivity: f tap + (1 - f) LP(tap) (1.8 kHz) x horn gain --+-> wet
 *
 *   - Crossover: second-order Linkwitz-Riley built from one one-pole low-pass
 *     coefficient: low = LP(LP(x)), high = -HP(HP(x)), HP = 1 - LP. The two
 *     bands add back to an all-pass of x when they line up, and their powers
 *     add to at most the input's whatever their delays: |LP|^4 + |HP|^4 <=
 *     (|LP|^2 + |HP|^2)^2 <= 1, since for the one-pole LP = a / (1 - b z^-1),
 *     b = 1 - a, |LP|^2 + |HP|^2 = 1 - 2ab (1 - cos w) / |1 - b e^-jw|^2.
 *   - Each rotor has a phase th (0..1) and c = cos(2 pi th) (lfo_sine). The
 *     left microphone sees the horn come round as c goes to +1, the right as c
 *     goes to -1. Doppler: the delay is centre -/+ excursion x c (the horn
 *     0.40 ms, the drum 0.25 ms, both around 0.5 ms), so the pitch rises as
 *     a rotor approaches a microphone and falls as it leaves: at the fast
 *     speed, +-1.7 % (29 cents) for the horn by the excursion alone, +-2.9 %
 *     as fx_selftest.c reads it on a 2 kHz tone (the level swing and the
 *     directivity add their sidebands), +-0.9 % for the drum, the two ears in
 *     opposite directions. Level: the gains are sqrt(1 +- a c), a 0.70 for
 *     the horn (+2.3 / -5.2 dB) and 0.35 for the drum, so the two
 *     microphones' gains' squares always sum to 2: the sound swings across
 *     the image without the total changing.
 *   - Horn driver: a real horn's compression driver rolls off above 5-6 kHz,
 *     so the horn band goes through a one-pole low-pass at 5.5 kHz. Before
 *     the delay line, where the driver is: the rotation shifts the pitch of
 *     what the driver already played. Without it the top octaves of the
 *     keys went round the horn at full strength, Doppler sidebands and all:
 *     a fizz on top, the likeliest part of what was heard as the rotary
 *     interfering with itself (2026-10-17; not yet confirmed by ear).
 *   - Directivity: a horn is bright with its mouth towards a microphone and
 *     dull with it turned away; the tone swirling with the rotation, not
 *     only the level and the pitch, is most of the Leslie's warmth. Each
 *     microphone keeps a dull copy of its own horn tap (one-pole low-pass,
 *     1.8 kHz, its own memory) and hears f x tap + (1 - f) x dull, f = (1 +
 *     c_mic) / 2 how far the mouth faces it: c_mic = +c left, -c right. So
 *     the left hears the bright horn as it comes round (f 1), the dull one
 *     as it faces the right (f 0), and side-on half of each. Fixed
 *     coefficients: per frame it is one crossfade weight, per channel a
 *     one-pole and a crossfade.
 *   - Speed: the knob is the speed. Its first ROTARY_MIX_FULL (10 %) fades the
 *     rotary in at the slow (chorale) speed: the engine's mix, mix_full_at
 *     0.1. From there to the top the target speed runs linearly from chorale
 *     (horn 0.80 Hz, drum 0.67 Hz) to tremolo (horn 6.7 Hz, drum 5.7 Hz).
 *     Each rotor follows its target through its own inertia, a one-pole on
 *     the speed: the light horn spins up in ~1 s and down in ~1.8 s (time
 *     constants 0.35 / 0.60 s), the heavy drum in ~4.5 s and ~6 s (1.5 /
 *     2.0 s), so a knob flip from slow to fast is the familiar wind-up, the
 *     horn ahead of the drum, and back.
 *
 * Depth 0 is the engine's bypass, bit for bit. Above it the mix is
 * d / 0.1 of full: the first knob step (1/127) mixes in 8 % of the rotary,
 * about -22 dB, at the edge of hearing; by 5 % the mix is half and the slow
 * rotor is clearly there; at 10 % it is all rotary at chorale speed.
 *
 * Level. With the same signal on both channels the gains alone keep the
 * two microphones' powers summed to the input's at every instant, whatever
 * the rotors do: for one band the gains' squares sum to 2, and the
 * crossover's bands, aligned or not, add to at most the input (above). The
 * drum band keeps that. The horn band gives some of it up to the
 * directivity, by design: side-on, both microphones hear half the dull
 * copy, so on a 3 kHz tone the sum dips to 0.49 of the input with the horn
 * side-on and comes back to 0.89 as it faces one (2.60 dB; the driver's
 * -1.1 dB there is in both). fx_selftest.c checks the power sum stays
 * within 4.4 dB on that tone (3.39 measured: the directivity, the Doppler
 * and the crossover; 1.25 before the directivity), and that a 5 kHz tone
 * at one microphone swings 18.6 dB as the horn turns (the level swing
 * alone is 7.5). Below 10 % it is a mix of the input and a ~0.5 ms late
 * copy, a comb whose first notch is at 1 kHz: keys -2.8 dB at 5 % before the level
 * curve. A 1 ms centre put that notch at 500 Hz, in the middle of the keys:
 * -5.1 dB. The crossover's band edge (its powers sum to -3 dB at 800 Hz when
 * the bands' delays differ), the delay lines' linear interpolation and the
 * horn's driver and directivity lose a little more on broadband: keys -0.47
 * dB at full (-0.42 before the driver and the directivity, the same run:
 * little of the keys' energy is where they act). The level curve gives all
 * of it back on keys; tones the comb passes come out over the input, up to
 * +2.5 dB (80 Hz at 5 %). Peaks are not guaranteed: a channel's gain reaches
 * +2.3 dB as the horn faces it. The engine's clamp holds +-1.
 *
 * The delay lines and crossover are written in bypass too (feed runs the whole
 * effect with the output discarded), and the rotors keep turning at the speed
 * the knob at 0 asks for (chorale), so coming back in matches an instance
 * that never stopped.
 *
 * Per stereo frame, active: speed targets 4, two rotors' inertia 2 x 4 and
 * phase 2 x 3, two cosines 2 x 9, four gains (one sqrt each) 4 x 3,
 * directivity weights 2; per channel: crossover 10 and its flushes 3, driver
 * low-pass 3 and its flush 1, two writes, two taps (delay 2, split 2, two
 * reads and a lerp 3) 14, dull copy 3 and its flush 1, crossfade 3, mix 3
 * => ~41; total ~130 ops plus 8 memory ops. No trig, no division. Runs
 * while bypassed too.
 */
#define ROTARY_XOVER_HZ 800.0f
#define ROTARY_DRIVER_HZ 5500.0f  /* the horn's compression driver rolls off here */
#define ROTARY_DARK_HZ 1800.0f    /* the horn's sound with its mouth turned away */
#define ROTARY_HORN_SLOW_HZ 0.80f
#define ROTARY_HORN_FAST_HZ 6.7f
#define ROTARY_DRUM_SLOW_HZ 0.67f
#define ROTARY_DRUM_FAST_HZ 5.7f
#define ROTARY_HORN_UP_S 0.35f
#define ROTARY_HORN_DOWN_S 0.60f
#define ROTARY_DRUM_UP_S 1.5f
#define ROTARY_DRUM_DOWN_S 2.0f
#define ROTARY_CENTER_MS 0.5f
#define ROTARY_HORN_EXC_MS 0.40f
#define ROTARY_DRUM_EXC_MS 0.25f
#define ROTARY_HORN_AM 0.70f
#define ROTARY_DRUM_AM 0.35f
#define ROTARY_DRUM_START 0.37f   /* the drum's phase at start: not in step with the horn */
#define ROTARY_MIX_FULL 0.1f      /* the knob position where the mix is all rotary */

/* Power of two >= (centre + excursion) at FX_MAX_RATE plus two taps:
 * 1.4 ms x 192 kHz = 269 samples. */
#define ROTARY_BUF 512u
#define ROTARY_MASK (ROTARY_BUF - 1u)

typedef struct {
    float horn[FX_CHANNELS][ROTARY_BUF];
    float drum[FX_CHANNELS][ROTARY_BUF];
    float lp1[FX_CHANNELS], lp2[FX_CHANNELS], hp2[FX_CHANNELS];   /* crossover */
    float drv[FX_CHANNELS];        /* the horn driver's low-pass, before the horn line */
    float dark[FX_CHANNELS];       /* each microphone's dull copy of its horn tap */
    float a_x, a_drv, a_dark;
    uint32_t w;
    float horn_hz, drum_hz;        /* the rotors' speeds */
    float horn_ph, drum_ph;        /* their phases, 0..1 */
    float inv_rate;
    float horn_up, horn_down, drum_up, drum_down;   /* inertia, per frame */
    float center, horn_exc, drum_exc;               /* samples */
} rotary_state;

enum { ROTARY_NO_FLUSH = 1, ROTARY_NO_DOPPLER = 2, ROTARY_NO_INERTIA = 4, ROTARY_LINEAR_AM = 8,
       ROTARY_WIDE_CENTER = 16, ROTARY_NO_DIRECTIVITY = 32 };

static float inertia_alpha(float seconds, float rate)
{
    return (float)(1.0 - exp(-1.0 / ((double)seconds * (double)rate)));
}

static int rotary_init(void *state, float rate)
{
    rotary_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    s->a_x = pole_alpha(ROTARY_XOVER_HZ, rate);
    s->a_drv = pole_alpha(ROTARY_DRIVER_HZ, rate);
    s->a_dark = pole_alpha(ROTARY_DARK_HZ, rate);
    s->w = 0;
    s->horn_hz = ROTARY_HORN_SLOW_HZ;
    s->drum_hz = ROTARY_DRUM_SLOW_HZ;
    s->horn_ph = 0.0f;
    s->drum_ph = ROTARY_DRUM_START;
    s->inv_rate = 1.0f / rate;
    s->horn_up = inertia_alpha(ROTARY_HORN_UP_S, rate);
    s->horn_down = inertia_alpha(ROTARY_HORN_DOWN_S, rate);
    s->drum_up = inertia_alpha(ROTARY_DRUM_UP_S, rate);
    s->drum_down = inertia_alpha(ROTARY_DRUM_DOWN_S, rate);
    s->center = ROTARY_CENTER_MS * 0.001f * rate;
    s->horn_exc = ROTARY_HORN_EXC_MS * 0.001f * rate;
    s->drum_exc = ROTARY_DRUM_EXC_MS * 0.001f * rate;
    return 0;
}

/* buf[n - delay], delay >= 1 sample, read before buf[n] is written. */
static inline float rotary_tap(const float *buf, uint32_t w, float delay)
{
    uint32_t whole = (uint32_t)delay;
    float frac = delay - (float)whole;
    float a = buf[(w - whole) & ROTARY_MASK];
    float b = buf[(w - whole - 1u) & ROTARY_MASK];
    return a + frac * (b - a);
}

static inline float rotor_step(float hz, float target, float up, float down, int mode)
{
    if (mode & ROTARY_NO_INERTIA) {
        return target;
    }
    return hz + (target > hz ? up : down) * (target - hz);
}

static inline void rotary_run(rotary_state *s, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], const float *amount, uint32_t n,
                              int mode)
{
    uint32_t w = s->w;
    const float horn_exc = (mode & ROTARY_NO_DOPPLER) ? 0.0f : s->horn_exc;
    const float drum_exc = (mode & ROTARY_NO_DOPPLER) ? 0.0f : s->drum_exc;
    const float center = (mode & ROTARY_WIDE_CENTER) ? 2.0f * s->center : s->center;
    for (uint32_t i = 0; i < n; i++) {
        /* Read the inputs before any write: `wet` may share memory with them. */
        float x[FX_CHANNELS] = { finite_or_zero(in[0][i]), finite_or_zero(in[1][i]) };

        float sp = (amount[i] - ROTARY_MIX_FULL) * (1.0f / (1.0f - ROTARY_MIX_FULL));
        sp = sp > 0.0f ? (sp < 1.0f ? sp : 1.0f) : 0.0f;   /* NaN -> 0 */
        const float horn_t = ROTARY_HORN_SLOW_HZ + (ROTARY_HORN_FAST_HZ - ROTARY_HORN_SLOW_HZ) * sp;
        const float drum_t = ROTARY_DRUM_SLOW_HZ + (ROTARY_DRUM_FAST_HZ - ROTARY_DRUM_SLOW_HZ) * sp;
        s->horn_hz = rotor_step(s->horn_hz, horn_t, s->horn_up, s->horn_down, mode);
        s->drum_hz = rotor_step(s->drum_hz, drum_t, s->drum_up, s->drum_down, mode);
        s->horn_ph = wrap01(s->horn_ph + s->horn_hz * s->inv_rate);
        s->drum_ph = wrap01(s->drum_ph + s->drum_hz * s->inv_rate);
        const float ch = lfo_sine(s->horn_ph);
        const float cd = lfo_sine(s->drum_ph);
        /* Left microphone: +c; right: -c. */
        float gh[FX_CHANNELS] = { sqrtf(1.0f + ROTARY_HORN_AM * ch),
                                  sqrtf(1.0f - ROTARY_HORN_AM * ch) };
        float gd[FX_CHANNELS] = { sqrtf(1.0f + ROTARY_DRUM_AM * cd),
                                  sqrtf(1.0f - ROTARY_DRUM_AM * cd) };
        if (mode & ROTARY_LINEAR_AM) {   /* self-test: powers no longer sum to 2 */
            gh[0] = 1.0f + ROTARY_HORN_AM * ch;
            gh[1] = 1.0f - ROTARY_HORN_AM * ch;
            gd[0] = 1.0f + ROTARY_DRUM_AM * cd;
            gd[1] = 1.0f - ROTARY_DRUM_AM * cd;
        }
        /* How far the horn's mouth faces each microphone, 0 (away) .. 1. */
        const float fl = 0.5f + 0.5f * ch;
        float fh[FX_CHANNELS] = { fl, 1.0f - fl };
        if (mode & ROTARY_NO_DIRECTIVITY) {   /* self-test: the horn sounds the same all round */
            fh[0] = 1.0f;
            fh[1] = 1.0f;
        }
        const float dh[FX_CHANNELS] = { center - horn_exc * ch, center + horn_exc * ch };
        const float dd[FX_CHANNELS] = { center - drum_exc * cd, center + drum_exc * cd };

        float y[FX_CHANNELS];
        for (int c = 0; c < FX_CHANNELS; c++) {
            s->lp1[c] += s->a_x * (x[c] - s->lp1[c]);
            s->lp2[c] += s->a_x * (s->lp1[c] - s->lp2[c]);
            const float h1 = x[c] - s->lp1[c];
            s->hp2[c] += s->a_x * (h1 - s->hp2[c]);
            const float lo = s->lp2[c];
            const float hi = s->hp2[c] - h1;   /* -(h1 - LP(h1)) */
            s->drv[c] += s->a_drv * (hi - s->drv[c]);
            const float th = rotary_tap(s->horn[c], w, dh[c]);
            const float td = rotary_tap(s->drum[c], w, dd[c]);
            s->dark[c] += s->a_dark * (th - s->dark[c]);
            if (!(mode & ROTARY_NO_FLUSH)) {
                s->lp1[c] = flush(s->lp1[c]);
                s->lp2[c] = flush(s->lp2[c]);
                s->hp2[c] = flush(s->hp2[c]);
                s->drv[c] = flush(s->drv[c]);
                s->dark[c] = flush(s->dark[c]);
            }
            s->horn[c][w] = s->drv[c];
            s->drum[c][w] = lo;
            const float hb = s->dark[c] + fh[c] * (th - s->dark[c]);   /* f bright + (1 - f) dark */
            y[c] = gh[c] * hb + gd[c] * td;
        }
        if (wet != NULL) {
            wet[0][i] = y[0];
            wet[1][i] = y[1];
        }
        w = (w + 1u) & ROTARY_MASK;
    }
    s->w = w;
}

static void rotary_process(void *state, const float *const in[FX_CHANNELS],
                           float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(state, in, wet, amount, n, 0);
}

static void rotary_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    rotary_run(state, in, NULL, ZERO_AMOUNT, n, 0);
}

/* ------------------------------------------------------------------------- */
/* Auto-wah                                                                   */
/* ------------------------------------------------------------------------- */

/* An envelope filter: a resonant band-pass whose centre follows how hard the
 * keys are played. A strike opens it upward, and as the note decays it closes
 * back down: the "wow".
 *
 * Signal path (both channels through one filter setting, so the image does not
 * move):
 *
 *   envelope = attack/release follower of max(|L|, |R|)
 *   s        = envelope / (envelope + WAH_SENS)          0..1, no log
 *   u        = s x depth                                  where in the range
 *   centre   = WAH_LO_HZ x (WAH_HI_HZ / WAH_LO_HZ)^u      a table, per rate
 *   wet      = k x bandpass(x)                            peak gain exactly 1
 *
 *   - Envelope: attack 4 ms (a strike opens it at once), release 120 ms (it
 *     closes with the note, not with each cycle of the waveform). One compare
 *     and one multiply-add per frame, in place: no buffer, no allocation.
 *   - s: a soft knee at WAH_SENS (0.05, -26 dBFS): a quiet note opens it a
 *     little, a firm one most of the way. One divide.
 *   - Range: 280 Hz (closed) to 2.4 kHz (fully open), on a log scale, so the
 *     sweep is even in pitch. Depth scales the range: at half depth the most
 *     a strike can open it is ~820 Hz.
 *   - Filter: the trapezoidal state-variable filter (Zavalishin's TPT SVF),
 *     which stays stable and click-free under a cutoff moving every sample.
 *     Q 2.2 (k = 1/Q = 0.45): enough to hear the vowel, low enough that it
 *     never whistles: after a noise burst it rings down 40 dB in 5.3 ms
 *     low in the range (fx_selftest.c; Q 12 takes 32 ms), and k x band-pass has gain exactly 1 at its centre
 *     and below 1 everywhere else, so no tone is ever lifted. The coefficient
 *     g = tan(pi f / rate) is tabulated over u (WAH_TABLE points) and
 *     interpolated; a1 = 1 / (1 + g (g + k)) is one divide per frame.
 *   - The top third (round 2, 2026-10-15: "deeper and wider sweep, more
 *     resonance" at the top): from depth 2/3 to full, by t^2 (t 0..1 over
 *     that third, so the value and the slope match at 2/3), the range
 *     widens from 280 Hz..2.4 kHz to 200 Hz..3 kHz (3.9 octaves, was 3.1)
 *     and Q follows the band: at full, 2 closed to 4 fully open (k 0.50 ->
 *     0.25, linear in the band's log position; was 2.2 everywhere), so the
 *     resonance is strongest where the sweep peaks and a quiet note low in
 *     the range rings no longer than before (a ring lasts ~Q / f; Q 4 at
 *     200 Hz would ring nearly as long as the Q 12 whistle at 530 Hz). The output is still k x band-pass, peak
 *     gain 1. Below 2/3 it is the old wah: the table spans
 *     180 Hz..3 kHz and the old ends sit at WAH_POS_LO / WAH_POS_HI. Why not
 *     further up: the level curve lifts keys ~9 dB at full, so a tone at the
 *     band's centre reads about that over its input, and a top of 4 kHz put
 *     the level check's 3 kHz tone there (+8.3 dB, ceiling +3). With 3 kHz
 *     and the extra width going down, the worst signal reads +1.9 dB.
 *   - Mix: wet_max 0.65 at full depth, rising with the knob (mix_full_at 1):
 *     away from the centre the output is 0.35 of the input (-9 dB), at the
 *     centre all of it. Depth sets both the range and the mix.
 *
 * Depth 0 is the engine's bypass, bit for bit. The first knob step (1/127)
 * mixes in 0.5 % of a filter that barely leaves 280 Hz: inaudible. Around
 * 15-20 % it is plainly a filter moving with the playing.
 *
 * Level. The band-pass's gain is at most 1 at every frequency, and the mix is
 * a weighted average of the input and it, so before the level curve the wah
 * only takes energy away, and its whole sound is how much it takes from
 * outside its band: keys -2.8 dB at 50 % and -8.7 dB at full. The level curve
 * gives that back on keys (up to +8.7 dB), so what sits in the band comes out
 * over its input: the 3 kHz tone +1.9 dB at full. A sine at the band's centre
 * reads about the whole lift: the any-signal bound is a sample of tones.
 * fx_selftest.c checks the band opens within 25 ms of a loud note.
 *
 * Stability: for any g > 0 and k > 0 the TPT SVF is stable, and the table
 * only holds positive g; the self-test runs 28.8 s at full depth with the
 * clamp off, against a mutant with negative damping that must grow.
 *
 * Bypass runs the follower and the filter with the output discarded, so
 * coming back in matches an instance that never stopped.
 *
 * Per stereo frame, active: detector 4, follower 3, knee 3 (one divide),
 * range 1, table lerp 6, a1..a3 6 (one divide), top third 10; per channel:
 * SVF 10, flushes 2, scale 1 => ~26; total ~85 ops. No trig. Runs while
 * bypassed too.
 */
#define WAH_LO_HZ 280.0
#define WAH_HI_HZ 2400.0         /* fully open, below the top third */
#define WAH_TOP_HZ 3000.0        /* fully open, at full depth */
#define WAH_BOTTOM_HZ 200.0      /* closed, at full depth */
#define WAH_K 0.45f              /* 1 / Q */
#define WAH_K_OPEN 0.25f         /* 1 / Q at full depth, fully open: Q 4 */
#define WAH_K_CLOSED 0.50f       /* 1 / Q at full depth, closed: Q 2 */
#define WAH_TOP_FROM (2.0f / 3.0f)
/* The table spans WAH_BOTTOM_HZ..WAH_TOP_HZ (log); the old range's ends sit
 * at these positions in it. */
#define WAH_SPAN (log(WAH_TOP_HZ / WAH_BOTTOM_HZ))
#define WAH_POS_LO ((float)(log(WAH_LO_HZ / WAH_BOTTOM_HZ) / WAH_SPAN))
#define WAH_POS_HI ((float)(log(WAH_HI_HZ / WAH_BOTTOM_HZ) / WAH_SPAN))
#define WAH_ATTACK_MS 4.0f
#define WAH_RELEASE_MS 120.0f
#define WAH_SENS 0.05f
#define WAH_WET 0.65f
#define WAH_TABLE 128

typedef struct {
    float g[WAH_TABLE + 1];
    float ic1[FX_CHANNELS], ic2[FX_CHANNELS];
    float env;
    float a_attack, a_release;
} wah_state;

enum { WAH_NO_FLUSH = 1, WAH_NO_SWEEP = 2, WAH_RUNAWAY = 4, WAH_WHISTLE = 8, WAH_SLOW_ATTACK = 16 };

static int wah_init(void *state, float rate)
{
    wah_state *s = state;
    if (!(rate > 0.0f) || rate > (float)FX_MAX_RATE) {
        return -1;
    }
    for (int j = 0; j <= WAH_TABLE; j++) {
        double hz = WAH_BOTTOM_HZ * pow(WAH_TOP_HZ / WAH_BOTTOM_HZ, (double)j / WAH_TABLE);
        s->g[j] = (float)tan(M_PI * hz / (double)rate);
    }
    s->a_attack = (float)(1.0 - exp(-1.0 / (WAH_ATTACK_MS * 0.001 * (double)rate)));
    s->a_release = (float)(1.0 - exp(-1.0 / (WAH_RELEASE_MS * 0.001 * (double)rate)));
    s->env = 0.0f;
    return 0;
}

static inline void wah_run(wah_state *s, const float *const in[FX_CHANNELS],
                           float *const wet[FX_CHANNELS], const float *amount, uint32_t n,
                           int mode)
{
    const int fixed_k = mode & (WAH_RUNAWAY | WAH_WHISTLE);
    const float k_fixed = (mode & WAH_RUNAWAY) ? -0.05f : (1.0f / 12.0f);
    /* 4 ms -> ~400 ms: the one-pole's coefficient is ~1 / (ms x rate). */
    const float a_attack = (mode & WAH_SLOW_ATTACK) ? 0.01f * s->a_attack : s->a_attack;
    for (uint32_t i = 0; i < n; i++) {
        /* Read the inputs before any write: `wet` may share memory with them. */
        float x[FX_CHANNELS] = { finite_or_zero(in[0][i]), finite_or_zero(in[1][i]) };
        float peak = fmaxf(fabsf(x[0]), fabsf(x[1]));
        s->env += (peak > s->env ? a_attack : s->a_release) * (peak - s->env);
        if (!(mode & WAH_NO_FLUSH)) {
            s->env = flush(s->env);
        }
        /* The top third: t^2 from 0 at 2/3 to 1 at full, as the drive's. */
        const float d = amount[i];
        float t = (d - WAH_TOP_FROM) * (1.0f / (1.0f - WAH_TOP_FROM));
        t = t > 0.0f ? (t < 1.0f ? t : 1.0f) : 0.0f;   /* NaN -> 0 */
#ifdef FX_SELFTEST
        if (fx_test_no_top) {
            t = 0.0f;
        }
#endif
        const float t2 = t * t;
        float open = s->env / (s->env + WAH_SENS);
        if (mode & WAH_NO_SWEEP) {
            open = 0.5f;
        }
        float u = open * d;
        u = u > 0.0f ? (u < 1.0f ? u : 1.0f) : 0.0f;   /* NaN -> 0 */
        /* 280 Hz..2.4 kHz below the top third, 200 Hz..3 kHz at full. */
        const float lo = WAH_POS_LO - t2 * WAH_POS_LO;
        const float hi = WAH_POS_HI + t2 * (1.0f - WAH_POS_HI);
        const float p = lo + u * (hi - lo);
        /* Q follows the band up at the top: a ring lasts ~Q / f, so low in
         * the range it must stay low. */
        const float k_top = WAH_K_CLOSED + p * (WAH_K_OPEN - WAH_K_CLOSED);
        const float k = fixed_k ? k_fixed : WAH_K + t2 * (k_top - WAH_K);
        const float out_k = (mode & WAH_WHISTLE) ? 1.0f : k;
        float pos = p * (float)WAH_TABLE;
        uint32_t j = (uint32_t)pos;
        if (j >= WAH_TABLE) {
            j = WAH_TABLE - 1;
        }
        const float g = s->g[j] + (pos - (float)j) * (s->g[j + 1] - s->g[j]);
        const float a1 = 1.0f / (1.0f + g * (g + k));
        const float a2 = g * a1;
        const float a3 = g * a2;
        for (int c = 0; c < FX_CHANNELS; c++) {
            const float v3 = x[c] - s->ic2[c];
            const float v1 = a1 * s->ic1[c] + a2 * v3;
            const float v2 = s->ic2[c] + a2 * s->ic1[c] + a3 * v3;
            s->ic1[c] = 2.0f * v1 - s->ic1[c];
            s->ic2[c] = 2.0f * v2 - s->ic2[c];
            if (!(mode & WAH_NO_FLUSH)) {
                s->ic1[c] = flush(s->ic1[c]);
                s->ic2[c] = flush(s->ic2[c]);
            }
            if (wet != NULL) {
                wet[c][i] = out_k * v1;
            }
        }
    }
}

static void wah_process(void *state, const float *const in[FX_CHANNELS],
                        float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    wah_run(state, in, wet, amount, n, 0);
}

static void wah_feed(void *state, const float *const in[FX_CHANNELS], uint32_t n)
{
    wah_run(state, in, NULL, ZERO_AMOUNT, n, 0);
}

/* ------------------------------------------------------------------------- */
/* The table                                                                  */
/* ------------------------------------------------------------------------- */

/* Level fits (fx_dsp.h, "Level curve"): { p0, p1, c0, c1 } per effect, so the
 * effect alone reads the input's RMS on the keys signal at every knob
 * position, before the chain's makeup (2026-10-13: "the total boost should be
 * 1 dB no matter what the effects are"). Least squares, relative error, on
 * each effect's raw loss at 32 knob positions, plus 12 inside the drive's and
 * the rotary's fade-ins (`fx-selftest --unity-raw K DEPTH`, level curve and
 * makeup off, 4.8 s of keys); the fit's worst residual is 0.10 dB (the
 * auto-wah), 0.06 the drive, 0.05 the rotary, 0.001 the rest (the drive and
 * the auto-wah refitted 2026-10-15 for their stronger top thirds, the rotary
 * 2026-10-17 for the horn's driver and directivity). The self-test
 * holds every effect to +-0.3 dB of the input on keys. Re-run the report and
 * refit when an effect's voicing changes: the unity check fails first. What
 * the raw loss was, keys at full depth: chorus -3.0, drive +0.06, phaser
 * -0.88, tremolo -3.36, flanger -2.09, rotary -0.47, auto-wah -8.71 dB.
 * Other signals keep their own difference from keys (`fx-selftest --unity
 * MASK DEPTH`): one effect alone at any depth, -6.6..+2.5 dB (the chorus's
 * 80 Hz tone at full; the rotary's 80 Hz tone in its fade-in). */
#define CHORUS_LEVEL { 0.2381f, 0.3811f, 0.0030f, 0.1900f }
#define DRIVE_LEVEL { 0.9519f, 0.0792f, 0.4629f, -0.1487f }
#define PHASER_LEVEL { 0.5039f, 0.6722f, 0.1522f, 0.3351f }
#define TREMOLO_LEVEL { 0.2555f, 0.2044f, 0.6255f, 0.1021f }
#define FLANGER_LEVEL { 0.4161f, 0.5551f, -0.0499f, 0.2770f }
#define ROTARY_LEVEL { 0.8872f, 0.0021f, 0.0771f, 0.2978f }
#define WAH_LEVEL { -0.0519f, 0.1251f, 0.3731f, -0.4100f }

/* The bass bypass's level correction (fx_dsp.h, "Bass bypass"): at each
 * grid crossover, the level change (dB, negative a loss) whose negative,
 * applied to the high band at the effect's full mix, puts the keys at full
 * depth back at their level with the split off. Fitted by fx-selftest
 * --fit-bass K (2026-10-15, the rotary 2026-10-17; the drive at depth 0.88,
 * see fit_bass in fx_selftest.c); 20 Hz is no overlap with the keys, 0.
 * Above 300 Hz (the UI's top) the last value holds. */
const float FX_BASS_GRID_HZ[FX_BASS_GRID] = { 20.0f, 80.0f, 100.0f, 120.0f, 150.0f, 200.0f,
                                              250.0f, 300.0f };
static const float CHORUS_BASS[FX_BASS_GRID] = { 0, -0.048f, -0.056f, -0.062f, -0.104f, -0.281f, -0.524f, -0.769f };
static const float DRIVE_BASS[FX_BASS_GRID] = { 0, -0.098f, -0.160f, -0.217f, -0.299f, -0.584f, -1.090f, -1.735f };
static const float PHASER_BASS[FX_BASS_GRID] = { 0, -0.118f, -0.186f, -0.256f, -0.402f, -0.838f, -1.480f, -2.208f };
static const float TREMOLO_BASS[FX_BASS_GRID] = { 0, -0.027f, -0.040f, -0.052f, -0.076f, -0.146f, -0.244f, -0.354f };
static const float FLANGER_BASS[FX_BASS_GRID] = { 0, -0.148f, -0.222f, -0.294f, -0.440f, -0.860f, -1.417f, -1.956f };
static const float ROTARY_BASS[FX_BASS_GRID] = { 0, -0.114f, -0.211f, -0.340f, -0.623f, -1.392f, -2.460f, -3.670f };
static const float WAH_BASS[FX_BASS_GRID] = { 0, 0.042f, 0.068f, 0.100f, 0.154f, 0.272f, 0.470f, 0.785f };

/* The wire's effect ids. Append only: an id is a number the policy module
 * sends, and renumbering would silently change what an old sender selects.
 * patch_browser/fx_host_protocol.py names the same ids in the same order;
 * tests/test_fx_host.py reads this table and fails if they drift. */
const fx_effect_desc FX_EFFECTS[] = {
    { "chorus", 0.5f, 1.0f, CHORUS_LEVEL, sizeof(chorus_state),
      chorus_init, chorus_process, chorus_feed, CHORUS_BASS },
    { "drive", 1.0f, 0.05f, DRIVE_LEVEL, sizeof(drive_state), drive_init, drive_process,
      drive_feed, DRIVE_BASS },
    { "phaser", PHASER_WET, 1.0f, PHASER_LEVEL, sizeof(phaser_state),
      phaser_init, phaser_process, phaser_feed, PHASER_BASS },
    { "tremolo", 1.0f, 1.0f, TREMOLO_LEVEL, sizeof(tremolo_state), tremolo_init, tremolo_process,
      tremolo_feed, TREMOLO_BASS },
    { "flanger", FLANGER_WET, 1.0f, FLANGER_LEVEL,
      sizeof(flanger_state), flanger_init, flanger_process, flanger_feed, FLANGER_BASS },
    { "rotary", 1.0f, ROTARY_MIX_FULL, ROTARY_LEVEL,
      sizeof(rotary_state), rotary_init, rotary_process, rotary_feed, ROTARY_BASS },
    { "auto-wah", WAH_WET, 1.0f, WAH_LEVEL, sizeof(wah_state), wah_init, wah_process, wah_feed,
      WAH_BASS },
};
const int FX_EFFECT_COUNT = (int)(sizeof(FX_EFFECTS) / sizeof(FX_EFFECTS[0]));

/* The run order, as ids: Drive -> Auto-wah -> Phaser -> Flanger -> Chorus ->
 * Rotary -> Tremolo. The drive first, so it shapes the plain signal rather
 * than a swept or doubled one. The auto-wah next: an envelope filter reads how
 * hard the keys are played, and every later stage blurs that (a sweep moves
 * the level, the rotary and tremolo modulate it), while the drive's own level
 * law keeps the dynamics it hands on; a wah after a drive is the pedalboard
 * habit too. The two sweeps next to each other, ahead of the chorus's
 * doubling. The rotary after them: it is a speaker cabinet, the end of an
 * organ's chain, so what comes before it is what it plays (an organ's own
 * chorus-vibrato sits before its Leslie), and a chorus after it would smear
 * its Doppler. The tremolo last, so its level wobble is not then shaped by
 * anything. patch_browser/fx_host_protocol.py's CHAIN_ORDER names the same
 * ids; tests/test_fx_host.py reads this array and fails if they drift. */
const int FX_CHAIN_ORDER[] = { 1, 6, 2, 4, 0, 5, 3 };
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
    tremolo_run(st, in, wet, n, 1.25f, 0);
}

static void tremolo_shallow_top(void *st, const float *const in[FX_CHANNELS],
                                float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(st, in, wet, n, 0.8f, 0);
}
static void tremolo_rounded(void *st, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(st, in, wet, n, 1.0f, TREMOLO_ROUNDED);
}
static void tremolo_no_floor(void *st, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    tremolo_run(st, in, wet, n, 1.0f, TREMOLO_NO_FLOOR);
}
static void rotary_no_flush(void *st, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(st, in, wet, amount, n, ROTARY_NO_FLUSH);
}
static void rotary_no_flush_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    rotary_run(st, in, NULL, ZERO_AMOUNT, n, ROTARY_NO_FLUSH);
}
static void rotary_no_doppler(void *st, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(st, in, wet, amount, n, ROTARY_NO_DOPPLER);
}
static void rotary_no_inertia(void *st, const float *const in[FX_CHANNELS],
                              float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(st, in, wet, amount, n, ROTARY_NO_INERTIA);
}
static void rotary_linear_am(void *st, const float *const in[FX_CHANNELS],
                             float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(st, in, wet, amount, n, ROTARY_LINEAR_AM);
}
static void rotary_wide_center(void *st, const float *const in[FX_CHANNELS],
                               float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(st, in, wet, amount, n, ROTARY_WIDE_CENTER);
}
static void rotary_wide_center_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    rotary_run(st, in, NULL, ZERO_AMOUNT, n, ROTARY_WIDE_CENTER);
}
static void rotary_no_directivity(void *st, const float *const in[FX_CHANNELS],
                                  float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    rotary_run(st, in, wet, amount, n, ROTARY_NO_DIRECTIVITY);
}
static void wah_no_flush(void *st, const float *const in[FX_CHANNELS],
                         float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    wah_run(st, in, wet, amount, n, WAH_NO_FLUSH);
}
static void wah_no_flush_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    wah_run(st, in, NULL, ZERO_AMOUNT, n, WAH_NO_FLUSH);
}
static void wah_no_sweep(void *st, const float *const in[FX_CHANNELS],
                         float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    wah_run(st, in, wet, amount, n, WAH_NO_SWEEP);
}
static void wah_runaway(void *st, const float *const in[FX_CHANNELS],
                        float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    wah_run(st, in, wet, amount, n, WAH_RUNAWAY);
}
static void wah_runaway_feed(void *st, const float *const in[FX_CHANNELS], uint32_t n)
{
    wah_run(st, in, NULL, ZERO_AMOUNT, n, WAH_RUNAWAY);
}
static void wah_whistle(void *st, const float *const in[FX_CHANNELS],
                        float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    wah_run(st, in, wet, amount, n, WAH_WHISTLE);
}
static void wah_slow_attack(void *st, const float *const in[FX_CHANNELS],
                            float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    wah_run(st, in, wet, amount, n, WAH_SLOW_ATTACK);
}

float fx_test_rotary_speed(const void *state, int rotor)
{
    const rotary_state *s = state;
    return rotor == 0 ? s->horn_hz : s->drum_hz;
}
float fx_test_lfo_sine(float phase)
{
    return lfo_sine(phase);
}
float fx_test_lfo_rounded(float phase)
{
    return lfo_shape(phase);
}

const fx_effect_desc FX_TEST_MUTANTS[FX_MUT_COUNT] = {
    [FX_MUT_DRIVE_NO_OVERSAMPLING] = { "drive-no-os", 1.0f, 0.05f, DRIVE_LEVEL, sizeof(drive_state),
                                       drive_init, drive_no_os, drive_feed, NULL },
    [FX_MUT_DRIVE_NO_CEILING] = { "drive-no-ceiling", 1.0f, 0.05f, DRIVE_LEVEL, sizeof(drive_state),
                                  drive_init, drive_no_ceiling, drive_feed, NULL },
    [FX_MUT_DRIVE_NO_MAKEUP] = { "drive-no-makeup", 1.0f, 0.05f, DRIVE_LEVEL, sizeof(drive_state),
                                 drive_init, drive_no_makeup, drive_feed, NULL },
    [FX_MUT_DRIVE_OLD_SHAPER] = { "drive-old-shaper", 1.0f, 0.05f, DRIVE_LEVEL, sizeof(drive_state),
                                  drive_init, drive_old_shaper, drive_feed, NULL },
    [FX_MUT_DRIVE_ROUND3_BIAS] = { "drive-round3-bias", 1.0f, 0.05f, DRIVE_LEVEL,
                                   sizeof(drive_state), drive_init, drive_round3_bias,
                                   drive_feed, NULL },
    [FX_MUT_DRIVE_NO_FLUSH] = { "drive-no-flush", 1.0f, 0.05f, DRIVE_LEVEL, sizeof(drive_state),
                                drive_init, drive_no_flush, drive_no_flush_feed, NULL },
    [FX_MUT_PHASER_NO_FLUSH] = { "phaser-no-flush", PHASER_WET, 1.0f, PHASER_LEVEL, sizeof(phaser_state),
                                 phaser_init, phaser_no_flush, phaser_no_flush_feed, NULL },
    [FX_MUT_PHASER_HOT_FEEDBACK] = { "phaser-hot", PHASER_WET, 1.0f, PHASER_LEVEL, sizeof(phaser_state),
                                     phaser_init, phaser_hot, phaser_feed, NULL },
    [FX_MUT_PHASER_RUNAWAY] = { "phaser-runaway", PHASER_WET, 1.0f, PHASER_LEVEL, sizeof(phaser_state),
                                phaser_init, phaser_runaway, phaser_runaway_feed, NULL },
    [FX_MUT_FLANGER_NO_FLUSH] = { "flanger-no-flush", FLANGER_WET, 1.0f, FLANGER_LEVEL, sizeof(flanger_state),
                                  flanger_init, flanger_no_flush, flanger_no_flush_feed, NULL },
    [FX_MUT_FLANGER_HOT_FEEDBACK] = { "flanger-hot", FLANGER_WET, 1.0f, FLANGER_LEVEL, sizeof(flanger_state),
                                      flanger_init, flanger_hot, flanger_feed, NULL },
    [FX_MUT_FLANGER_RUNAWAY] = { "flanger-runaway", FLANGER_WET, 1.0f, FLANGER_LEVEL, sizeof(flanger_state),
                                 flanger_init, flanger_runaway, flanger_runaway_feed, NULL },
    [FX_MUT_TREMOLO_OVERSHOOT] = { "tremolo-overshoot", 1.0f, 1.0f, TREMOLO_LEVEL, sizeof(tremolo_state),
                                   tremolo_init, tremolo_overshoot, tremolo_feed, NULL },
    [FX_MUT_TREMOLO_SHALLOW_TOP] = { "tremolo-shallow-top", 1.0f, 1.0f, TREMOLO_LEVEL,
                                     sizeof(tremolo_state), tremolo_init, tremolo_shallow_top,
                                     tremolo_feed, NULL },
    [FX_MUT_TREMOLO_ROUNDED_TRI] = { "tremolo-rounded-triangle", 1.0f, 1.0f, TREMOLO_LEVEL,
                                     sizeof(tremolo_state), tremolo_init, tremolo_rounded,
                                     tremolo_feed, NULL },
    [FX_MUT_TREMOLO_NO_FLOOR] = { "tremolo-no-floor", 1.0f, 1.0f, TREMOLO_LEVEL,
                                  sizeof(tremolo_state), tremolo_init, tremolo_no_floor,
                                  tremolo_feed, NULL },
    [FX_MUT_ROTARY_NO_FLUSH] = { "rotary-no-flush", 1.0f, ROTARY_MIX_FULL, ROTARY_LEVEL,
                                 sizeof(rotary_state), rotary_init,
                                 rotary_no_flush, rotary_no_flush_feed, NULL },
    [FX_MUT_ROTARY_NO_DOPPLER] = { "rotary-no-doppler", 1.0f, ROTARY_MIX_FULL, ROTARY_LEVEL,
                                   sizeof(rotary_state), rotary_init,
                                   rotary_no_doppler, rotary_feed, NULL },
    [FX_MUT_ROTARY_NO_INERTIA] = { "rotary-no-inertia", 1.0f, ROTARY_MIX_FULL, ROTARY_LEVEL,
                                   sizeof(rotary_state), rotary_init,
                                   rotary_no_inertia, rotary_feed, NULL },
    [FX_MUT_ROTARY_LINEAR_AM] = { "rotary-linear-am", 1.0f, ROTARY_MIX_FULL, ROTARY_LEVEL,
                                  sizeof(rotary_state), rotary_init, rotary_linear_am,
                                  rotary_feed, NULL },
    [FX_MUT_ROTARY_WIDE_CENTER] = { "rotary-1ms-centre", 1.0f, ROTARY_MIX_FULL, ROTARY_LEVEL,
                                    sizeof(rotary_state), rotary_init, rotary_wide_center,
                                    rotary_wide_center_feed, NULL },
    [FX_MUT_ROTARY_NO_DIRECTIVITY] = { "rotary-no-directivity", 1.0f, ROTARY_MIX_FULL,
                                       ROTARY_LEVEL, sizeof(rotary_state), rotary_init,
                                       rotary_no_directivity, rotary_feed, NULL },
    [FX_MUT_WAH_NO_FLUSH] = { "auto-wah-no-flush", WAH_WET, 1.0f, WAH_LEVEL, sizeof(wah_state),
                              wah_init, wah_no_flush, wah_no_flush_feed, NULL },
    [FX_MUT_WAH_NO_SWEEP] = { "auto-wah-no-sweep", WAH_WET, 1.0f, WAH_LEVEL, sizeof(wah_state),
                              wah_init, wah_no_sweep, wah_feed, NULL },
    [FX_MUT_WAH_RUNAWAY] = { "auto-wah-runaway", WAH_WET, 1.0f, WAH_LEVEL, sizeof(wah_state),
                             wah_init, wah_runaway, wah_runaway_feed, NULL },
    [FX_MUT_WAH_WHISTLE] = { "auto-wah-whistle", WAH_WET, 1.0f, WAH_LEVEL, sizeof(wah_state),
                             wah_init, wah_whistle, wah_feed, NULL },
    [FX_MUT_WAH_SLOW_ATTACK] = { "auto-wah-slow-attack", WAH_WET, 1.0f, WAH_LEVEL,
                                 sizeof(wah_state), wah_init, wah_slow_attack, wah_feed, NULL },
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
int fx_test_no_top;
int fx_test_level_hot;
int fx_test_level_cold;
int fx_test_level_wobble;
int fx_test_reverse_order;
int fx_test_no_makeup;
int fx_test_makeup_unsmoothed;
int fx_test_gain_off;
int fx_test_gain_no_ramp;
int fx_test_gain_post_chain;
int fx_test_gain_unclamped;
int fx_test_gain_start_ramps;
int fx_test_gain_twice;
int fx_test_gain_no_snap;
static uint32_t g_wobble_t;
#endif

/* The chain's makeup G(m) (fx_dsp.h, "Makeup"), tabulated over m. Startup
 * only: it uses pow. Exactly 1 at m = 0. */
static void makeup_table(fx_engine *e)
{
    for (int j = 0; j <= FX_LEVEL_POINTS; j++) {
        double m = (double)j / FX_LEVEL_POINTS;
        e->makeup[j] = j == 0 ? 1.0f : (float)pow(10.0, FX_MAKEUP_DB * m / 20.0);
    }
}

static inline float makeup_at(const fx_engine *e, float m)
{
#ifdef FX_SELFTEST
    if (fx_test_no_makeup) {
        return 1.0f;
    }
#endif
    float pos = m * (float)FX_LEVEL_POINTS;
    int j = (int)pos;
    if (j >= FX_LEVEL_POINTS) {
        j = FX_LEVEL_POINTS - 1;
    }
    float frac = pos - (float)j;
    return e->makeup[j] + frac * (e->makeup[j + 1] - e->makeup[j]);
}

/* ------------------------------------------------------------------------- */
/* Bass bypass (fx_dsp.h, "Bass bypass")                                      */
/* ------------------------------------------------------------------------- */

#ifdef FX_SELFTEST
int fx_test_bass_split_at_depth0;
int fx_test_bass_lr2;
int fx_test_bass_unsmoothed;
int fx_test_bass_stereo;
int fx_test_bass_low_left;
int fx_test_bass_no_park;
int fx_test_bass_drive_outside;
int fx_test_bass_no_corr;
const float *fx_test_bass_fit[FX_MAX_EFFECTS];
#endif

#ifndef FX_BASS_CHAIN_SHARE
#define FX_BASS_CHAIN_SHARE 0.85f
#endif
#define BASS_K 1.41421356f   /* 1/Q, Butterworth: two of them in series are LR4 */

/* The TPT SVF's coefficients for the crossover in force. Once per chunk while
 * it glides (tanf), never per sample. */
static void bass_coefs(fx_engine *e)
{
    float hz = e->bass_hz;
    if (hz > 0.45f * e->rate) {
        hz = 0.45f * e->rate;
    }
    const float g = tanf((float)M_PI * hz / e->rate);
    e->bass_a1 = 1.0f / (1.0f + g * (g + BASS_K));
    e->bass_a2 = g * e->bass_a1;
    e->bass_a3 = g * e->bass_a2;
    /* The level correction at this crossover: linear in log frequency
     * between grid points. */
    int j = 0;
    while (j < FX_BASS_GRID - 2 && e->bass_hz > FX_BASS_GRID_HZ[j + 1]) {
        j++;
    }
    float t = logf(e->bass_hz / FX_BASS_GRID_HZ[j]) / logf(FX_BASS_GRID_HZ[j + 1] / FX_BASS_GRID_HZ[j]);
    t = t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
    for (int k = 0; k < e->count; k++) {
        const float *loss = e->table[k].bass_loss;
#ifdef FX_SELFTEST
        if (fx_test_bass_fit[k] != NULL) {
            loss = fx_test_bass_fit[k];
        }
#endif
        float db = 0.0f;
        if (loss != NULL) {
            db = loss[j] + t * (loss[j + 1] - loss[j]);
        }
#ifdef FX_SELFTEST
        if (fx_test_bass_no_corr) {
            db = 0.0f;
        }
#endif
        e->bass_corr[k] = powf(10.0f, -db / 20.0f);
    }
}

/* One sample through one section: its low- and high-pass outputs. */
static inline void svf_tick(fx_svf *f, const fx_engine *e, float v0, float *lp, float *hp)
{
    const float v3 = v0 - f->ic2;
    const float v1 = e->bass_a1 * f->ic1 + e->bass_a2 * v3;
    const float v2 = f->ic2 + e->bass_a2 * f->ic1 + e->bass_a3 * v3;
    f->ic1 = flush(2.0f * v1 - f->ic1);
    f->ic2 = flush(2.0f * v2 - f->ic2);
    *lp = v2;
    *hp = v0 - BASS_K * v1 - v2;
}

static void bass_reset(fx_engine *e)
{
    memset(e->bass_first, 0, sizeof(e->bass_first));
    memset(&e->bass_lo, 0, sizeof(e->bass_lo));
    memset(&e->bass_lo_r, 0, sizeof(e->bass_lo_r));
    memset(e->bass_hi, 0, sizeof(e->bass_hi));
}

/* The crossover's glide toward the target, one step per chunk. Off holds the
 * crossover where it is: the fade-out plays at the split it had. */
static void bass_glide(fx_engine *e, float target)
{
    if (!(target > 0.0f) || e->bass_hz == target) {
        return;
    }
    float hz = e->bass_hz + e->bass_glide * (target - e->bass_hz);
#ifdef FX_SELFTEST
    if (fx_test_bass_unsmoothed) {
        hz = target;
    }
#endif
    if (fabsf(target - hz) <= 0.01f || hz == e->bass_hz) {
        hz = target;
    }
    e->bass_hz = hz;
    bass_coefs(e);
}

/* One frame of the split: x (the two channels, in place) becomes the chain's
 * input x + b (hi - x), and lo[ch] the low band times b to add back after the
 * chain: the same mono low band on both channels. */
static inline void bass_frame(fx_engine *e, float *l, float *r, float b, float lo[FX_CHANNELS])
{
    float lp_l, hp_l, lp_r, hp_r, low, hi_l, hi_r, unused;
    svf_tick(&e->bass_first[0], e, finite_or_zero(*l), &lp_l, &hp_l);
    svf_tick(&e->bass_first[1], e, finite_or_zero(*r), &lp_r, &hp_r);
#ifdef FX_SELFTEST
    if (fx_test_bass_lr2) {
        /* One Butterworth section per band: LP + HP sum with a notch at the
         * corner. */
        lo[0] = lo[1] = b * 0.5f * (lp_l + lp_r);
        *l += b * (hp_l - *l);
        *r += b * (hp_r - *r);
        return;
    }
    if (fx_test_bass_low_left) {
        /* Mutant: both channels' lows from L only. */
        svf_tick(&e->bass_lo, e, lp_l, &low, &unused);
        svf_tick(&e->bass_hi[0], e, hp_l, &unused, &hi_l);
        svf_tick(&e->bass_hi[1], e, hp_r, &unused, &hi_r);
        lo[0] = lo[1] = b * low;
        *l += b * (hi_l - *l);
        *r += b * (hi_r - *r);
        return;
    }
    if (FX_BASS_LOW_MONO ? fx_test_bass_stereo : !fx_test_bass_stereo) {
#else
    if (!FX_BASS_LOW_MONO) {
#endif
        /* Each channel keeps its own low band. */
        float low_r;
        svf_tick(&e->bass_lo, e, lp_l, &low, &unused);
        svf_tick(&e->bass_lo_r, e, lp_r, &low_r, &unused);
        svf_tick(&e->bass_hi[0], e, hp_l, &unused, &hi_l);
        svf_tick(&e->bass_hi[1], e, hp_r, &unused, &hi_r);
        lo[0] = b * low;
        lo[1] = b * low_r;
        *l += b * (hi_l - *l);
        *r += b * (hi_r - *r);
        return;
    }
    svf_tick(&e->bass_lo, e, 0.5f * (lp_l + lp_r), &low, &unused);
    svf_tick(&e->bass_hi[0], e, hp_l, &unused, &hi_l);
    svf_tick(&e->bass_hi[1], e, hp_r, &unused, &hi_r);
    lo[0] = lo[1] = b * low;
    *l += b * (hi_l - *l);
    *r += b * (hi_r - *r);
}

void fx_engine_set_bass(fx_engine *e, float hz)
{
    if (!(hz > 0.0f)) {        /* also NaN */
        e->bass_target = 0.0f;
        return;
    }
    e->bass_target = hz < FX_BASS_MIN_HZ ? FX_BASS_MIN_HZ : (hz > FX_BASS_MAX_HZ ? FX_BASS_MAX_HZ : hz);
}

/* The static level curve (fx_dsp.h, "Level curve"), tabulated over depth.
 * Startup and rate change only: it uses pow. */
static void level_table(fx_engine *e, int k)
{
    const fx_effect_desc *d = &e->table[k];
    for (int j = 0; j <= FX_LEVEL_POINTS; j++) {
        double depth = (double)j / FX_LEVEL_POINTS;
        double w = (double)d->wet_max * fmin(1.0, depth / (double)d->mix_full_at);
        const fx_level_fit *f = &d->level;
        double ms = (1.0 - w) * (1.0 - w) + w * w * ((double)f->p0 + (double)f->p1 * depth) +
                    2.0 * w * (1.0 - w) * ((double)f->c0 + (double)f->c1 * depth);
        double g = fx_level_fit_on(f) ? pow(fmax(ms, FX_LEVEL_MS_MIN), -0.5) : 1.0;
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
    if (fx_test_level_cold && d > 0.0f) {
        g *= 0.9441f;   /* -0.5 dB */
    }
    if (fx_test_level_wobble && fx_level_fit_on(&e->table[k].level)) {
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
    /* The stage gain keeps its value across a rate change: only its ramp's
     * coefficient follows the rate. */
    e->gain_alpha = one_pole_alpha(FX_GAIN_MS, rate);
    e->xfade_step = xfade_step_for(e->xfade_ms, rate);
    e->bass_glide = (float)(1.0 - exp(-(double)FX_CHUNK / ((FX_BASS_GLIDE_MS / 1000.0) * (double)rate)));
    bass_reset(e);
    e->bass_warm = 0;
    e->bass_mix = 0.0f;
    if (!(e->bass_hz > 0.0f)) {
        e->bass_hz = 120.0f;   /* any valid corner: replaced before it is heard */
    }
    bass_coefs(e);
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
    e->gain = 1.0f;
    e->gain_target = 1.0f;
    e->gain_max = (float)pow(10.0, FX_GAIN_MAX_DB / 20.0);
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
    /* The bass split comes before the first stage inside it: the drive, if
     * it runs first and is outside (FX_BASS_DRIVE_INSIDE 0), runs before it. */
    int drive_inside = FX_BASS_DRIVE_INSIDE;
#ifdef FX_SELFTEST
    if (fx_test_bass_drive_outside) {
        drive_inside = 0;
    }
#endif
    e->bass_split_at = 0;
    if (!drive_inside && n > 0 && strcmp(table[e->order[0]].name, "drive") == 0) {
        e->bass_split_at = 1;
    }
    makeup_table(e);
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

void fx_engine_set_gain(fx_engine *e, float linear)
{
    if (!isfinite(linear) || linear < 0.0f) {
        return;   /* NaN, inf, negative: the target stays as it was */
    }
#ifdef FX_SELFTEST
    if (fx_test_gain_unclamped) {
        e->gain_target = linear;
        return;
    }
#endif
    e->gain_target = linear > e->gain_max ? e->gain_max : linear;
}

void fx_engine_start_gain(fx_engine *e, float linear)
{
    fx_engine_set_gain(e, linear);   /* refuses NaN, inf, negative; clamps */
#ifdef FX_SELFTEST
    if (fx_test_gain_start_ramps) {
        return;
    }
#endif
    e->gain = e->gain_target;
}

/* The stage gain (fx_dsp.h, "Stage gain") over `n` frames of `x`, in place.
 * Settled at unity it touches nothing: the input, bit for bit. */
static inline void gain_stage(fx_engine *e, float x[FX_CHANNELS][FX_CHUNK], uint32_t n)
{
#ifdef FX_SELFTEST
    if (fx_test_gain_off) {
        return;
    }
#endif
    float g = e->gain;
    const float t = e->gain_target;
    if (g == t) {
        if (g == 1.0f) {
            return;
        }
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            for (uint32_t i = 0; i < n; i++) {
                x[ch][i] *= g;
            }
        }
        return;
    }
    for (uint32_t i = 0; i < n; i++) {
        const float next = g + e->gain_alpha * (t - g);
        int land = e->gain_alpha <= 0.0f || fabsf(t - g) <= FX_GAIN_SNAP || next == g;
#ifdef FX_SELFTEST
        if (fx_test_gain_no_ramp) {
            land = 1;
        } else if (fx_test_gain_no_snap) {
            land = e->gain_alpha <= 0.0f;
        }
#endif
        g = land ? t : next;
        x[0][i] *= g;
        x[1][i] *= g;
    }
    e->gain = g;
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
    float makeup_m[FX_CHUNK];   /* the chain's makeup driver m, per frame */
    float bass_lo[FX_CHANNELS][FX_CHUNK];   /* the low band times b, added back */
    float bass_h[FX_CHUNK];   /* the split's level correction - 1, per frame */
    float bass_lo_b[FX_CHUNK];   /* b, per frame */
    float bass_hp_max[FX_CHUNK], bass_hp_sum[FX_CHUNK];   /* the boosts: largest, total */
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
        /* The stage gain, before the chain: everything below sees its output. */
#ifdef FX_SELFTEST
        if (!fx_test_gain_post_chain) {
            gain_stage(e, x_buf, n);
        }
        if (fx_test_gain_twice) {
            /* Applied again with the ramp's last value: twice the dB. */
            const float g2 = e->gain;
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                for (uint32_t i = 0; i < n; i++) {
                    x_buf[ch][i] *= g2;
                }
            }
        }
#else
        gain_stage(e, x_buf, n);
#endif

        /* Any stage enabled or still fading out, at a depth that is not 0. */
        int live = 0;
        for (int k = 0; k < e->count; k++) {
            const int audible = ((mask >> k) & 1u) || e->on[k] > 0.0f;
            live |= audible && !(e->depth[k] == 0.0f && target[k] == 0.0f);
        }

        /* The bass split: wanted while the chain is live with a crossover
         * set; running while wanted or still fading out. */
        const float bass_t = e->bass_target;
        int bass_live = live;
#ifdef FX_SELFTEST
        if (fx_test_bass_split_at_depth0) {
            bass_live = 1;
        }
#endif
        const float bass_want = (bass_t > 0.0f && bass_live) ? 1.0f : 0.0f;
        const int bass_run = e->bass_mix > 0.0f || bass_want > 0.0f;

        if (!live && !bass_run) {
            /* The filters stay warm while an effect is ticked, so the knob
             * coming up from 0 finds them current; otherwise nothing runs. */
            if (bass_t > 0.0f && (mask & ((1u << e->count) - 1u)) != 0u) {
                float park = FX_BASS_PARK_HZ;
#ifdef FX_SELFTEST
                if (fx_test_bass_no_park) {
                    park = bass_t;
                }
#endif
                if (!e->bass_warm) {
                    bass_reset(e);
                    e->bass_hz = park;
                    bass_coefs(e);
                }
                bass_glide(e, park);   /* the split comes in parked */
                for (uint32_t i = 0; i < n; i++) {
                    float l = x_buf[0][i], r = x_buf[1][i], lo[FX_CHANNELS];
                    bass_frame(e, &l, &r, 1.0f, lo);
                }
                e->bass_warm = 1;
            } else {
                e->bass_warm = 0;
            }
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
#ifdef FX_SELFTEST
            if (fx_test_gain_post_chain) {
                gain_stage(e, x_buf, n);
            }
#endif
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

        for (uint32_t i = 0; i < n; i++) {
            makeup_m[i] = 0.0f;
            bass_h[i] = 0.0f;   /* the largest cut */
            bass_hp_max[i] = 0.0f;
            bass_hp_sum[i] = 0.0f;
        }
        if (bass_run && !e->bass_warm) {
            /* Coming in cold: from rest, parked, under the fade. */
            bass_reset(e);
            e->bass_hz = FX_BASS_PARK_HZ;
#ifdef FX_SELFTEST
            if (fx_test_bass_no_park && bass_t > 0.0f) {
                e->bass_hz = bass_t;
            }
#endif
            bass_coefs(e);
        }
        e->bass_warm = bass_run;
        for (int s = 0; s <= e->count; s++) {
            if (bass_run && s == e->bass_split_at) {
                /* b moves only with the corner parked; the corner leaves the
                 * park only once b is 1 (fx_dsp.h, FX_BASS_PARK_HZ). */
                float b = e->bass_mix;
                float hz_goal = (bass_want > 0.0f && b >= 1.0f) ? bass_t : FX_BASS_PARK_HZ;
                float b_goal = e->bass_hz <= FX_BASS_PARKED ? bass_want : b;
#ifdef FX_SELFTEST
                if (fx_test_bass_no_park) {
                    hz_goal = bass_t;
                    b_goal = bass_want;
                }
#endif
                bass_glide(e, hz_goal);
                for (uint32_t i = 0; i < n; i++) {
                    if (b < b_goal) {
                        b = fminf(b_goal, b + e->xfade_step);
                    } else if (b > b_goal) {
                        b = fmaxf(b_goal, b - e->xfade_step);
                    }
#ifdef FX_SELFTEST
                    if (fx_test_bass_unsmoothed) {
                        b = bass_want;
                    }
#endif
                    float lo[FX_CHANNELS];
                    bass_frame(e, &x_buf[0][i], &x_buf[1][i], b, lo);
                    bass_lo[0][i] = lo[0];
                    bass_lo[1][i] = lo[1];
                    bass_lo_b[i] = b;
                }
                e->bass_mix = b;
            }
            if (s == e->count) {
                break;
            }
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
            /* The bass split's level correction (fx_dsp.h, "Bass bypass"):
             * this stage's share, per unit of its mix, the enable ramp and
             * the split's fade; the chain applies the largest. */
            const float corr = bass_run ? (e->bass_corr[k] - 1.0f) / wmax : 0.0f;
            for (uint32_t i = 0; i < n; i++) {
                /* full = 1 (the knob is the mix) leaves d unchanged, bit for bit. */
                const float dk = depth_ramp[i];
                const float w = fminf(1.0f, dk * full) * wmax;
                const float g = level_at(e, k, dk);
                if (corr != 0.0f) {
                    const float h = corr * w * on_ramp[i];
                    if (h > 0.0f) {
                        bass_hp_max[i] = fmaxf(bass_hp_max[i], h);
                        bass_hp_sum[i] += h;
                    } else {
                        bass_h[i] = fminf(bass_h[i], h);
                    }
                }
                const float ck = on_ramp[i];
                for (int ch = 0; ch < FX_CHANNELS; ch++) {
                    const float xs = x_buf[ch][i];
                    const float y = g * (xs + w * (wet_buf[ch][i] - xs));
                    x_buf[ch][i] = ck >= 1.0f ? y : xs + ck * (y - xs);
                }
                /* This stage's effective depth, as heard: smoothed, times
                 * its enable ramp. */
                makeup_m[i] = fmaxf(makeup_m[i], ck * dk);
            }
        }
#ifdef FX_SELFTEST
        if (fx_test_makeup_unsmoothed) {
            /* A makeup that skipped the ramps: it jumps to the targets. */
            float m_t = 0.0f;
            for (int k = 0; k < e->count; k++) {
                if ((mask >> k) & 1u) {
                    m_t = fmaxf(m_t, target[k]);
                }
            }
            for (uint32_t i = 0; i < n; i++) {
                makeup_m[i] = m_t;
            }
        }
#endif
        /* The low band back, under the makeup: the level on keys stays
         * G(m) x the input whatever the split. */
        if (bass_run) {
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                for (uint32_t i = 0; i < n; i++) {
                    /* The high band's correction, faded with the split: the
                     * largest boost, a share of the others, the largest cut. */
                    const float b = bass_lo_b[i];
                    const float up = bass_hp_max[i] +
                                     FX_BASS_CHAIN_SHARE * (bass_hp_sum[i] - bass_hp_max[i]);
                    const float h = (1.0f + b * up) * (1.0f + b * bass_h[i]);
                    x_buf[ch][i] = x_buf[ch][i] * h + bass_lo[ch][i];
                }
            }
        }
        /* The chain's makeup, once, after the last stage. G(0) is exactly 1. */
        for (uint32_t i = 0; i < n; i++) {
            const float gm = makeup_at(e, makeup_m[i]);
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                x_buf[ch][i] *= gm;
            }
        }
#ifdef FX_SELFTEST
        g_wobble_t += n;
        if (fx_test_gain_post_chain) {
            gain_stage(e, x_buf, n);
        }
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

/* The next token is a plain decimal: digits and a point, no sign, exponent,
 * hex, NaN or inf. */
static int plain_decimal(const char *rest)
{
    while (*rest == ' ' || *rest == '\t') {
        rest++;
    }
    for (const char *c = rest; *c != '\0' && *c != ' ' && *c != '\t' && *c != '\n' && *c != '\r'; c++) {
        if ((*c < '0' || *c > '9') && *c != '.') {
            return 0;
        }
    }
    return 1;
}

int fx_parse_bass(const char *text, float *hz)
{
    while (*text == ' ' || *text == '\t') {
        text++;
    }
    if (strncmp(text, "bass", 4) != 0) {
        return 0;
    }
    const char *rest = text + 4;
    if (*rest != ' ' && *rest != '\t') {
        return 0;
    }
    if (!plain_decimal(rest)) {
        return 0;
    }
    float value = 0.0f;
    if (!parse_float(&rest, &value) || !only_blanks(rest) || value < 0.0f) {
        return 0;
    }
    *hz = value;
    return 1;
}

int fx_parse_gain(const char *text, float *linear)
{
    while (*text == ' ' || *text == '\t') {
        text++;
    }
    if (strncmp(text, "gain", 4) != 0) {
        return 0;
    }
    const char *rest = text + 4;
    if (*rest != ' ' && *rest != '\t') {
        return 0;
    }
    if (!plain_decimal(rest)) {
        return 0;
    }
    float value = 0.0f;
    if (!parse_float(&rest, &value) || !only_blanks(rest) || value < 0.0f) {
        return 0;
    }
    *linear = value;
    return 1;
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
