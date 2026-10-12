/*
 * fx_selftest — the listener-protecting properties of fx_dsp, run without JACK.
 *
 *   fx-selftest                 run every check; exit 0 only if all pass
 *   fx-selftest --parse TEXT    print "ok <id> <depth>" or "reject" for one
 *                               datagram, through the client's real parser
 *   fx-selftest --measure       print the level table (RMS out/in per effect,
 *                               knob position and signal), the drive's
 *                               harmonic profile and its aliasing figure
 *   fx-selftest --bench         ns per stereo frame for each effect
 *
 * The discontinuity checks each carry a control: the same run with the
 * protection switched off (no depth smoothing, an instant swap) must FAIL the
 * same criterion. A criterion that the broken build also passes measures
 * nothing, and would read exactly like this one passing.
 *
 * Driven by tests/test_fx_host.py through `make selftest`.
 */

#define _GNU_SOURCE
#include "fx_dsp.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RATE 48000.0f
#define PERIOD 256u
#define DEPTH_MS 40.0
#define XFADE_MS 30.0

static int g_failures;
static int g_verbose;

#define CHECK(cond, ...)                                   \
    do {                                                   \
        if (!(cond)) {                                     \
            g_failures++;                                  \
            fprintf(stderr, "FAIL %s:%d: ", __func__, __LINE__); \
            fprintf(stderr, __VA_ARGS__);                  \
            fputc('\n', stderr);                           \
        }                                                  \
    } while (0)

static uint32_t g_rng = 0x9e3779b9u;
static float noise(void)
{
    g_rng ^= g_rng << 13;
    g_rng ^= g_rng >> 17;
    g_rng ^= g_rng << 5;
    return ((float)(g_rng >> 8) / 8388608.0f) - 1.0f;   /* [-1, 1) */
}

/* A test-only second effect: a polarity flip. At depth 1 with wet_max 0.5 the
 * mix is dry + 0.5(-dry - dry) = 0 — silence against the chorus's full-level
 * output, so an instant swap is a step the size of the signal. */
static int flip_init(void *s, float r) { (void)s; (void)r; return 0; }
static void flip_process(void *s, const float *const in[FX_CHANNELS],
                         float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)s;
    (void)amount;
    for (uint32_t i = 0; i < n; i++) {
        float l = in[0][i];
        float r = in[1][i];
        wet[0][i] = -l;
        wet[1][i] = -r;
    }
}

static fx_effect_desc g_two[2];

static void build_two(void)
{
    g_two[0] = FX_EFFECTS[0];
    g_two[1] = (fx_effect_desc){ "flip", 0.5f, 1.0f, 1.0f, 1, flip_init, flip_process, NULL };
}

static void run_period(fx_engine *e, float *l, float *r, float *ol, float *or_,
                       uint32_t n, int effect, float depth)
{
    const float *const in[FX_CHANNELS] = { l, r };
    float *const out[FX_CHANNELS] = { ol, or_ };
    fx_engine_process(e, in, out, n, effect, depth);
}

static void test_depth_zero_is_the_input(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0,
          "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    size_t mismatches = 0;
    for (int p = 0; p < 2000; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = noise();
            r[i] = noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, 0, 0.0f);
        if (memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0) {
            mismatches++;
        }
        /* In place, as JACK may hand it over. */
        float keep[PERIOD];
        memcpy(keep, l, sizeof(l));
        run_period(&e, l, r, l, r, PERIOD, 0, 0.0f);
        if (memcmp(keep, l, sizeof(l)) != 0) {
            mismatches++;
        }
    }
    CHECK(mismatches == 0, "%zu bypass periods differ from their input", mismatches);

    /* And back to bypass after being in: ramp up, ramp down, then identity. */
    for (int p = 0; p < 200; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = r[i] = noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, 0, p < 100 ? 1.0f : 0.0f);
    }
    for (uint32_t i = 0; i < PERIOD; i++) {
        l[i] = noise();
        r[i] = noise();
    }
    run_period(&e, l, r, ol, or_, PERIOD, 0, 0.0f);
    CHECK(memcmp(l, ol, sizeof(l)) == 0 && memcmp(r, or_, sizeof(r)) == 0,
          "depth 0 after a ramp down is not the input");
    fx_engine_free(&e);
}

/* Can only keep or reduce level: with in-range input of peak A, no output
 * sample exceeds A — at any depth, through any depth jump. Then the clamp:
 * out-of-range, infinite and NaN input never leave ±1.0 or produce NaN. */
/* Largest output sample over 32 s of noise at peak 0.5, depth and effect
 * (chorus, flip) moving. */
static float bounded_peak(fx_engine *e, float amp)
{
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    float peak = 0.0f;
    for (int p = 0; p < 6000; p++) {          /* 32 s */
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = amp * noise();
            r[i] = amp * noise();
        }
        float depth = (p % 37 == 0) ? 0.0f : (float)((p * 7919) % 101) / 100.0f;
        int effect = (p / 300) % 2;
        run_period(e, l, r, ol, or_, PERIOD, effect, depth);
        for (uint32_t i = 0; i < PERIOD; i++) {
            peak = fmaxf(peak, fmaxf(fabsf(ol[i]), fabsf(or_[i])));
        }
    }
    return peak;
}

static void test_output_is_bounded(void)
{
    fx_engine e;
    build_two();
    CHECK(fx_engine_init(&e, g_two, 2, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    const float amp = 0.5f;
    float peak = bounded_peak(&e, amp);
    CHECK(peak <= amp + 1e-6f, "output peak %.7f exceeds input peak %.7f", peak, amp);
    /* Control: the chorus's level stage lifts a half-decorrelated mix by up to
     * 3 dB; without its limiter, that must show here as a peak over the input. */
    fx_test_no_limit = 1;
    float c = bounded_peak(&e, amp);
    fx_test_no_limit = 0;
    CHECK(c > amp + 1e-6f, "control (no limiter) kept the peak too: %.7f", (double)c);
    if (g_verbose) {
        printf("  chorus   peak bound: %.4f for input %.4f; control (no limiter) %.4f\n",
               (double)peak, (double)amp, (double)c);
    }

    size_t bad = 0;
    for (int p = 0; p < 2000; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 4.0f * noise();
            r[i] = (i % 97 == 0) ? NAN : (i % 89 == 0 ? INFINITY : 4.0f * noise());
        }
        run_period(&e, l, r, ol, or_, PERIOD, p % 2, (float)(p % 11) / 10.0f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            if (!isfinite(ol[i]) || !isfinite(or_[i]) || fabsf(ol[i]) > 1.0f ||
                fabsf(or_[i]) > 1.0f) {
                bad++;
            }
        }
    }
    CHECK(bad == 0, "%zu samples non-finite or beyond ±1.0", bad);
    fx_engine_free(&e);
}

/* Two minutes of audio with depth and effect moving, then silence. Nothing may
 * go non-finite or subnormal, and silence in must become exact silence out. */
static void test_long_run_stays_clean(void)
{
    fx_engine e;
    build_two();
    CHECK(fx_engine_init(&e, g_two, 2, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    size_t nonfinite = 0, subnormal = 0, residue = 0;
    const int periods = (int)(120.0f * RATE / PERIOD);
    for (int p = 0; p < periods; p++) {
        float level = (p % 500 < 400) ? 1e-3f * (float)(1 + p % 7) : 1e-30f;
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = level * noise();
            r[i] = level * noise();
        }
        float depth = (float)((p / 50) % 5) / 4.0f;
        run_period(&e, l, r, ol, or_, PERIOD, (p / 1000) % 2, depth);
        for (uint32_t i = 0; i < PERIOD; i++) {
            nonfinite += !isfinite(ol[i]) + !isfinite(or_[i]);
            if (level > 1e-20f) {
                subnormal += (fpclassify(ol[i]) == FP_SUBNORMAL) +
                             (fpclassify(or_[i]) == FP_SUBNORMAL);
            }
        }
    }
    memset(l, 0, sizeof(l));
    memset(r, 0, sizeof(r));
    for (int p = 0; p < 200; p++) {
        run_period(&e, l, r, ol, or_, PERIOD, 0, 0.7f);
        if (p >= 4) {   /* past the longest delay tap */
            for (uint32_t i = 0; i < PERIOD; i++) {
                residue += (ol[i] != 0.0f) + (or_[i] != 0.0f);
            }
        }
    }
    CHECK(nonfinite == 0, "%zu non-finite samples", nonfinite);
    CHECK(subnormal == 0, "%zu subnormal samples from normal-range input", subnormal);
    CHECK(residue == 0, "%zu non-zero samples after silence", residue);
    fx_engine_free(&e);
}

/* Largest sample-to-sample step, whole run vs. settled windows only. A click
 * is a step the signal itself never makes. */
typedef struct {
    float worst;      /* anywhere */
    float settled;    /* outside the transition windows */
} steps;

static steps measure_steps(fx_engine *e, const int *effects, const float *depths, int segments,
                           int periods_per_segment, int settle_periods)
{
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    double phase = 0.0;
    const double inc = 2.0 * M_PI * 220.0 / RATE;
    float prev = 0.0f;
    int have_prev = 0;
    steps s = { 0.0f, 0.0f };
    for (int seg = 0; seg < segments; seg++) {
        for (int p = 0; p < periods_per_segment; p++) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                l[i] = r[i] = 0.5f * (float)sin(phase);
                phase += inc;
            }
            run_period(e, l, r, ol, or_, PERIOD, effects[seg], depths[seg]);
            for (uint32_t i = 0; i < PERIOD; i++) {
                if (have_prev) {
                    float step = fabsf(ol[i] - prev);
                    s.worst = fmaxf(s.worst, step);
                    if (p >= settle_periods) {
                        s.settled = fmaxf(s.settled, step);
                    }
                }
                prev = ol[i];
                have_prev = 1;
            }
        }
    }
    return s;
}

static int click_free(steps s)
{
    return s.worst <= s.settled * 1.25f + 1e-4f;
}

static void test_depth_step_has_no_click(void)
{
    const int effects[] = { 0, 0, 0, 0, 0 };
    const float depths[] = { 0.0f, 1.0f, 0.0f, 0.5f, 1.0f };
    fx_engine e;

    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    steps s = measure_steps(&e, effects, depths, 5, 190, 60);
    CHECK(click_free(s), "depth steps: worst %.5f vs settled %.5f", s.worst, s.settled);
    fx_engine_free(&e);

    /* Control: no smoothing must produce a click this criterion sees. */
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 0.0, XFADE_MS) == 0, "init");
    steps c = measure_steps(&e, effects, depths, 5, 190, 60);
    CHECK(!click_free(c), "control (unsmoothed) passed too: worst %.5f vs settled %.5f — "
                          "the criterion cannot see a click", c.worst, c.settled);
    fx_engine_free(&e);
}

static void test_swap_crossfades(void)
{
    const int effects[] = { 0, 1, 0, 1 };
    const float depths[] = { 1.0f, 1.0f, 1.0f, 1.0f };
    fx_engine e;
    build_two();

    CHECK(fx_engine_init(&e, g_two, 2, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    steps s = measure_steps(&e, effects, depths, 4, 190, 60);
    CHECK(click_free(s), "swap: worst %.5f vs settled %.5f", s.worst, s.settled);
    CHECK(e.active == 1 && e.incoming == -1, "swap did not land (active %d, incoming %d)",
          e.active, e.incoming);
    fx_engine_free(&e);

    CHECK(fx_engine_init(&e, g_two, 2, RATE, DEPTH_MS, 0.0) == 0, "init");
    steps c = measure_steps(&e, effects, depths, 4, 190, 60);
    CHECK(!click_free(c), "control (instant swap) passed too: worst %.5f vs settled %.5f",
          c.worst, c.settled);
    fx_engine_free(&e);
}

/* Bypass keeps writing the delay line. Coming back in must read the audio
 * that went past while bypassed, not silence or whatever was there before. */
static void test_bypass_keeps_history(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    double phase = 0.0;
    const double inc = 2.0 * M_PI * 220.0 / RATE;
    for (int p = 0; p < 200; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = r[i] = 0.5f * (float)sin(phase);
            phase += inc;
        }
        run_period(&e, l, r, ol, or_, PERIOD, 0, 0.0f);
    }
    float wl[FX_CHUNK], wr[FX_CHUNK];
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    static const float zero[FX_CHUNK];
    FX_EFFECTS[0].process(e.state[0], in, wet, zero, FX_CHUNK);
    float peak = 0.0f;
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        peak = fmaxf(peak, fmaxf(fabsf(wl[i]), fabsf(wr[i])));
    }
    /* 64 frames of a 220 Hz sine span ~105 degrees, so the delayed copy must
     * reach well past half its amplitude somewhere in the chunk. */
    CHECK(peak > 0.3f, "re-entry read %.4f — the delay line was not fed in bypass", peak);
    fx_engine_free(&e);
}

static void test_rate_limits(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    CHECK(fx_engine_set_rate(&e, 44100.0f) == 0, "44.1 kHz refused");
    CHECK(fx_engine_set_rate(&e, (float)FX_MAX_RATE) == 0, "max rate refused");
    CHECK(fx_engine_set_rate(&e, (float)FX_MAX_RATE * 2.0f) != 0,
          "a rate beyond the delay line was accepted");
    CHECK(e.rate == (float)FX_MAX_RATE, "a refused rate changed the engine");
    fx_engine_free(&e);
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, 384000.0f, DEPTH_MS, XFADE_MS) != 0,
          "init accepted a rate beyond the delay line");
}

/* ========================================================================= */
/* The starter pack: drive (1), phaser (2), tremolo (3)                       */
/* ========================================================================= */
/*
 * The same battery as the chorus, per effect, each check paired with a
 * deliberately broken run it must fail. The broken effects are FX_TEST_MUTANTS
 * (fx_dsp.c, compiled only with FX_SELFTEST) or built here from the shipped
 * descriptor (feed removed, wet made 0.5 dB hotter).
 */

#define N_FX 4
static const char *const FX_LABEL[N_FX] = { "chorus", "drive", "phaser", "tremolo" };

/* The shipped table with entry `k` replaced. */
static fx_effect_desc g_table[N_FX];
static const fx_effect_desc *table_with(int k, const fx_effect_desc *replacement)
{
    for (int i = 0; i < N_FX; i++) {
        g_table[i] = FX_EFFECTS[i];
    }
    if (k >= 0) {
        g_table[k] = *replacement;
    }
    return g_table;
}

/* +0.5 dB on the wet signal of whichever effect is wrapped. */
static const fx_effect_desc *g_hot_inner;
static void hot_process(void *s, const float *const in[FX_CHANNELS],
                        float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    g_hot_inner->process(s, in, wet, amount, n);
    for (int ch = 0; ch < FX_CHANNELS; ch++) {
        for (uint32_t i = 0; i < n; i++) {
            wet[ch][i] *= 1.0593f;
        }
    }
}
static fx_effect_desc hot_version(int k)
{
    g_hot_inner = &FX_EFFECTS[k];
    fx_effect_desc d = FX_EFFECTS[k];
    d.process = hot_process;
    return d;
}

/* A test-only effect with a leaky memory and no flush — the instrument check
 * for the long run: it must see the subnormals and the residue this leaves. */
typedef struct { float z[FX_CHANNELS]; } leaky_state;
static int leaky_init(void *s, float r) { (void)s; (void)r; return 0; }
static void leaky_run(void *st, const float *const in[FX_CHANNELS],
                      float *const wet[FX_CHANNELS], uint32_t n)
{
    leaky_state *s = st;
    for (uint32_t i = 0; i < n; i++) {
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            float x = in[ch][i];
            s->z[ch] = 0.995f * s->z[ch] + 0.005f * (x == x ? x : 0.0f);
            if (wet != NULL) {
                wet[ch][i] = s->z[ch];
            }
        }
    }
}
static void leaky_process(void *s, const float *const in[FX_CHANNELS],
                          float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)amount;
    leaky_run(s, in, wet, n);
}
static void leaky_feed(void *s, const float *const in[FX_CHANNELS], uint32_t n)
{
    leaky_run(s, in, NULL, n);
}
static const fx_effect_desc LEAKY = { "leaky", 1.0f, 1.0f, 1.0f, sizeof(leaky_state),
                                      leaky_init, leaky_process, leaky_feed };

/* --- signals ------------------------------------------------------------- */

enum { SIG_NOISE, SIG_SINE, SIG_KEYS };
typedef struct {
    int kind;
    float amp;
    float hz;
    double phase;
} signal_gen;

/* A keys-like signal: four chords, each struck and left to decay, 0.6 s apart
 * and looping every 2.4 s. Each note has eight harmonics falling as 1/h^1.2,
 * the upper ones decaying faster, a 4 ms attack, and a fixed pan, so the two
 * channels are related but not identical — closer to a Surge keys patch than
 * a sine or white noise is. Built once (it needs sin and exp per sample);
 * `amp` scales a buffer whose peak is 1. */
#define KEYS_CHORD_S 0.6
#define KEYS_CHORDS 4
#define KEYS_LEN 115200u   /* KEYS_CHORD_S x KEYS_CHORDS x 48 kHz */
static float g_keys[FX_CHANNELS][KEYS_LEN];
static int g_keys_ready;

static void keys_build(void)
{
    static const double chords[KEYS_CHORDS][4] = {
        { 130.81, 261.63, 329.63, 392.00 },   /* C */
        { 110.00, 220.00, 261.63, 329.63 },   /* Am */
        { 87.31, 261.63, 349.23, 440.00 },    /* F */
        { 98.00, 246.94, 293.66, 349.23 },    /* G7 */
    };
    static const double pan[4] = { 0.0, -0.4, 0.3, 0.5 };   /* -1 left .. 1 right */
    const uint32_t chord_len = (uint32_t)(KEYS_CHORD_S * RATE);
    float peak = 0.0f;
    for (uint32_t t = 0; t < KEYS_LEN; t++) {
        int c = (int)(t / chord_len);
        double ts = (double)(t % chord_len) / RATE;
        /* The previous chord's tail rings under the new strike. */
        double ts_prev = ts + KEYS_CHORD_S;
        int cp = (c + KEYS_CHORDS - 1) % KEYS_CHORDS;
        double acc[FX_CHANNELS] = { 0.0, 0.0 };
        for (int pass = 0; pass < 2; pass++) {
            int cc = pass == 0 ? c : cp;
            double tt = pass == 0 ? ts : ts_prev;
            double attack = tt < 0.004 ? tt / 0.004 : 1.0;
            for (int n = 0; n < 4; n++) {
                double v = 0.0;
                for (int h = 1; h <= 8; h++) {
                    double env = exp(-tt * (1.8 + 0.6 * h));
                    v += env * pow((double)h, -1.2) *
                         sin(2.0 * M_PI * chords[cc][n] * h * (double)t / RATE + 0.7 * n * h);
                }
                v *= attack;
                acc[0] += v * (1.0 - 0.5 * pan[n]);
                acc[1] += v * (1.0 + 0.5 * pan[n]);
            }
        }
        for (int ch = 0; ch < FX_CHANNELS; ch++) {
            g_keys[ch][t] = (float)acc[ch];
            peak = fmaxf(peak, fabsf((float)acc[ch]));
        }
    }
    for (int ch = 0; ch < FX_CHANNELS; ch++) {
        for (uint32_t t = 0; t < KEYS_LEN; t++) {
            g_keys[ch][t] /= peak;
        }
    }
    g_keys_ready = 1;
}

static void sig_fill(signal_gen *g, float *l, float *r, uint32_t n)
{
    if (g->kind == SIG_KEYS && !g_keys_ready) {
        keys_build();
    }
    for (uint32_t i = 0; i < n; i++) {
        if (g->kind == SIG_NOISE) {
            l[i] = g->amp * noise();
            r[i] = g->amp * noise();
        } else if (g->kind == SIG_KEYS) {
            uint32_t t = (uint32_t)g->phase;
            l[i] = g->amp * g_keys[0][t];
            r[i] = g->amp * g_keys[1][t];
            g->phase = (double)((t + 1u) % KEYS_LEN);
        } else {
            l[i] = r[i] = g->amp * (float)sin(g->phase);
            g->phase += 2.0 * M_PI * g->hz / RATE;
            if (g->phase > 2.0 * M_PI) {
                g->phase -= 2.0 * M_PI;
            }
        }
    }
}

/* --- 1. bypass identity -------------------------------------------------- */

/* Periods (of 300) whose output differs from the input at `depth`, run as the
 * client runs it: separate buffers, then in place. */
static size_t bypass_mismatches(int k, float depth)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD], keep_l[PERIOD], keep_r[PERIOD];
    size_t mismatches = 0;
    for (int p = 0; p < 300; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.5f * noise();
            r[i] = 0.5f * noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, k, depth);
        mismatches += memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0;
        memcpy(keep_l, l, sizeof(l));
        memcpy(keep_r, r, sizeof(r));
        run_period(&e, l, r, l, r, PERIOD, k, depth);
        mismatches += memcmp(keep_l, l, sizeof(l)) != 0 || memcmp(keep_r, r, sizeof(r)) != 0;
    }
    fx_engine_free(&e);
    return mismatches;
}

static void test_pack_bypass_identity(int k)
{
    size_t m = bypass_mismatches(k, 0.0f);
    CHECK(m == 0, "%s: %zu bypass periods differ from their input", FX_LABEL[k], m);
    /* Control: the smallest depth the knob can send (1/127) must be visible. */
    size_t c = bypass_mismatches(k, 1.0f / 127.0f);
    CHECK(c > 0, "%s: control (depth 1/127) also identical — the comparison is blind",
          FX_LABEL[k]);
}

/* --- 2. level bound ------------------------------------------------------ */

/* Fixed margin on the RMS bound: 0.1 dB. */
#define RMS_MARGIN 1.0116f
/* The lower bound: -1 dB. */
#define RMS_FLOOR 0.8913f

#define N_LEVEL_SIGNALS 8
enum { SIG_I_KEYS = 7 };
#define MASK_ALL ((1u << N_LEVEL_SIGNALS) - 1u)
#define MASK_KEYS (1u << SIG_I_KEYS)
static const signal_gen LEVEL_SIGNALS[N_LEVEL_SIGNALS] = {
    { SIG_NOISE, 0.5f, 0.0f, 0.0 },
    { SIG_NOISE, 0.05f, 0.0f, 0.0 },
    { SIG_SINE, 0.5f, 220.0f, 0.0 },
    { SIG_SINE, 0.5f, 80.0f, 0.0 },
    { SIG_SINE, 0.3f, 3000.0f, 0.0 },
    { SIG_SINE, 0.02f, 220.0f, 0.0 },
    { SIG_SINE, 0.9f, 440.0f, 0.0 },
    [SIG_I_KEYS] = { SIG_KEYS, 0.5f, 0.0f, 0.0 },
};
static const char *const LEVEL_SIGNAL_LABEL[N_LEVEL_SIGNALS] = {
    "noise.5", "noise.05", "sin220", "sin80", "sin3k", "sin220q", "sin440h", "keys",
};

/* RMS and peak out/in for one effect, one signal, one knob position: 0.4 s to
 * settle, then 1.2 s measured, or 2.4 s (one whole loop) for the keys. */
static void level_at(const fx_effect_desc *table, int k, int sgi, float depth, float *rms,
                     float *peak)
{
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    signal_gen g = LEVEL_SIGNALS[sgi];
    double sum_in = 0.0, sum_out = 0.0;
    float peak_in = 0.0f, peak_out = 0.0f;
    const int settle = (int)(0.4f * RATE / PERIOD);
    const int measure = (int)((g.kind == SIG_KEYS ? 2.4f : 1.2f) * RATE / PERIOD);
    for (int p = 0; p < settle + measure; p++) {
        sig_fill(&g, l, r, PERIOD);
        run_period(&e, l, r, ol, or_, PERIOD, k, depth);
        for (uint32_t i = 0; i < PERIOD; i++) {
            peak_in = fmaxf(peak_in, fmaxf(fabsf(l[i]), fabsf(r[i])));
            if (p >= settle) {
                sum_in += (double)l[i] * l[i] + (double)r[i] * r[i];
                sum_out += (double)ol[i] * ol[i] + (double)or_[i] * or_[i];
                peak_out = fmaxf(peak_out, fmaxf(fabsf(ol[i]), fabsf(or_[i])));
            }
        }
    }
    fx_engine_free(&e);
    *rms = (float)sqrt(sum_out / sum_in);
    *peak = peak_out / peak_in;
}

typedef struct {
    float rms_max;      /* worst out/in, upward */
    float peak_max;
    float at_depth;
    int at_signal;
    float rms_min[N_LEVEL_SIGNALS];   /* worst out/in, downward, per signal */
    float min_depth[N_LEVEL_SIGNALS];
    float floor_margin_db;            /* vs `floor`, if given: worst measured - floor */
} level_result;

/* `steps` + 1 knob positions from 0 to 1, on the signals in `mask`. */
static level_result level_sweep(const fx_effect_desc *table, int k, unsigned mask, int steps,
                                double (*floor_db)(float depth))
{
    level_result res = { 0.0f, 0.0f, 0.0f, -1, { 0 }, { 0 }, 1e9f };
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        res.rms_min[sgi] = 1e9f;
        if (!(mask & (1u << sgi))) {
            continue;
        }
        for (int step = 0; step <= steps; step++) {
            float depth = (float)step / (float)steps;
            float rr, pr;
            level_at(table, k, sgi, depth, &rr, &pr);
            if (rr > res.rms_max) {
                res.rms_max = rr;
                res.at_depth = depth;
                res.at_signal = sgi;
            }
            if (rr < res.rms_min[sgi]) {
                res.rms_min[sgi] = rr;
                res.min_depth[sgi] = depth;
            }
            res.peak_max = fmaxf(res.peak_max, pr);
            if (floor_db != NULL) {
                res.floor_margin_db = fminf(res.floor_margin_db,
                                            (float)(20.0 * log10(rr) - floor_db(depth)));
            }
        }
    }
    return res;
}

/* The tremolo's level at depth d, by construction: out = in (1 - d + d g),
 * g = (1 + s) / 2 for the rounded-triangle LFO s, whose mean square is
 * E[s^2] = 2.25/3 - 1.5/5 + 0.25/7 = 0.4857 (mean 0). So the mean-square gain
 * is (1 - d/2)^2 + (d/2)^2 E[s^2]: -1.1, -2.3, -3.4, -4.3 dB at 25..100 %.
 * Its floor is that, less 0.2 dB. */
static double tremolo_floor_db(float depth)
{
    const double q = 2.25 / 3.0 - 1.5 / 5.0 + 0.25 / 7.0;
    double h = 0.5 * (double)depth;
    return 10.0 * log10((1.0 - h) * (1.0 - h) + h * h * q) - 0.2;
}

static double db(float ratio)
{
    return 20.0 * log10((double)ratio);
}

static void test_pack_level_bound(int k)
{
    level_result r = level_sweep(FX_EFFECTS, k, MASK_ALL, 20,
                                 k == 3 ? tremolo_floor_db : NULL);
    if (g_verbose) {
        printf("  %-8s level: worst RMS out/in %+.3f dB (depth %.2f, %s), worst peak out/in "
               "%+.3f dB; keys lowest %+.3f dB (depth %.2f)\n", FX_LABEL[k], db(r.rms_max),
               (double)r.at_depth, r.at_signal >= 0 ? LEVEL_SIGNAL_LABEL[r.at_signal] : "-",
               db(r.peak_max), db(r.rms_min[SIG_I_KEYS]), (double)r.min_depth[SIG_I_KEYS]);
    }

    /* Upper bound: never louder (RMS, + 0.1 dB), every effect, every signal,
     * every knob position. */
    CHECK(r.rms_max <= RMS_MARGIN, "%s: output RMS %.4fx input (depth %.2f, %s)", FX_LABEL[k],
          (double)r.rms_max, (double)r.at_depth, LEVEL_SIGNAL_LABEL[r.at_signal]);

    /* Its controls. Chorus and phaser: their level stage aiming 0.5 dB hot.
     * Phaser also: positive feedback, a resonance above unity the stage only
     * ever lifts and so cannot hide. Drive: its wet 0.5 dB hot. Tremolo: its
     * guarantee is the peak one, so an LFO that overshoots unity. */
    level_result c;
    if (k == 0 || k == 2) {
        fx_test_level_hot = 1;
        c = level_sweep(FX_EFFECTS, k, MASK_ALL, 4, NULL);
        fx_test_level_hot = 0;
        CHECK(c.rms_max > RMS_MARGIN, "%s: control (level stage 0.5 dB hot) passed: %+.3f dB",
              FX_LABEL[k], db(c.rms_max));
        if (g_verbose) {
            printf("  %-8s upper control, level stage hot: RMS %+.3f dB\n", FX_LABEL[k],
                   db(c.rms_max));
        }
    }
    if (k == 1) {
        fx_effect_desc hot = hot_version(k);
        c = level_sweep(table_with(k, &hot), k, MASK_ALL, 4, NULL);
        CHECK(c.rms_max > RMS_MARGIN, "drive: control (+0.5 dB) passed: %+.3f dB", db(c.rms_max));
    } else if (k == 2) {
        c = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_PHASER_HOT_FEEDBACK]), k, MASK_ALL,
                        4, NULL);
        CHECK(c.rms_max > RMS_MARGIN, "phaser: control (hot feedback) passed: %+.3f dB",
              db(c.rms_max));
    } else if (k == 3) {
        c = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_TREMOLO_OVERSHOOT]), k, MASK_ALL, 4,
                        NULL);
        CHECK(c.peak_max > 1.0f + 1e-6f, "tremolo: control (overshoot) passed: %+.3f dB",
              db(c.peak_max));
    }
    if (g_verbose && k != 0) {
        printf("  %-8s upper control: RMS %+.3f dB, peak %+.3f dB\n", FX_LABEL[k],
               db(c.rms_max), db(c.peak_max));
    }

    /* Peak, where the effect can promise it. The tremolo's gain is in [0, 1];
     * the drive's limiter holds it under the input's recent peak. The chorus's
     * is test_output_is_bounded. The phaser cannot: an all-pass moves phase
     * and raises the crest factor, and its limiter only stops the makeup from
     * adding to that. */
    if (k == 3) {
        CHECK(r.peak_max <= 1.0f + 1e-6f, "tremolo: output peak %.6fx input",
              (double)r.peak_max);
    }
    if (k == 1) {
        CHECK(r.peak_max <= 1.0f + 1e-4f, "drive: output peak %.6fx input", (double)r.peak_max);
        level_result cp = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_CEILING]),
                                      k, MASK_ALL, 4, NULL);
        CHECK(cp.peak_max > 1.0f + 1e-4f, "drive: control (no ceiling, no limiter) kept the "
              "peak: %+.3f dB", db(cp.peak_max));
        if (g_verbose) {
            printf("  drive    peak control (no ceiling, no limiter): %+.3f dB\n",
                   db(cp.peak_max));
        }
    }

    /* Lower bound: not more than 1 dB quieter, on the keys signal at every
     * knob position. The other signals are reported by --measure: white noise
     * and pure tones through a sweeping notch cannot be brought to parity
     * without breaking a peak guarantee (see the PR's table). The tremolo
     * cannot meet -1 dB at all without its peak going over the input, so it
     * is held to its own computed level instead (tremolo_floor_db). */
    if (k == 3) {
        CHECK(r.floor_margin_db >= 0.0f, "tremolo: %.3f dB under its computed level",
              -(double)r.floor_margin_db);
        level_result cl = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_TREMOLO_SHALLOW_TOP]),
                                      k, MASK_KEYS, 4, tremolo_floor_db);
        CHECK(cl.floor_margin_db < 0.0f, "tremolo: control (top at 0.8) met the floor: %+.3f dB",
              (double)cl.floor_margin_db);
        if (g_verbose) {
            printf("  tremolo  vs computed level: worst %+.3f dB; control (top 0.8) %+.3f dB\n",
                   (double)r.floor_margin_db, (double)cl.floor_margin_db);
        }
        return;
    }
    CHECK(r.rms_min[SIG_I_KEYS] >= RMS_FLOOR, "%s: keys %.3f dB at depth %.2f, floor -1 dB",
          FX_LABEL[k], db(r.rms_min[SIG_I_KEYS]), (double)r.min_depth[SIG_I_KEYS]);
    level_result cl;
    if (k == 1) {
        cl = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_MAKEUP]), k, MASK_KEYS,
                         4, NULL);
    } else {
        fx_test_no_level = 1;
        cl = level_sweep(FX_EFFECTS, k, MASK_KEYS, 4, NULL);
        fx_test_no_level = 0;
    }
    CHECK(cl.rms_min[SIG_I_KEYS] < RMS_FLOOR, "%s: control (%s) met the -1 dB floor: %+.3f dB",
          FX_LABEL[k], k == 1 ? "no makeup" : "no level stage", db(cl.rms_min[SIG_I_KEYS]));
    if (g_verbose) {
        printf("  %-8s lower control (%s): keys %+.3f dB\n", FX_LABEL[k],
               k == 1 ? "no makeup" : "no level stage", db(cl.rms_min[SIG_I_KEYS]));
    }
}

/* The drive's makeup is dynamic, so it also gets a level step: a loud note,
 * then the same note 40 dB down. The quiet tail must not be pumped up by the
 * makeup the loud note needed: in every 5 ms window after the first 30 ms, the
 * output peak stays within 1 dB of the input's peak over the last 30 ms. */
static float drive_tail_excess(const fx_effect_desc *table)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    const uint32_t win = (uint32_t)(0.005f * RATE);
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    float in_hist[6] = { 0 };   /* last six 5 ms input peaks */
    float worst = 0.0f, in_peak = 0.0f, out_peak = 0.0f;
    uint32_t pos = 0;
    signal_gen g = { SIG_SINE, 0.5f, 220.0f, 0.0 };
    const int periods = (int)(1.5f * RATE / PERIOD);
    const int drop = (int)(0.8f * RATE / PERIOD);
    for (int p = 0; p < periods; p++) {
        g.amp = p < drop ? 0.5f : 0.005f;
        sig_fill(&g, l, r, PERIOD);
        run_period(&e, l, r, ol, or_, PERIOD, 1, 1.0f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            in_peak = fmaxf(in_peak, fabsf(l[i]));
            out_peak = fmaxf(out_peak, fabsf(ol[i]));
            if (++pos == win) {
                for (int h = 5; h > 0; h--) {
                    in_hist[h] = in_hist[h - 1];
                }
                in_hist[0] = in_peak;
                float ref = 0.0f;
                for (int h = 0; h < 6; h++) {
                    ref = fmaxf(ref, in_hist[h]);
                }
                if (p > drop + 2 || p < drop) {
                    worst = fmaxf(worst, out_peak / ref);
                }
                pos = 0;
                in_peak = out_peak = 0.0f;
            }
        }
    }
    fx_engine_free(&e);
    return worst;
}

static void test_drive_level_step(void)
{
    float w = drive_tail_excess(FX_EFFECTS);
    float c = drive_tail_excess(table_with(1, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_CEILING]));
    if (g_verbose) {
        printf("  drive    level step: worst windowed peak out/in %+.2f dB "
               "(control, no ceiling: %+.2f dB)\n", 20.0 * log10(w), 20.0 * log10(c));
    }
    CHECK(w <= 1.122f, "drive: after a 40 dB drop the output peak reached %.3fx the input's", (double)w);
    CHECK(c > 1.122f, "drive: control (no ceiling) passed the level step too: %.3f", (double)c);
}

/* --- 3. clamp ------------------------------------------------------------ */

/* `abuse` 0 plays in-range noise through the first phase instead: the
 * reference for what "recovered" should measure, with every LFO in step. */
static size_t clamp_violations(int k, float *recovered, int abuse)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    size_t bad = 0;
    for (int p = 0; p < 2000; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 4.0f * noise();
            r[i] = (i % 97 == 0) ? NAN : (i % 89 == 0 ? INFINITY : 4.0f * noise());
            if (!abuse) {
                l[i] *= 0.1f;
                r[i] = 0.4f * noise();
            }
        }
        run_period(&e, l, r, ol, or_, PERIOD, k, 0.1f + (float)(p % 10) / 10.0f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            if (!isfinite(ol[i]) || !isfinite(or_[i]) || fabsf(ol[i]) > 1.0f ||
                fabsf(or_[i]) > 1.0f) {
                bad++;
            }
        }
    }
    /* And it comes back: NaN must not have poisoned a filter memory. */
    signal_gen g = { SIG_SINE, 0.3f, 220.0f, 0.0 };
    double sum = 0.0;
    int n = 0;
    for (int p = 0; p < 200; p++) {
        sig_fill(&g, l, r, PERIOD);
        run_period(&e, l, r, ol, or_, PERIOD, k, 0.5f);
        if (p >= 100) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                sum += (double)or_[i] * or_[i];
                n++;
            }
        }
    }
    *recovered = (float)sqrt(sum / n) / (0.3f / sqrtf(2.0f));
    fx_engine_free(&e);
    return bad;
}

static void test_pack_clamp(int k)
{
    float rec = 0.0f, fresh = 0.0f;
    size_t bad = clamp_violations(k, &rec, 1);
    (void)clamp_violations(k, &fresh, 0);
    CHECK(bad == 0, "%s: %zu samples non-finite or beyond ±1.0", FX_LABEL[k], bad);
    /* And it comes back to exactly what a fresh engine does, within 0.3 dB: a
     * NaN that sat in a level detector would leave the stage stuck, the
     * effect still sounding but 1-2 dB quiet. */
    double off_db = fabs(db(rec / fresh));
    CHECK(rec > 0.3f && off_db < 0.3, "%s: output %.3f of normal after NaN/Inf input, %.2f dB "
          "from a fresh engine — a memory stayed poisoned", FX_LABEL[k], (double)rec, off_db);
    fx_test_no_clamp = 1;
    size_t c = clamp_violations(k, &rec, 1);
    fx_test_no_clamp = 0;
    CHECK(c > 0, "%s: control (clamp off) passed too", FX_LABEL[k]);
    if (k == 0 || k == 2) {
        /* Control for the recovery criterion: a level stage stuck off. */
        float stuck = 0.0f;
        fx_test_no_level = 1;
        (void)clamp_violations(k, &stuck, 0);
        fx_test_no_level = 0;
        CHECK(fabs(db(stuck / fresh)) >= 0.3, "%s: control (level stage stuck off) recovered "
              "\"normally\" too: %.2f dB", FX_LABEL[k], fabs(db(stuck / fresh)));
        if (g_verbose) {
            printf("  %-8s NaN recovery: %.3f dB from fresh; control (stage stuck) %.2f dB\n",
                   FX_LABEL[k], off_db, fabs(db(stuck / fresh)));
        }
    }
}

/* --- 4. 120 s clean ------------------------------------------------------ */

typedef struct {
    size_t nonfinite, subnormal, residue;
} clean_result;

static clean_result long_run(const fx_effect_desc *table, int k)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    clean_result c = { 0, 0, 0 };
    const int periods = (int)(120.0f * RATE / PERIOD);
    for (int p = 0; p < periods; p++) {
        float level = (p % 500 < 400) ? 1e-3f * (float)(1 + p % 7) : 1e-30f;
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = level * noise();
            r[i] = level * noise();
        }
        /* Depth walks through the range, including 0 (bypass and re-entry). */
        float depth = (float)((p / 50) % 5) / 4.0f;
        run_period(&e, l, r, ol, or_, PERIOD, k, depth);
        for (uint32_t i = 0; i < PERIOD; i++) {
            c.nonfinite += !isfinite(ol[i]) + !isfinite(or_[i]);
            if (level > 1e-20f) {
                c.subnormal += (fpclassify(ol[i]) == FP_SUBNORMAL) +
                               (fpclassify(or_[i]) == FP_SUBNORMAL);
            }
        }
    }
    memset(l, 0, sizeof(l));
    memset(r, 0, sizeof(r));
    for (int p = 0; p < 400; p++) {   /* 2.1 s of silence */
        run_period(&e, l, r, ol, or_, PERIOD, k, 0.7f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            c.subnormal += (fpclassify(ol[i]) == FP_SUBNORMAL) +
                           (fpclassify(or_[i]) == FP_SUBNORMAL);
            if (p >= 100) {   /* 0.53 s: past the slowest filter's flush */
                c.residue += (ol[i] != 0.0f) + (or_[i] != 0.0f);
            }
        }
    }
    fx_engine_free(&e);
    return c;
}

static int clean(clean_result c)
{
    return c.nonfinite == 0 && c.subnormal == 0 && c.residue == 0;
}

static void test_pack_long_run(int k)
{
    clean_result c = long_run(FX_EFFECTS, k);
    CHECK(clean(c), "%s: 120 s run: %zu non-finite, %zu subnormal, %zu residue after silence",
          FX_LABEL[k], c.nonfinite, c.subnormal, c.residue);
    const fx_effect_desc *broken = k == 1 ? &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_FLUSH]
                                 : k == 2 ? &FX_TEST_MUTANTS[FX_MUT_PHASER_NO_FLUSH]
                                          : &LEAKY;   /* the tremolo has no memory to flush */
    clean_result m = long_run(table_with(k, broken), k);
    CHECK(!clean(m), "%s: control (%s) also came out clean", FX_LABEL[k], broken->name);
    if (g_verbose) {
        printf("  %-8s 120 s: clean; control %s: %zu subnormal, %zu residue\n", FX_LABEL[k],
               broken->name, m.subnormal, m.residue);
    }
}

/* --- 5 & 6. depth steps and swaps ---------------------------------------- */

static steps measure_steps_sig(fx_engine *e, const int *effects, const float *depths,
                               int segments, int periods_per_segment, int settle_periods,
                               float amp, float hz)
{
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    signal_gen g = { SIG_SINE, amp, hz, 0.0 };
    float prev = 0.0f;
    int have_prev = 0;
    steps s = { 0.0f, 0.0f };
    for (int seg = 0; seg < segments; seg++) {
        for (int p = 0; p < periods_per_segment; p++) {
            sig_fill(&g, l, r, PERIOD);
            run_period(e, l, r, ol, or_, PERIOD, effects[seg], depths[seg]);
            for (uint32_t i = 0; i < PERIOD; i++) {
                if (have_prev) {
                    float step = fabsf(ol[i] - prev);
                    s.worst = fmaxf(s.worst, step);
                    if (p >= settle_periods) {
                        s.settled = fmaxf(s.settled, step);
                    }
                }
                prev = ol[i];
                have_prev = 1;
            }
        }
    }
    return s;
}

/* The drive at full depth turns a 220 Hz sine into near-square edges whose
 * steps are larger than any click, so the drive's step checks use a low,
 * quieter tone where the edges stay soft: 0.1 at 110 Hz. */
static void step_signal(int k, float *amp, float *hz)
{
    *amp = k == 1 ? 0.1f : 0.5f;
    *hz = k == 1 ? 110.0f : 220.0f;
}

static void test_pack_depth_steps(int k)
{
    const int effects[] = { k, k, k, k, k };
    const float depths[] = { 0.0f, 1.0f, 0.0f, 0.5f, 1.0f };
    float amp, hz;
    step_signal(k, &amp, &hz);
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    steps s = measure_steps_sig(&e, effects, depths, 5, 190, 60, amp, hz);
    fx_engine_free(&e);
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 0.0, XFADE_MS) == 0, "init");
    steps c = measure_steps_sig(&e, effects, depths, 5, 190, 60, amp, hz);
    fx_engine_free(&e);
    if (g_verbose) {
        printf("  %-8s depth steps: worst %.5f / settled %.5f; control %.5f / %.5f\n",
               FX_LABEL[k], (double)s.worst, (double)s.settled, (double)c.worst,
               (double)c.settled);
    }
    CHECK(click_free(s), "%s depth steps: worst %.5f vs settled %.5f", FX_LABEL[k],
          (double)s.worst, (double)s.settled);
    CHECK(!click_free(c), "%s: control (unsmoothed) passed too: worst %.5f vs settled %.5f",
          FX_LABEL[k], (double)c.worst, (double)c.settled);
}

/* Every pair, both directions, at a depth where both are clearly audible. */
static void test_pack_swaps(void)
{
    for (int a = 0; a < N_FX; a++) {
        for (int b = a + 1; b < N_FX; b++) {
            const int effects[] = { a, b, a, b };
            const float depths[] = { 0.6f, 0.6f, 0.6f, 0.6f };
            /* Quiet and low so the drive's edges stay below a click. */
            fx_engine e;
            CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0,
                  "init");
            steps s = measure_steps_sig(&e, effects, depths, 4, 190, 60, 0.1f, 110.0f);
            int landed = e.active == b && e.incoming == -1;
            fx_engine_free(&e);
            CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, 0.0) == 0,
                  "init");
            steps c = measure_steps_sig(&e, effects, depths, 4, 190, 60, 0.1f, 110.0f);
            fx_engine_free(&e);
            if (g_verbose) {
                printf("  swap %-7s<->%-7s worst %.5f / settled %.5f; control %.5f / %.5f\n",
                       FX_LABEL[a], FX_LABEL[b], (double)s.worst, (double)s.settled,
                       (double)c.worst, (double)c.settled);
            }
            CHECK(click_free(s), "swap %s<->%s: worst %.5f vs settled %.5f", FX_LABEL[a],
                  FX_LABEL[b], (double)s.worst, (double)s.settled);
            CHECK(landed, "swap %s<->%s did not land", FX_LABEL[a], FX_LABEL[b]);
            CHECK(!click_free(c), "swap %s<->%s: control (instant) passed too: %.5f vs %.5f",
                  FX_LABEL[a], FX_LABEL[b], (double)c.worst, (double)c.settled);
        }
    }
}

/* --- 7. re-entry after bypass -------------------------------------------- */

/* Two instances see the same 1.1 s of audio (not a whole number of tremolo or
 * phaser cycles, so a frozen LFO cannot land back in step by luck): one bypassed (feed), one running
 * (process, knob at the bottom). The first chunk after re-entry must be the
 * same — i.e. bypass left nothing stale. Returns the largest difference. */
static float reentry_difference(const fx_effect_desc *d)
{
    void *a = calloc(1, d->state_bytes);
    void *b = calloc(1, d->state_bytes);
    CHECK(a != NULL && b != NULL && d->init(a, RATE) == 0 && d->init(b, RATE) == 0, "init");
    static const float zero[FX_CHUNK];
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], vl[FX_CHUNK], vr[FX_CHUNK];
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wa[FX_CHANNELS] = { wl, wr };
    float *const wb[FX_CHANNELS] = { vl, vr };
    signal_gen g = { SIG_SINE, 0.4f, 220.0f, 0.0 };
    for (int c = 0; c < (int)(1.1f * RATE / FX_CHUNK); c++) {
        sig_fill(&g, l, r, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            r[i] += 0.1f * noise();
        }
        if (d->feed != NULL) {
            d->feed(a, in, FX_CHUNK);
        }
        d->process(b, in, wb, zero, FX_CHUNK);
    }
    sig_fill(&g, l, r, FX_CHUNK);
    d->process(a, in, wa, zero, FX_CHUNK);
    d->process(b, in, wb, zero, FX_CHUNK);
    float diff = 0.0f;
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        diff = fmaxf(diff, fmaxf(fabsf(wl[i] - vl[i]), fabsf(wr[i] - vr[i])));
    }
    free(a);
    free(b);
    return diff;
}

static void test_pack_reentry(int k)
{
    float d = reentry_difference(&FX_EFFECTS[k]);
    CHECK(d == 0.0f, "%s: re-entry after bypass differs from never-bypassed by %.6f",
          FX_LABEL[k], (double)d);
    fx_effect_desc no_feed = FX_EFFECTS[k];
    no_feed.feed = NULL;
    float c = reentry_difference(&no_feed);
    CHECK(c > 1e-3f, "%s: control (no feed) re-entered cleanly too: %.6f", FX_LABEL[k],
          (double)c);
    if (g_verbose) {
        printf("  %-8s re-entry: diff %.3g; control (no feed) %.3g\n", FX_LABEL[k], (double)d,
               (double)c);
    }
}

/* --- 8. drive aliasing ---------------------------------------------------- */

/* A 10 kHz sine at 48 kHz, driven to the top. Its harmonics at 30, 40, 50 kHz
 * ... fold back to 18, 8, 2 kHz ... unless they are removed before they can.
 * The sine repeats every 24 samples, so the output (time-invariant once
 * settled) folds onto one 24-sample period, whose DFT bins are multiples of
 * 2 kHz. The true harmonics below Nyquist are 10 and 20 kHz; everything else
 * except DC is alias. Returns alias power over total, in dB. */
static float drive_alias_db(const fx_effect_desc *table)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    enum { P = 24 };
    float l[PERIOD * 3], r[PERIOD * 3], ol[PERIOD * 3], or_[PERIOD * 3];   /* 768 = 32 P */
    double fold[P] = { 0 };
    signal_gen g = { SIG_SINE, 0.5f, 10000.0f, 0.0 };
    long t = 0;
    for (int p = 0; p < 400; p++) {
        /* Phase from the sample index, so the period is exact. */
        for (uint32_t i = 0; i < PERIOD * 3; i++, t++) {
            l[i] = r[i] = g.amp * (float)sin(2.0 * M_PI * (double)(t % P) * 10.0 / 48.0);
        }
        run_period(&e, l, r, ol, or_, PERIOD * 3, 1, 1.0f);
        if (p >= 200) {
            for (uint32_t i = 0; i < PERIOD * 3; i++) {
                fold[i % P] += ol[i];
            }
        }
    }
    fx_engine_free(&e);
    double total = 0.0, alias = 0.0;
    for (int b = 1; b <= P / 2; b++) {
        double re = 0.0, im = 0.0;
        for (int i = 0; i < P; i++) {
            re += fold[i] * cos(2.0 * M_PI * b * i / P);
            im -= fold[i] * sin(2.0 * M_PI * b * i / P);
        }
        double pw = re * re + im * im;
        total += pw;
        if (b != 5 && b != 10) {
            alias += pw;
        }
    }
    return (float)(10.0 * log10(alias / total + 1e-30));
}

/* Measured: -51.7 dB oversampled, -12.5 dB without (the round-3 voicing:
 * algebraic sigmoid, top drive x8). The old cubic, driven to x16, measured
 * -26.0 / -8.9: its harmonics 17-21 folded at the 4x rate itself. The limit
 * sits between the shipped figure and the control's. */
#define ALIAS_LIMIT_DB -35.0f

static void test_drive_aliasing(void)
{
    float a = drive_alias_db(FX_EFFECTS);
    float c = drive_alias_db(table_with(1, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_OVERSAMPLING]));
    if (g_verbose) {
        printf("  drive    aliasing (10 kHz, full drive): %.1f dB; control (no oversampling) "
               "%.1f dB; limit %.1f dB\n", (double)a, (double)c, (double)ALIAS_LIMIT_DB);
    }
    CHECK(a < ALIAS_LIMIT_DB, "drive: aliased energy %.1f dB, limit %.1f", (double)a,
          (double)ALIAS_LIMIT_DB);
    CHECK(c >= ALIAS_LIMIT_DB, "drive: control (no oversampling) passed too: %.1f dB",
          (double)c);
}

/* --- cost ----------------------------------------------------------------- */

#include <time.h>

/* ns per stereo frame for each effect alone, active at depth 0.7. Machine-
 * dependent: a comparison between effects, not the Pi measurement. */
static void bench(void)
{
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], amt[FX_CHUNK];
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        amt[i] = 0.7f;
    }
    for (int k = 0; k < FX_EFFECT_COUNT; k++) {
        void *s = calloc(1, FX_EFFECTS[k].state_bytes);
        FX_EFFECTS[k].init(s, RATE);
        const int chunks = 200000;
        struct timespec t0, t1;
        double sink = 0.0;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        for (int c = 0; c < chunks; c++) {
            for (uint32_t i = 0; i < FX_CHUNK; i++) {
                l[i] = 0.3f * noise();
                r[i] = 0.3f * noise();
            }
            FX_EFFECTS[k].process(s, in, wet, amt, FX_CHUNK);
            sink += wl[c % FX_CHUNK];
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        double ns = ((double)(t1.tv_sec - t0.tv_sec) * 1e9 + (double)(t1.tv_nsec - t0.tv_nsec)) /
                    ((double)chunks * FX_CHUNK);
        printf("%-8s %6.1f ns/stereo frame (incl. ~2 ns noise generation)%s\n", FX_EFFECTS[k].name,
               ns, sink == 12345.0 ? " " : "");
        free(s);
    }
}

/* --- measurement report (--measure) --------------------------------------- */

/* Levels of harmonics 1..7 of a 250 Hz tone (exactly 192 samples a period, so
 * the folded DFT has no leakage) through `table`'s effect k at `depth`, in dB
 * relative to the fundamental (h[0] is the fundamental in dBFS). */
static void harmonic_profile(const fx_effect_desc *table, int k, float amp, float depth,
                             double h[7])
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    enum { P = 192 };
    float l[PERIOD * 3], r[PERIOD * 3], ol[PERIOD * 3], or_[PERIOD * 3];   /* 768 = 4 P */
    static double fold[P];
    memset(fold, 0, sizeof(fold));
    long t = 0;
    int folded = 0;
    for (int p = 0; p < 300; p++) {
        for (uint32_t i = 0; i < PERIOD * 3; i++, t++) {
            l[i] = r[i] = amp * (float)sin(2.0 * M_PI * (double)(t % P) / P);
        }
        run_period(&e, l, r, ol, or_, PERIOD * 3, k, depth);
        if (p >= 100) {
            for (uint32_t i = 0; i < PERIOD * 3; i++) {
                fold[i % P] += ol[i];
            }
            folded += 4;
        }
    }
    fx_engine_free(&e);
    double ref = 0.0;
    for (int b = 1; b <= 7; b++) {
        double re = 0.0, im = 0.0;
        for (int i = 0; i < P; i++) {
            re += fold[i] * cos(2.0 * M_PI * b * i / P);
            im -= fold[i] * sin(2.0 * M_PI * b * i / P);
        }
        double mag = 2.0 * sqrt(re * re + im * im) / ((double)P * folded);
        if (b == 1) {
            ref = mag;
            h[0] = 20.0 * log10(mag + 1e-30);
        } else {
            h[b - 1] = 20.0 * log10(mag / ref + 1e-30);
        }
    }
}

static void measure_report(void)
{
    static const float knobs[5] = { 0.0f, 0.25f, 0.5f, 0.75f, 1.0f };
    printf("RMS out/in, dB (both channels), 0.4 s settle + 1.2 s measured (keys 2.4 s)\n");
    printf("%-8s %5s", "effect", "knob");
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        printf(" %8s", LEVEL_SIGNAL_LABEL[sgi]);
    }
    printf(" %8s %8s %8s\n", "min", "max", "peak");
    for (int k = 0; k < N_FX; k++) {
        for (int j = 0; j < 5; j++) {
            float lo = 1e9f, hi = -1e9f, pk = -1e9f;
            printf("%-8s %4.0f%%", FX_LABEL[k], 100.0 * knobs[j]);
            for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
                float rr, pr;
                level_at(FX_EFFECTS, k, sgi, knobs[j], &rr, &pr);
                float db = 20.0f * log10f(rr);
                lo = fminf(lo, db);
                hi = fmaxf(hi, db);
                pk = fmaxf(pk, 20.0f * log10f(pr));
                printf(" %+8.2f", (double)db);
            }
            printf(" %+8.2f %+8.2f %+8.2f\n", (double)lo, (double)hi, (double)pk);
        }
    }
    printf("\nDrive harmonic profile, 250 Hz sine, knob 100%%: fundamental dBFS, then "
           "h2..h7 dB re fundamental\n");
    static const float amps[3] = { 0.5f, 0.2f, 0.05f };
    for (int a = 0; a < 3; a++) {
        double h[7];
        harmonic_profile(FX_EFFECTS, 1, amps[a], 1.0f, h);
        printf("  in %.2f: h1 %+6.1f |", (double)amps[a], h[0]);
        for (int b = 1; b < 7; b++) {
            printf(" h%d %+6.1f", b + 1, h[b]);
        }
        printf("\n");
    }
    printf("\nDrive aliasing, 10 kHz sine 0.5, knob 100%%: %.1f dB of output energy\n",
           (double)drive_alias_db(FX_EFFECTS));
}

/* Smoother at full drive, as an objective proxy for the ear: on a 250 Hz
 * tone at 0.5, the odd harmonics fall off fast. Shipped: h5 -19.3, h7 -25.8
 * dB re the fundamental. The round-2 voicing measured h5 -14.6, h7 -18.2.
 * Limits h5 <= -17, h7 <= -22. Control: the round-2 cubic shaper on the new
 * drive law must fail them. */
static int drive_smooth(const fx_effect_desc *table, double h[7])
{
    harmonic_profile(table, 1, 0.5f, 1.0f, h);
    return h[4] <= -17.0 && h[6] <= -22.0;
}

static void test_drive_smoothness(void)
{
    double h[7], c[7];
    int ok = drive_smooth(FX_EFFECTS, h);
    int cok = drive_smooth(table_with(1, &FX_TEST_MUTANTS[FX_MUT_DRIVE_OLD_SHAPER]), c);
    if (g_verbose) {
        printf("  drive    harmonics at full, 250 Hz 0.5: h3 %+.1f h5 %+.1f h7 %+.1f dB; control "
               "(old shaper) h3 %+.1f h5 %+.1f h7 %+.1f dB\n", h[2], h[4], h[6], c[2], c[4],
               c[6]);
    }
    CHECK(ok, "drive: h5 %+.1f / h7 %+.1f dB at full drive, limits -17 / -22", h[4], h[6]);
    CHECK(!cok, "drive: control (old shaper) passed the smoothness limits: h5 %+.1f h7 %+.1f",
          c[4], c[6]);
}

static void test_pack(void)
{
    /* The chorus has its own battery above; these add its level stage. */
    test_pack_level_bound(0);
    test_pack_clamp(0);
    for (int k = 1; k < N_FX; k++) {
        test_pack_bypass_identity(k);
        test_pack_level_bound(k);
        test_pack_clamp(k);
        test_pack_long_run(k);
        test_pack_depth_steps(k);
        test_pack_reentry(k);
    }
    test_drive_level_step();
    test_drive_aliasing();
    test_drive_smoothness();
    test_pack_swaps();
}

int main(int argc, char **argv)
{
    if (argc == 3 && strcmp(argv[1], "--parse") == 0) {
        int id = -1;
        float depth = 0.0f;
        if (fx_parse_control(argv[2], &id, &depth)) {
            printf("ok %d %.6f\n", id, (double)depth);
        } else {
            printf("reject\n");
        }
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--measure") == 0) {
        measure_report();
        return g_failures != 0;
    }
    if (argc == 2 && strcmp(argv[1], "--bench") == 0) {
        bench();
        return 0;
    }
    g_verbose = argc == 2 && strcmp(argv[1], "-v") == 0;

    test_depth_zero_is_the_input();
    test_output_is_bounded();
    test_long_run_stays_clean();
    test_depth_step_has_no_click();
    test_swap_crossfades();
    test_bypass_keeps_history();
    test_rate_limits();
    test_pack();

    if (g_failures != 0) {
        fprintf(stderr, "fx-selftest: %d check(s) failed\n", g_failures);
        return 1;
    }
    printf("fx-selftest: all checks passed\n");
    return 0;
}
