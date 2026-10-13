/*
 * fx_selftest — the listener-protecting properties of fx_dsp, run without JACK.
 *
 *   fx-selftest [-v]            run every check, one worker per core (see the
 *                               runner at the end); exit 0 only if all pass.
 *                               -v prints each check's numbers and its
 *                               control's. FX_SELFTEST_JOBS=N caps the
 *                               workers (1: serial), FX_SELFTEST_FULL=1 runs
 *                               the pre-2026-10-13 signal lengths,
 *                               FX_SELFTEST_TIMES=1 adds each check's time
 *   fx-selftest --runner-check order|fail|crash   the runner's own controls
 *   fx-selftest --parse TEXT    print "ok <mask> <depth> <l0> ... <l7>" (every
 *                               level slot, FX_MAX_EFFECTS of them) or
 *                               "reject" for one datagram, through the
 *                               client's real parser
 *   fx-selftest --parse-lines   the same for each line of stdin, one answer
 *                               per line
 *   fx-selftest --measure       print the level table (RMS out/in per effect,
 *                               a few chains, knob position and signal), the
 *                               drive's harmonic profile and its aliasing
 *   fx-selftest --measure-unlevelled   the same with no level curve (the
 *                               mixes' own loss, as #149 had it)
 *   fx-selftest --measure-no-makeup    the same without the chain's makeup
 *                               (the levels as they were before it)
 *   fx-selftest --bench         ns per stereo frame for each effect, and for
 *                               the engine with each alone and all five on,
 *                               and all five at mixed levels, settled and
 *                               moving every period
 *
 * The discontinuity checks each carry a control: the same run with the
 * protection switched off (no depth smoothing, an instant swap) must FAIL the
 * same criterion. A criterion that the broken build also passes measures
 * nothing, and would read exactly like this one passing.
 *
 * Driven by tests/test_fx_host.py (built by `make selftest-bin`); `make
 * selftest` builds and runs it.
 */

#define _GNU_SOURCE
#include "fx_dsp.h"

#include <errno.h>
#include <math.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define RATE 48000.0f
#define PERIOD 256u
#define DEPTH_MS 40.0
#define XFADE_MS 30.0

static int g_failures;
static int g_verbose;

/* Signal lengths. The long runs were 120 s and the level windows 5 s for
 * every effect until 2026-10-13; the measured worst cases and every control at
 * both lengths are in the PR that cut them. FX_SELFTEST_FULL=1 runs the old
 * lengths, for a soak or to re-measure. The feedback check keeps its 115.2 s:
 * at 28.8 s its growth reading of the phaser runaway fell from +1.6 dB to
 * +0.0 dB, so that criterion no longer saw its control. */
static int g_full;
#define LONG_RUN_S (g_full ? 120.0f : 30.0f)
#define FEEDBACK_WINDOWS 12   /* of 9.6 s: 115.2 s */

#define CHECK(cond, ...)                                   \
    do {                                                   \
        if (!(cond)) {                                     \
            g_failures++;                                  \
            fprintf(stderr, "FAIL %s:%d: ", __func__, __LINE__); \
            fprintf(stderr, __VA_ARGS__);                  \
            fputc('\n', stderr);                           \
        }                                                  \
    } while (0)

/* Each check runs in a worker process (see the runner at the end); a check
 * split across workers leaves its part in a slot of this shared map, which
 * the parent folds back together in a fixed order. */
#define SLOT_BYTES 256
static unsigned char *g_slots;
static const void *slot_of(int case_index)
{
    return g_slots + (size_t)case_index * SLOT_BYTES;
}

#define RNG_SEED 0x9e3779b9u
static uint32_t g_rng = RNG_SEED;
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
    g_two[1] = (fx_effect_desc){ "flip", 0.5f, 1.0f, 0.0f, 0.0f, 1, flip_init, flip_process, NULL };
}

#define BIT(k) (1u << (k))

/* The per-effect levels every run_period plays at: NULL is every level 1.0,
 * which is the `chain` command. The level checks set it and put it back. */
static const float *g_levels;

/* The chain's makeup the engine should apply once settled, from FX_MAKEUP_DB
 * and the depth alone, never from the engine's own table: a broken table must
 * not move the bound with it. m is the loudest enabled stage's depth, the
 * shared depth times its level (fx_dsp.h, "Makeup"). */
static float makeup_m(uint32_t fx, float depth)
{
    float top = 0.0f;
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        if ((fx >> k) & 1u) {
            float lv = g_levels == NULL ? 1.0f : g_levels[k];
            lv = lv > 1.0f ? 1.0f : (lv > 0.0f ? lv : 0.0f);
            top = fmaxf(top, lv);
        }
    }
    return depth * top;
}
static float makeup_gain(float m)
{
    return (float)pow(10.0, FX_MAKEUP_DB * (double)m / 20.0);
}

/* One period with the effects in `mask` enabled (BIT(k) for effect k alone). */
static void run_period(fx_engine *e, float *l, float *r, float *ol, float *or_,
                       uint32_t n, uint32_t mask, float depth)
{
    const float *const in[FX_CHANNELS] = { l, r };
    float *const out[FX_CHANNELS] = { ol, or_ };
    if (g_levels == NULL) {
        fx_engine_process(e, in, out, n, mask, depth);
    } else {
        fx_engine_process_levels(e, in, out, n, mask, depth, g_levels);
    }
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
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0), 0.0f);
        if (memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0) {
            mismatches++;
        }
        /* In place, as JACK may hand it over. */
        float keep[PERIOD];
        memcpy(keep, l, sizeof(l));
        run_period(&e, l, r, l, r, PERIOD, BIT(0), 0.0f);
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
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0), p < 100 ? 1.0f : 0.0f);
    }
    for (uint32_t i = 0; i < PERIOD; i++) {
        l[i] = noise();
        r[i] = noise();
    }
    run_period(&e, l, r, ol, or_, PERIOD, BIT(0), 0.0f);
    CHECK(memcmp(l, ol, sizeof(l)) == 0 && memcmp(r, or_, sizeof(r)) == 0,
          "depth 0 after a ramp down is not the input");
    fx_engine_free(&e);
}

/* Peak: with in-range input of peak A, no output sample exceeds A times the
 * chorus's level gain at full depth times the chain's makeup at full, the
 * largest gain the engine applies to it — the mix itself (an interpolation of the last ~9 ms of input) never
 * exceeds A, and the gain is static. Then the clamp: out-of-range, infinite
 * and NaN input never leave ±1.0 or produce NaN. */
/* Largest output sample over 32 s of noise at peak `amp`, depth stepping
 * through 0, 0.3, 0.7 and 1 (each held long enough to settle) and the effect
 * (chorus, flip) swapping. */
static float bounded_peak(fx_engine *e, float amp)
{
    static const float depths[4] = { 0.0f, 0.3f, 0.7f, 1.0f };
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    float peak = 0.0f;
    for (int p = 0; p < 6000; p++) {          /* 32 s */
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = amp * noise();
            r[i] = amp * noise();
        }
        float depth = depths[(p / 40) % 4];
        int effect = (p / 300) % 2;
        run_period(e, l, r, ol, or_, PERIOD, BIT(effect), depth);
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
    const float bound = amp * e.level[0][FX_LEVEL_POINTS] * makeup_gain(1.0f) + 1e-6f;
    float peak = bounded_peak(&e, amp);
    CHECK(peak <= bound, "output peak %.7f exceeds input peak x full-depth gain %.7f", peak,
          bound);
    /* Control: the level curve 0.5 dB hot must show as a peak over it. */
    fx_test_level_hot = 1;
    float c = bounded_peak(&e, amp);
    fx_test_level_hot = 0;
    CHECK(c > bound, "control (level 0.5 dB hot) kept the peak too: %.7f", (double)c);
    if (g_verbose) {
        printf("  chorus   peak bound: %.4f for input %.4f x gain %.4f x makeup %.4f; control "
               "(hot) %.4f\n", (double)peak, (double)amp, (double)e.level[0][FX_LEVEL_POINTS],
               (double)makeup_gain(1.0f), (double)c);
    }

    size_t bad = 0;
    for (int p = 0; p < 2000; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 4.0f * noise();
            r[i] = (i % 97 == 0) ? NAN : (i % 89 == 0 ? INFINITY : 4.0f * noise());
        }
        run_period(&e, l, r, ol, or_, PERIOD, BIT(p % 2), (float)(p % 11) / 10.0f);
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

/* LONG_RUN_S of audio with depth and effect moving, then silence. Nothing may
 * go non-finite or subnormal, and silence in must become exact silence out. */
typedef struct {
    size_t nonfinite, subnormal, residue;
} clean_result;

static clean_result two_long_run(const fx_effect_desc *two)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, two, 2, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    size_t nonfinite = 0, subnormal = 0, residue = 0;
    const int periods = (int)(LONG_RUN_S * RATE / PERIOD);
    for (int p = 0; p < periods; p++) {
        float level = (p % 500 < 400) ? 1e-3f * (float)(1 + p % 7) : 1e-30f;
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = level * noise();
            r[i] = level * noise();
        }
        float depth = (float)((p / 50) % 5) / 4.0f;
        run_period(&e, l, r, ol, or_, PERIOD, BIT((p / 1000) % 2), depth);
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
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0), 0.7f);
        if (p >= 4) {   /* past the longest delay tap */
            for (uint32_t i = 0; i < PERIOD; i++) {
                residue += (ol[i] != 0.0f) + (or_[i] != 0.0f);
            }
        }
    }
    fx_engine_free(&e);
    return (clean_result){ nonfinite, subnormal, residue };
}

static void test_long_run_stays_clean(void)
{
    build_two();
    clean_result c = two_long_run(g_two);
    CHECK(c.nonfinite == 0, "%zu non-finite samples", c.nonfinite);
    CHECK(c.subnormal == 0, "%zu subnormal samples from normal-range input", c.subnormal);
    CHECK(c.residue == 0, "%zu non-zero samples after silence", c.residue);
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
            run_period(e, l, r, ol, or_, PERIOD, BIT(effects[seg]), depths[seg]);
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
    CHECK(e.on[1] == 1.0f && e.on[0] == 0.0f, "swap did not land (on %.3f / %.3f)",
          (double)e.on[0], (double)e.on[1]);
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
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0), 0.0f);
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
/* The starter pack: drive (1), phaser (2), tremolo (3); flanger (4)          */
/* ========================================================================= */
/*
 * The same battery as the chorus, per effect, each check paired with a
 * deliberately broken run it must fail. The broken effects are FX_TEST_MUTANTS
 * (fx_dsp.c, compiled only with FX_SELFTEST) or built here from the shipped
 * descriptor (feed removed, wet made 0.5 dB hotter).
 */

#define N_FX 5
static const char *const FX_LABEL[N_FX] = { "chorus", "drive", "phaser", "tremolo", "flanger" };
#define FX_I_FLANGER 4

/* The effects with a static level curve (fx_dsp.h): the mixes. */
static int has_level_curve(int k)
{
    return k == 0 || k == 2 || k == FX_I_FLANGER;
}

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
static const fx_effect_desc LEAKY = { "leaky", 1.0f, 1.0f, 0.0f, 0.0f, sizeof(leaky_state),
                                      leaky_init, leaky_process, leaky_feed };

/* The chorus long run's control (test_long_run_stays_clean): the leaky effect
 * in the chorus's slot must show the subnormals and the residue. Added when
 * the run was cut from 120 s to 30 s, so the shorter run is shown to see. */
static void test_two_long_run_control(void)
{
    build_two();
    const fx_effect_desc two[2] = { LEAKY, g_two[1] };
    clean_result m = two_long_run(two);
    CHECK(m.nonfinite != 0 || m.subnormal != 0 || m.residue != 0,
          "chorus long run: control (leaky) also came out clean");
    if (g_verbose) {
        printf("  chorus   %.0f s: clean; control leaky: %zu non-finite, %zu subnormal, %zu residue\n",
               (double)LONG_RUN_S, m.nonfinite, m.subnormal, m.residue);
    }
}

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
        run_period(&e, l, r, ol, or_, PERIOD, BIT(k), depth);
        mismatches += memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0;
        memcpy(keep_l, l, sizeof(l));
        memcpy(keep_r, r, sizeof(r));
        run_period(&e, l, r, l, r, PERIOD, BIT(k), depth);
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
/* The lower bound: -1 dB. The phaser's is -1.3 dB. Round 4's four-stage
 * phaser could not reach -1 dB on keys without white noise and an 80 Hz tone
 * going over their input. Round 5's alone could (-0.93 dB at worst), but in
 * series with the flanger the two curves stack: phaser + flanger at full
 * measured keys +0.28 dB over its input. The upper bound wins, so the
 * phaser's curve is held lower (see PHASER_LEVEL_SCALE in fx_dsp.c). */
#define RMS_FLOOR 0.8913f
#define RMS_FLOOR_PHASER 0.8610f

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

/* The measured window, whole sweeps of every LFO in `fx`. A window shorter
 * than a sweep reads whatever part of the sweep it caught: 1.2 s of noise
 * through the phaser read 0.13 to 0.2 dB off its long-run level, which is the
 * whole margin a static level curve has (see PHASER_LEVEL_SCALE). The LFOs:
 * flanger 0.2 Hz (5 s), phaser 0.4 Hz (2.5 s), chorus 0.6 Hz (1.67 s),
 * tremolo 5 Hz (0.2 s); the drive has none. So 5 s with the flanger, or the
 * chorus and the phaser together; 2.5 s for the phaser, one chorus sweep for
 * the chorus, 1 s (five tremolo cycles) for the drive and the tremolo. The
 * keys: two loops (4.8 s, 1.9 phaser cycles) with a sweep slower than the
 * loop, one loop (2.4 s) without. Until 2026-10-13 every mask had 5 s, keys
 * 4.8 s (FX_SELFTEST_FULL=1). */
static float level_window_s(uint32_t fx, int kind)
{
    const uint32_t sweeps = BIT(0) | BIT(2) | BIT(FX_I_FLANGER);
    if (g_full) {
        return kind == SIG_KEYS ? 4.8f : 5.0f;
    }
    if (kind == SIG_KEYS) {
        return (fx & sweeps) != 0 ? 4.8f : 2.4f;
    }
    if ((fx & BIT(FX_I_FLANGER)) != 0 || (fx & (BIT(0) | BIT(2))) == (BIT(0) | BIT(2))) {
        return 5.0f;
    }
    if ((fx & BIT(2)) != 0) {
        return 2.5f;
    }
    if ((fx & BIT(0)) != 0) {
        return 5.0f / 3.0f;
    }
    return 1.0f;
}

/* Each signal's noise starts from its own seed, whatever ran before, so a
 * level reading does not depend on how the sweeps are split across workers. */
static void level_seed(int sgi)
{
    g_rng = 0x9e3779b9u ^ (0x85ebca6bu * (uint32_t)(sgi + 1));
    if (g_rng == 0u) {
        g_rng = 1u;
    }
}

/* RMS and peak out/in for the effects in `fx`, one signal, one knob position:
 * 0.4 s to settle, then level_window_s measured. */
static void level_at(const fx_effect_desc *table, uint32_t fx, int sgi, float depth,
                     float *rms, float *peak)
{
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    signal_gen g = LEVEL_SIGNALS[sgi];
    level_seed(sgi);
    double sum_in = 0.0, sum_out = 0.0;
    float peak_in = 0.0f, peak_out = 0.0f;
    const int settle = (int)(0.4f * RATE / PERIOD);
    const int measure = (int)(level_window_s(fx, g.kind) * RATE / PERIOD);
    for (int p = 0; p < settle + measure; p++) {
        sig_fill(&g, l, r, PERIOD);
        run_period(&e, l, r, ol, or_, PERIOD, fx, depth);
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

/* `steps` + 1 knob positions from 0 to 1, on the signals in `mask`. Every
 * reading is out / (in x G(m)): relative to the chain's makeup at that knob
 * position, so "never louder" below means never louder than the makeup, and
 * the floors are floors under it. */
static level_result level_sweep(const fx_effect_desc *table, uint32_t fx, unsigned mask,
                                int steps, double (*floor_db)(float depth))
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
            level_at(table, fx, sgi, depth, &rr, &pr);
            const float gm = makeup_gain(makeup_m(fx, depth));
            rr /= gm;
            pr /= gm;
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

/* The level bound runs as one sweep per (effect, variant, signal), each in
 * its own worker, merged back in signal order (lb_merge): the same result a
 * single sweep over every signal gives. The variants are the main sweep and
 * the controls below. */
enum { LB_MAIN, LB_CURVE_HOT, LB_UPPER, LB_UPPER2, LB_PEAK, LB_LOWER, LB_N };

typedef struct {
    const fx_effect_desc *table;
    unsigned mask;
    int steps;
    double (*floor_db)(float depth);
    int level_hot, no_level;
} lb_spec;

/* The sweep for effect k, variant v; 0 if k has no such variant. */
static int lb_spec_for(int k, int v, lb_spec *sp)
{
    static fx_effect_desc hot;
    *sp = (lb_spec){ FX_EFFECTS, MASK_ALL, 4, NULL, 0, 0 };
    switch (v) {
    case LB_MAIN:
        sp->steps = 20;
        sp->floor_db = k == 3 ? tremolo_floor_db : NULL;
        return 1;
    case LB_CURVE_HOT:
        sp->level_hot = 1;
        return has_level_curve(k);
    case LB_UPPER:
        if (k == 1) {
            hot = hot_version(k);
            sp->table = table_with(k, &hot);
        } else if (k == 2) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_PHASER_HOT_FEEDBACK]);
        } else if (k == FX_I_FLANGER) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_FLANGER_HOT_FEEDBACK]);
        } else if (k == 3) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_TREMOLO_OVERSHOOT]);
        } else {
            return 0;
        }
        return 1;
    case LB_UPPER2:
        if (k != 2) {
            return 0;
        }
        sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_PHASER_FULL_BAND]);
        return 1;
    case LB_PEAK:
        if (k != 1) {
            return 0;
        }
        sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_CEILING]);
        return 1;
    case LB_LOWER:
        sp->mask = MASK_KEYS;
        if (k == 3) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_TREMOLO_SHALLOW_TOP]);
            sp->floor_db = tremolo_floor_db;
        } else if (k == 1) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_MAKEUP]);
        } else {
            sp->no_level = 1;
        }
        return 1;
    default:
        return 0;
    }
}

/* One signal of one variant, in a worker. */
static void lb_sweep_signal(int k, int v, int sgi, level_result *out)
{
    lb_spec sp;
    CHECK(lb_spec_for(k, v, &sp) && (sp.mask & (1u << sgi)), "no sweep %d/%d/%d", k, v, sgi);
    fx_test_level_hot = sp.level_hot;
    fx_test_no_level = sp.no_level;
    *out = level_sweep(sp.table, BIT(k), 1u << sgi, sp.steps, sp.floor_db);
    fx_test_level_hot = 0;
    fx_test_no_level = 0;
}

static level_result lb_init(void)
{
    level_result res = { 0.0f, 0.0f, 0.0f, -1, { 0 }, { 0 }, 1e9f };
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        res.rms_min[sgi] = 1e9f;
    }
    return res;
}

/* Fold signal sgi's sweep into acc. In signal order this is level_sweep's own
 * loop: the first strictly larger RMS wins, the minima are per signal, and
 * the peak and the floor margin are a max and a min. */
static void lb_merge(level_result *acc, const level_result *part, int sgi)
{
    if (part->rms_max > acc->rms_max) {
        acc->rms_max = part->rms_max;
        acc->at_depth = part->at_depth;
        acc->at_signal = part->at_signal;
    }
    acc->rms_min[sgi] = part->rms_min[sgi];
    acc->min_depth[sgi] = part->min_depth[sgi];
    acc->peak_max = fmaxf(acc->peak_max, part->peak_max);
    acc->floor_margin_db = fminf(acc->floor_margin_db, part->floor_margin_db);
}

/* The checks, on the merged sweeps (`sw[v]`, for each variant k has). */
static void check_pack_level_bound(int k, const level_result sw[LB_N])
{
    const level_result r = sw[LB_MAIN];
    if (g_verbose) {
        printf("  %-8s level vs makeup: worst RMS %+.3f dB (depth %.2f, %s), worst peak "
               "%+.3f dB; keys lowest %+.3f dB (depth %.2f)\n", FX_LABEL[k], db(r.rms_max),
               (double)r.at_depth, r.at_signal >= 0 ? LEVEL_SIGNAL_LABEL[r.at_signal] : "-",
               db(r.peak_max), db(r.rms_min[SIG_I_KEYS]), (double)r.min_depth[SIG_I_KEYS]);
    }

    /* Upper bound: never louder than the makeup (RMS, + 0.1 dB), every
     * effect, every signal, every knob position. */
    CHECK(r.rms_max <= RMS_MARGIN, "%s: output RMS %.4fx input x makeup (depth %.2f, %s)",
          FX_LABEL[k],
          (double)r.rms_max, (double)r.at_depth, LEVEL_SIGNAL_LABEL[r.at_signal]);

    /* Its controls. Chorus, phaser and flanger: their level curve 0.5 dB hot.
     * Phaser and flanger also: feedback 0.95, a resonance far above unity;
     * the phaser also its feedback full-band (the shelf boost). Drive: its
     * wet 0.5 dB hot. Tremolo: its guarantee is the peak one, so an LFO that
     * overshoots unity. */
    level_result c = { 0.0f, 0.0f, 0.0f, -1, { 0 }, { 0 }, 0.0f };
    if (has_level_curve(k)) {
        c = sw[LB_CURVE_HOT];
        CHECK(c.rms_max > RMS_MARGIN, "%s: control (level curve 0.5 dB hot) passed: %+.3f dB",
              FX_LABEL[k], db(c.rms_max));
        if (g_verbose) {
            printf("  %-8s upper control, level curve hot: RMS %+.3f dB\n", FX_LABEL[k],
                   db(c.rms_max));
        }
    }
    if (k == 1) {
        c = sw[LB_UPPER];
        CHECK(c.rms_max > RMS_MARGIN, "drive: control (+0.5 dB) passed: %+.3f dB", db(c.rms_max));
    } else if (k == 2) {
        c = sw[LB_UPPER];
        CHECK(c.rms_max > RMS_MARGIN, "phaser: control (hot feedback) passed: %+.3f dB",
              db(c.rms_max));
        const level_result fb = sw[LB_UPPER2];
        CHECK(fb.rms_max > RMS_MARGIN, "phaser: control (full-band feedback) passed: %+.3f dB",
              db(fb.rms_max));
        if (g_verbose) {
            printf("  phaser   upper control, full-band feedback: RMS %+.3f dB (%s)\n",
                   db(fb.rms_max), fb.at_signal >= 0 ? LEVEL_SIGNAL_LABEL[fb.at_signal] : "-");
        }
    } else if (k == FX_I_FLANGER) {
        c = sw[LB_UPPER];
        CHECK(c.rms_max > RMS_MARGIN, "flanger: control (hot feedback) passed: %+.3f dB",
              db(c.rms_max));
    } else if (k == 3) {
        c = sw[LB_UPPER];
        CHECK(c.peak_max > 1.0f + 1e-6f, "tremolo: control (overshoot) passed: %+.3f dB",
              db(c.peak_max));
    }
    if (g_verbose && k != 0) {
        printf("  %-8s upper control: RMS %+.3f dB, peak %+.3f dB\n", FX_LABEL[k],
               db(c.rms_max), db(c.peak_max));
    }

    /* Peak, where the effect can promise it, times the makeup (r is already
     * relative to it). The tremolo's gain is in [0, 1]; the drive's limiter
     * holds it under the input's recent peak. The chorus's
     * is the input's peak times its static gain (test_output_is_bounded). The
     * phaser and the flanger cannot: an all-pass moves phase and raises the
     * crest factor, a resonance swells what it crosses, and the static gain
     * adds to that. The ±1 clamp holds for every effect. */
    if (k == 3) {
        CHECK(r.peak_max <= 1.0f + 1e-6f, "tremolo: output peak %.6fx input",
              (double)r.peak_max);
    }
    if (k == 1) {
        CHECK(r.peak_max <= 1.0f + 1e-4f, "drive: output peak %.6fx input", (double)r.peak_max);
        const level_result cp = sw[LB_PEAK];
        CHECK(cp.peak_max > 1.0f + 1e-4f, "drive: control (no ceiling, no limiter) kept the "
              "peak: %+.3f dB", db(cp.peak_max));
        if (g_verbose) {
            printf("  drive    peak control (no ceiling, no limiter): %+.3f dB\n",
                   db(cp.peak_max));
        }
    }

    /* Lower bound: not more than 1 dB under the makeup, on the keys signal at
     * every knob position (the phaser: -1.3 dB, see RMS_FLOOR_PHASER). The other
     * signals are reported by --measure: one static gain per knob position
     * cannot bring white noise and pure tones through a sweeping notch to
     * parity as well. The tremolo
     * cannot meet -1 dB at all without its peak going over the input, so it
     * is held to its own computed level instead (tremolo_floor_db). */
    const level_result cl = sw[LB_LOWER];
    if (k == 3) {
        CHECK(r.floor_margin_db >= 0.0f, "tremolo: %.3f dB under its computed level",
              -(double)r.floor_margin_db);
        CHECK(cl.floor_margin_db < 0.0f, "tremolo: control (top at 0.8) met the floor: %+.3f dB",
              (double)cl.floor_margin_db);
        if (g_verbose) {
            printf("  tremolo  vs computed level: worst %+.3f dB; control (top 0.8) %+.3f dB\n",
                   (double)r.floor_margin_db, (double)cl.floor_margin_db);
        }
        return;
    }
    const float floor = k == 2 ? RMS_FLOOR_PHASER : RMS_FLOOR;
    CHECK(r.rms_min[SIG_I_KEYS] >= floor, "%s: keys %.3f dB at depth %.2f, floor %.1f dB",
          FX_LABEL[k], db(r.rms_min[SIG_I_KEYS]), (double)r.min_depth[SIG_I_KEYS], db(floor));
    CHECK(cl.rms_min[SIG_I_KEYS] < floor, "%s: control (%s) met the floor: %+.3f dB",
          FX_LABEL[k], k == 1 ? "no makeup" : "no level curve", db(cl.rms_min[SIG_I_KEYS]));
    if (g_verbose) {
        printf("  %-8s lower control (%s): keys %+.3f dB\n", FX_LABEL[k],
               k == 1 ? "no makeup" : "no level curve", db(cl.rms_min[SIG_I_KEYS]));
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
        run_period(&e, l, r, ol, or_, PERIOD, BIT(1), 1.0f);
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
                    /* Relative to the chain's makeup at full: that gain is
                     * static, the drive's own makeup is what may pump. */
                    worst = fmaxf(worst, out_peak / (ref * makeup_gain(1.0f)));
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
        printf("  drive    level step: worst windowed peak out/(in x makeup) %+.2f dB "
               "(control, no ceiling: %+.2f dB)\n", 20.0 * log10(w), 20.0 * log10(c));
    }
    CHECK(w <= 1.122f, "drive: after a 40 dB drop the output peak reached %.3fx the input's", (double)w);
    CHECK(c > 1.122f, "drive: control (no ceiling) passed the level step too: %.3f", (double)c);
}

/* --- 3. clamp ------------------------------------------------------------ */

/* `abuse` 0 plays in-range noise through the first phase instead: the
 * reference for what "recovered" should measure, with every LFO in step. */
static size_t clamp_violations(uint32_t fx, float *recovered, int abuse)
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
        run_period(&e, l, r, ol, or_, PERIOD, fx, 0.1f + (float)(p % 10) / 10.0f);
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
        run_period(&e, l, r, ol, or_, PERIOD, fx, 0.5f);
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
    size_t bad = clamp_violations(BIT(k), &rec, 1);
    (void)clamp_violations(BIT(k), &fresh, 0);
    CHECK(bad == 0, "%s: %zu samples non-finite or beyond ±1.0", FX_LABEL[k], bad);
    /* And it comes back to exactly what a fresh engine does, within 0.3 dB: a
     * NaN that sat in a filter or delay memory would leave the effect silent
     * or off level. */
    double off_db = fabs(db(rec / fresh));
    CHECK(rec > 0.3f && off_db < 0.3, "%s: output %.3f of normal after NaN/Inf input, %.2f dB "
          "from a fresh engine — a memory stayed poisoned", FX_LABEL[k], (double)rec, off_db);
    fx_test_no_clamp = 1;
    size_t c = clamp_violations(BIT(k), &rec, 1);
    fx_test_no_clamp = 0;
    CHECK(c > 0, "%s: control (clamp off) passed too", FX_LABEL[k]);
    if (has_level_curve(k)) {
        /* Control for the recovery criterion: the level curve off. */
        float stuck = 0.0f;
        fx_test_no_level = 1;
        (void)clamp_violations(BIT(k), &stuck, 0);
        fx_test_no_level = 0;
        CHECK(fabs(db(stuck / fresh)) >= 0.3, "%s: control (level curve off) recovered "
              "\"normally\" too: %.2f dB", FX_LABEL[k], fabs(db(stuck / fresh)));
        if (g_verbose) {
            printf("  %-8s NaN recovery: %.3f dB from fresh; control (stage stuck) %.2f dB\n",
                   FX_LABEL[k], off_db, fabs(db(stuck / fresh)));
        }
    }
}

/* --- 4. long run clean ------------------------------------------------------ */

static clean_result long_run(const fx_effect_desc *table, uint32_t fx)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    clean_result c = { 0, 0, 0 };
    const int periods = (int)(LONG_RUN_S * RATE / PERIOD);
    for (int p = 0; p < periods; p++) {
        float level = (p % 500 < 400) ? 1e-3f * (float)(1 + p % 7) : 1e-30f;
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = level * noise();
            r[i] = level * noise();
        }
        /* Depth walks through the range, including 0 (bypass and re-entry). */
        float depth = (float)((p / 50) % 5) / 4.0f;
        run_period(&e, l, r, ol, or_, PERIOD, fx, depth);
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
        run_period(&e, l, r, ol, or_, PERIOD, fx, 0.7f);
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
    clean_result c = long_run(FX_EFFECTS, BIT(k));
    CHECK(clean(c), "%s: %.0f s run: %zu non-finite, %zu subnormal, %zu residue after silence",
          FX_LABEL[k], (double)LONG_RUN_S, c.nonfinite, c.subnormal, c.residue);
    const fx_effect_desc *broken = k == 1 ? &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_FLUSH]
                                 : k == 2 ? &FX_TEST_MUTANTS[FX_MUT_PHASER_NO_FLUSH]
                                 : k == FX_I_FLANGER ? &FX_TEST_MUTANTS[FX_MUT_FLANGER_NO_FLUSH]
                                          : &LEAKY;   /* the tremolo has no memory to flush */
    clean_result m = long_run(table_with(k, broken), BIT(k));
    CHECK(!clean(m), "%s: control (%s) also came out clean", FX_LABEL[k], broken->name);
    if (g_verbose) {
        printf("  %-8s %.0f s: clean; control %s: %zu non-finite, %zu subnormal, %zu residue\n",
               FX_LABEL[k], (double)LONG_RUN_S, broken->name, m.nonfinite, m.subnormal, m.residue);
    }
}

/* --- 5 & 6. depth steps and swaps ---------------------------------------- */

static steps measure_steps_sig(fx_engine *e, const uint32_t *masks, const float *depths,
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
            run_period(e, l, r, ol, or_, PERIOD, masks[seg], depths[seg]);
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
    const uint32_t effects[] = { BIT(k), BIT(k), BIT(k), BIT(k), BIT(k) };
    const float depths[] = { 0.0f, 1.0f, 0.0f, 0.5f, 1.0f };
    float amp, hz;
    step_signal(k, &amp, &hz);
    /* The settled reference is the largest step the steady signal makes, so
     * it must see the whole of the flanger's 5 s sweep (as level_window_s's
     * windows do): 0.7 s of it read 1.22x under the transitions' worst before
     * the chain's makeup, and 1.28x once a full-depth transition was 1 dB
     * louder than the half-depth segment that set the reference. 1000
     * periods (5.3 s), 940 of them settled (5.0 s). */
    const int seg_periods = k == FX_I_FLANGER ? 1000 : 190;
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    steps s = measure_steps_sig(&e, effects, depths, 5, seg_periods, 60, amp, hz);
    fx_engine_free(&e);
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 0.0, XFADE_MS) == 0, "init");
    steps c = measure_steps_sig(&e, effects, depths, 5, seg_periods, 60, amp, hz);
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
            const uint32_t effects[] = { BIT(a), BIT(b), BIT(a), BIT(b) };
            const float depths[] = { 0.6f, 0.6f, 0.6f, 0.6f };
            /* Quiet and low so the drive's edges stay below a click. */
            fx_engine e;
            CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0,
                  "init");
            steps s = measure_steps_sig(&e, effects, depths, 4, 190, 60, 0.1f, 110.0f);
            int landed = e.on[b] == 1.0f && e.on[a] == 0.0f;
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
        run_period(&e, l, r, ol, or_, PERIOD * 3, BIT(1), 1.0f);
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

/* --- 9. feedback stability (phaser, flanger) ------------------------------ */

/* 115.2 s (FEEDBACK_WINDOWS) at full depth with the clamp off, so the clamp
 * cannot hide a loop that grows: keys at 0.5 with a 0.1 s burst of noise at the start of
 * every 9.6 s window (four keys loops), then 2 s of silence. The RMS out/in of
 * each window against the first's, the largest output sample against the
 * input's, non-finite samples, and non-zero samples in the last second of the
 * silence. A loop at or above unity gain grows, overflows, or rings on. */
typedef struct {
    double growth_db, peak_db;
    size_t nonfinite, residue;
} stability;

static stability feedback_stability(const fx_effect_desc *table, int k)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    signal_gen g = { SIG_KEYS, 0.5f, 0.0f, 0.0 };
    const int window = (int)(9.6f * RATE / PERIOD), windows = FEEDBACK_WINDOWS, burst = (int)(0.1f * RATE / PERIOD);
    stability st = { 0.0, -1e9, 0, 0 };
    double first = 0.0, sum_in = 0.0, sum_out = 0.0;
    float peak_in = 0.0f, peak_out = 0.0f;
    fx_test_no_clamp = 1;
    for (int p = 0; p < window * windows; p++) {
        sig_fill(&g, l, r, PERIOD);
        if (p % window < burst) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                l[i] += 0.4f * noise();
                r[i] += 0.4f * noise();
            }
        }
        run_period(&e, l, r, ol, or_, PERIOD, BIT(k), 1.0f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            st.nonfinite += !isfinite(ol[i]) + !isfinite(or_[i]);
            peak_in = fmaxf(peak_in, fmaxf(fabsf(l[i]), fabsf(r[i])));
            float po = fmaxf(fabsf(ol[i]), fabsf(or_[i]));
            peak_out = po > peak_out || po != po ? po : peak_out;
            sum_in += (double)l[i] * l[i] + (double)r[i] * r[i];
            sum_out += (double)ol[i] * ol[i] + (double)or_[i] * or_[i];
        }
        if ((p + 1) % window == 0) {
            double ratio = 10.0 * log10(sum_out / sum_in);
            if (p + 1 == window) {
                first = ratio;
            }
            double grow = ratio - first;
            st.growth_db = grow > st.growth_db || grow != grow ? grow : st.growth_db;
            sum_in = sum_out = 0.0;
        }
    }
    st.peak_db = 20.0 * log10((double)peak_out / (double)peak_in);
    memset(l, 0, sizeof(l));
    memset(r, 0, sizeof(r));
    const int silence = (int)(2.0f * RATE / PERIOD);
    for (int p = 0; p < silence; p++) {
        run_period(&e, l, r, ol, or_, PERIOD, BIT(k), 1.0f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            st.nonfinite += !isfinite(ol[i]) + !isfinite(or_[i]);
            if (p >= silence / 2) {
                st.residue += (ol[i] != 0.0f) + (or_[i] != 0.0f);
            }
        }
    }
    fx_test_no_clamp = 0;
    fx_engine_free(&e);
    return st;
}

/* Limits: no window more than 1 dB above the first, no sample more than
 * 12 dB over the input's peak (a resonance swells a tone, it does not
 * explode), exact silence a second after the input stops. */
static int stable(stability st)
{
    return st.nonfinite == 0 && st.growth_db < 1.0 && st.peak_db < 12.0 && st.residue == 0;
}

static void test_feedback_stability(int k)
{
    const fx_effect_desc *runaway = k == 2 ? &FX_TEST_MUTANTS[FX_MUT_PHASER_RUNAWAY]
                                           : &FX_TEST_MUTANTS[FX_MUT_FLANGER_RUNAWAY];
    stability st = feedback_stability(FX_EFFECTS, k);
    stability c = feedback_stability(table_with(k, runaway), k);
    if (g_verbose) {
        printf("  %-8s feedback, %.1f s at full depth, clamp off: growth %+.2f dB, peak %+.2f dB "
               "over input, %zu non-finite, %zu residue; control (%s) growth %+.1f dB, peak "
               "%+.1f dB, %zu non-finite, %zu residue\n", FX_LABEL[k], 9.6 * FEEDBACK_WINDOWS,
               st.growth_db, st.peak_db,
               st.nonfinite, st.residue, runaway->name, c.growth_db, c.peak_db, c.nonfinite,
               c.residue);
    }
    CHECK(stable(st), "%s: feedback not stable at full depth: growth %+.2f dB, peak %+.2f dB, "
          "%zu non-finite, %zu residue", FX_LABEL[k], st.growth_db, st.peak_db, st.nonfinite,
          st.residue);
    CHECK(!stable(c), "%s: control (%s) looked stable too", FX_LABEL[k], runaway->name);
}

/* --- 10. strength: the notches are deep (phaser, flanger) ----------------- */

/* Round 5: "too subtle". A sweeping filter is heard through its notches: as
 * one crosses a tone, the tone dips. Tones at 300 Hz, 1 kHz and 3 kHz (0.25,
 * well clear of the clamp) through the effect alone at full depth for a whole
 * sweep; the tone's amplitude is read by demodulating eight-cycle windows
 * every two cycles. Returns the mean over the three tones of each one's
 * deepest dip, in dB re the input. Shipped: phaser -42 / -41 / -20 dB (its
 * notches are shallower near 3 kHz, where the feedback band-pass rolls off),
 * flanger -29 / -34 / -48 dB. Round 4's phaser dipped -16.7 dB at every
 * frequency (its notches were all the same depth). The limit on the mean is
 * -25 dB. */
#define NOTCH_LIMIT_DB -25.0

static double sweep_dip_db(const fx_effect_desc *table, int k, float hz, float seconds)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    enum { MAX_FRAMES = 300000 };
    static float y[MAX_FRAMES];
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    const int settle = (int)(0.4f * RATE / PERIOD);
    const int periods = settle + (int)(seconds * RATE / PERIOD);
    const uint32_t cycle = (uint32_t)(RATE / hz + 0.5f);   /* 160, 48, 16 */
    long t = 0, n = 0;
    for (int p = 0; p < periods; p++) {
        for (uint32_t i = 0; i < PERIOD; i++, t++) {
            l[i] = r[i] = 0.25f * (float)sin(2.0 * M_PI * (double)(t % cycle) / cycle);
        }
        run_period(&e, l, r, ol, or_, PERIOD, BIT(k), 1.0f);
        if (p >= settle) {
            for (uint32_t i = 0; i < PERIOD && n < MAX_FRAMES; i++) {
                y[n++] = ol[i];
            }
        }
    }
    fx_engine_free(&e);
    const long win = 8 * (long)cycle, hop = 2 * (long)cycle;
    double lowest = 1e9;
    for (long start = 0; start + win <= n; start += hop) {
        double re = 0.0, im = 0.0;
        for (long i = 0; i < win; i++) {
            double ph = 2.0 * M_PI * (double)((start + i) % (long)cycle) / cycle;
            re += y[start + i] * cos(ph);
            im += y[start + i] * sin(ph);
        }
        double amp = 2.0 * sqrt(re * re + im * im) / (double)win;
        lowest = fmin(lowest, amp);
    }
    return 20.0 * log10(lowest / 0.25 + 1e-12);
}

static double notch_depth_db(const fx_effect_desc *table, int k, float seconds, double dips[3])
{
    static const float tones[3] = { 300.0f, 1000.0f, 3000.0f };
    double sum = 0.0;
    for (int j = 0; j < 3; j++) {
        dips[j] = sweep_dip_db(table, k, tones[j], seconds);
        sum += dips[j];
    }
    return sum / 3.0;
}

static void test_notch_depth(int k)
{
    /* One whole sweep: 2.5 s for the phaser (0.4 Hz), 5 s for the flanger. */
    const float seconds = k == 2 ? 2.5f : 5.0f;
    double d[3], c[3];
    double depth = notch_depth_db(FX_EFFECTS, k, seconds, d);
    /* Control: the same effect mixed short of the null (wet 0.35, not 0.6),
     * the shallow, "too subtle" kind of sweep. */
    fx_effect_desc weak = FX_EFFECTS[k];
    weak.wet_max = 0.35f;
    double cd = notch_depth_db(table_with(k, &weak), k, seconds, c);
    if (g_verbose) {
        printf("  %-8s notch depth at full, 300 Hz / 1 kHz / 3 kHz: %.1f / %.1f / %.1f dB; "
               "mean %.1f; control (wet 0.35) %.1f / %.1f / %.1f dB, mean %.1f; limit %.0f\n",
               FX_LABEL[k], d[0], d[1], d[2], depth, c[0], c[1], c[2], cd, NOTCH_LIMIT_DB);
    }
    CHECK(depth <= NOTCH_LIMIT_DB, "%s: tones dip only %.1f dB (mean) as the notches sweep past, "
          "limit %.0f", FX_LABEL[k], depth, NOTCH_LIMIT_DB);
    CHECK(cd > NOTCH_LIMIT_DB, "%s: control (wet 0.35) dipped %.1f dB: the check cannot tell a "
          "shallow sweep", FX_LABEL[k], cd);
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
    /* The whole engine, as the client runs it: one effect, then all five. */
    static const uint32_t masks[7] = { 0u, 1u << 0, 1u << 1, 1u << 2, 1u << 3, 1u << 4,
                                       (1u << 5) - 1u };
    static const char *const names[7] = { "bypass", "chorus", "drive", "phaser", "tremolo",
                                          "flanger", "all five" };
    for (int m = 0; m < 7; m++) {
        fx_engine e;
        fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 40.0, 30.0);
        float bl[256], br[256], ol[256], or_[256];
        const int periods = 50000;
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        for (int p = 0; p < periods; p++) {
            for (uint32_t i = 0; i < 256; i++) {
                bl[i] = 0.3f * noise();
                br[i] = 0.3f * noise();
            }
            const float *const in2[FX_CHANNELS] = { bl, br };
            float *const out2[FX_CHANNELS] = { ol, or_ };
            fx_engine_process(&e, in2, out2, 256, masks[m], 0.7f);
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        double ns = ((double)(t1.tv_sec - t0.tv_sec) * 1e9 + (double)(t1.tv_nsec - t0.tv_nsec)) /
                    ((double)periods * 256);
        printf("engine, %-9s %6.1f ns/stereo frame\n", names[m], ns);
        fx_engine_free(&e);
    }
    /* All five with per-effect levels: settled (each depth reached its
     * target, the smoother is a fill), and moving (the levels change every
     * period, so every stage's smoother runs every frame: the worst case). */
    static const float mixed[FX_MAX_EFFECTS] = { 0.8f, 0.35f, 1.0f, 0.15f, 0.6f, 1.0f, 1.0f, 1.0f };
    for (int moving = 0; moving < 2; moving++) {
        fx_engine e;
        fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 40.0, 30.0);
        float bl[256], br[256], ol[256], or_[256], lv[FX_MAX_EFFECTS];
        const int periods = 50000;
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        for (int p = 0; p < periods; p++) {
            for (uint32_t i = 0; i < 256; i++) {
                bl[i] = 0.3f * noise();
                br[i] = 0.3f * noise();
            }
            for (int k = 0; k < FX_MAX_EFFECTS; k++) {
                lv[k] = moving && (p & 1) ? 1.0f - 0.5f * mixed[k] : mixed[k];
            }
            const float *const in2[FX_CHANNELS] = { bl, br };
            float *const out2[FX_CHANNELS] = { ol, or_ };
            fx_engine_process_levels(&e, in2, out2, 256, (1u << 5) - 1u, 0.7f, lv);
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        double ns = ((double)(t1.tv_sec - t0.tv_sec) * 1e9 + (double)(t1.tv_nsec - t0.tv_nsec)) /
                    ((double)periods * 256);
        printf("engine, all five, levels %-8s %6.1f ns/stereo frame\n",
               moving ? "moving" : "settled", ns);
        fx_engine_free(&e);
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
        run_period(&e, l, r, ol, or_, PERIOD * 3, BIT(k), depth);
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
    printf("RMS out/in, dB (both channels), 0.4 s settle + 5 s measured (keys 4.8 s)\n");
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
                level_at(FX_EFFECTS, BIT(k), sgi, knobs[j], &rr, &pr);
                float db = 20.0f * log10f(rr);
                lo = fminf(lo, db);
                hi = fmaxf(hi, db);
                pk = fmaxf(pk, 20.0f * log10f(pr));
                printf(" %+8.2f", (double)db);
            }
            printf(" %+8.2f %+8.2f %+8.2f\n", (double)lo, (double)hi, (double)pk);
        }
    }
    static const uint32_t chains[7] = { 0x3u, 0x5u, 0x7u, 0xFu, 0x14u, 0x11u, 0x1Fu };
    static const char *const chain_names[7] = { "ch+drv", "ch+ph", "ch+dr+ph", "all4", "ph+fl",
                                                "ch+fl", "all5" };
    for (int c = 0; c < 7; c++) {
        for (int j = 1; j < 5; j++) {
            float lo = 1e9f, hi = -1e9f, pk = -1e9f;
            printf("%-8s %4.0f%%", chain_names[c], 100.0 * knobs[j]);
            for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
                float rr, pr;
                level_at(FX_EFFECTS, chains[c], sgi, knobs[j], &rr, &pr);
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

/* The asymmetry (round 4: bias 0.2, back from round 3's 0.1), as its even
 * harmonic: on a 250 Hz tone at 0.2 (moderate playing), full drive, h2 is
 * -21.7 dB re the fundamental; the round-3 bias measured -27.7. Limit -24. */
static void test_drive_asymmetry(void)
{
    double h[7], c[7];
    harmonic_profile(FX_EFFECTS, 1, 0.2f, 1.0f, h);
    harmonic_profile(table_with(1, &FX_TEST_MUTANTS[FX_MUT_DRIVE_ROUND3_BIAS]), 1, 0.2f, 1.0f, c);
    if (g_verbose) {
        printf("  drive    asymmetry, 250 Hz 0.2 at full: h2 %+.1f dB; control (bias 0.1) "
               "%+.1f dB; limit -24\n", h[1], c[1]);
    }
    CHECK(h[1] >= -24.0, "drive: h2 %+.1f dB at 0.2 in, full drive: asymmetry below the 0.2 "
          "bias's", h[1]);
    CHECK(c[1] < -24.0, "drive: control (bias 0.1) passed the asymmetry check: h2 %+.1f", c[1]);
}

/* ========================================================================= */
/* The chain (round 4)                                                        */
/* ========================================================================= */

#define FX_ALL ((1u << N_FX) - 1u)
#define BIT_DRIVE BIT(1)
#define BIT_PHASER BIT(2)
#define BIT_CHORUS BIT(0)
#define BIT_TREMOLO BIT(3)
#define BIT_FLANGER BIT(4)

/* --- static level: the output is the #149 mix times a constant ------------ */

/* Effect k alone at a fixed depth, against a second instance of the same
 * effect fed the same keys: after the depth has landed, the engine's output
 * must be the reference mix times one constant (the level curve's value).
 * Returns the residual of the best constant fit, in dB re the output. A gain
 * that moved with the signal or the sweep, as round 3's makeup and limiter
 * did, leaves a residual; a static one leaves rounding. */
static double static_level_residual_db(int k, float depth, float *gain)
{
    const fx_effect_desc *d = &FX_EFFECTS[k];
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    void *ref = calloc(1, d->state_bytes);
    CHECK(ref != NULL && d->init(ref, RATE) == 0, "init ref");
    signal_gen g = { SIG_KEYS, 0.5f, 0.0f, 0.0 };
    float l[FX_CHUNK], r[FX_CHUNK], ol[FX_CHUNK], or_[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK];
    float amt[FX_CHUNK];
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    double so = 0.0, sm = 0.0, smm = 0.0, soo = 0.0;
    const float w = d->wet_max * fminf(1.0f, depth / d->mix_full_at);
    enum { SL_CHUNKS = 3600 };   /* 4.8 s at 48 kHz: two keys loops */
    /* 1 s to settle: the depth smoother lands on its target (DEPTH_SNAP) after
     * ~0.45 s from 0 to 0.7. */
    const int chunks = SL_CHUNKS, settle = (int)(1.0f * RATE / FX_CHUNK);
    static float out_keep[2][SL_CHUNKS][FX_CHUNK];
    static float mix_keep[2][SL_CHUNKS][FX_CHUNK];
    for (int c = 0; c < chunks; c++) {
        sig_fill(&g, l, r, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            amt[i] = depth;
        }
        d->process(ref, in, wet, amt, FX_CHUNK);   /* reads l, r before the engine */
        run_period(&e, l, r, ol, or_, FX_CHUNK, BIT(k), depth);
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            float ml = l[i] + w * (wl[i] - l[i]);
            float mr = r[i] + w * (wr[i] - r[i]);
            out_keep[0][c][i] = ol[i];
            out_keep[1][c][i] = or_[i];
            mix_keep[0][c][i] = ml;
            mix_keep[1][c][i] = mr;
            if (c >= settle) {
                so += (double)ol[i] * ml + (double)or_[i] * mr;
                smm += (double)ml * ml + (double)mr * mr;
                soo += (double)ol[i] * ol[i] + (double)or_[i] * or_[i];
            }
        }
    }
    double g0 = so / smm;
    for (int c = settle; c < chunks; c++) {
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            for (int ch = 0; ch < 2; ch++) {
                double res = out_keep[ch][c][i] - g0 * mix_keep[ch][c][i];
                sm += res * res;
            }
        }
    }
    free(ref);
    fx_engine_free(&e);
    *gain = (float)g0;
    return 10.0 * log10(sm / soo + 1e-30);
}

static void test_static_level(void)
{
    static const int ks[3] = { 0, 2, FX_I_FLANGER };
    for (int j = 0; j < 3; j++) {
        int k = ks[j];
        float gain = 0.0f, cg = 0.0f;
        double res = static_level_residual_db(k, 0.7f, &gain);
        fx_test_level_wobble = 1;
        double c = static_level_residual_db(k, 0.7f, &cg);
        fx_test_level_wobble = 0;
        if (g_verbose) {
            printf("  %-8s static level at 70%%: gain %+.2f dB, residual %.1f dB; control "
                   "(gain wobbling +-0.5 dB) %.1f dB\n", FX_LABEL[k], db(gain), res, c);
        }
        CHECK(res < -100.0, "%s: output is not the #149 mix times a constant: residual %.1f dB",
              FX_LABEL[k], res);
        CHECK(gain > 1.0f, "%s: no level gain at 70%%: %+.2f dB", FX_LABEL[k], db(gain));
        CHECK(c >= -100.0, "%s: control (wobbling gain) passed too: %.1f dB", FX_LABEL[k], c);
    }
}

/* --- order ----------------------------------------------------------------- */

/* Five test effects that do not commute, in the shipped table's slots: the
 * drive's clips at ±0.1, the phaser's scales by 0.25, the flanger's is
 * 2x + 0.03, the chorus's adds 0.01, the tremolo's inverts. A constant 0.5
 * through all five in the shipped order (drive, phaser, flanger, chorus,
 * tremolo) is -(2 (0.25 * 0.1) + 0.03 + 0.01) = -0.09; swapping any two
 * neighbours gives something else. */
static int op_init(void *s, float r) { (void)s; (void)r; return 0; }
#define OP(name, expr)                                                                 \
    static void name(void *s, const float *const in[FX_CHANNELS],                       \
                     float *const wet[FX_CHANNELS], const float *amount, uint32_t n)    \
    {                                                                                   \
        (void)s;                                                                        \
        (void)amount;                                                                   \
        for (uint32_t i = 0; i < n; i++) {                                              \
            for (int ch = 0; ch < FX_CHANNELS; ch++) {                                  \
                float x = in[ch][i];                                                    \
                wet[ch][i] = (expr);                                                    \
            }                                                                           \
        }                                                                               \
    }
OP(op_add, x + 0.01f)
OP(op_clip, fminf(0.1f, fmaxf(-0.1f, x)))
OP(op_scale, 0.25f * x)
OP(op_neg, -x)
OP(op_affine, 2.0f * x + 0.03f)

static float chain_order_output(void)
{
    const fx_effect_desc ops[N_FX] = {
        { "add", 1.0f, 1.0f, 0.0f, 0.0f, 1, op_init, op_add, NULL },
        { "clip", 1.0f, 1.0f, 0.0f, 0.0f, 1, op_init, op_clip, NULL },
        { "scale", 1.0f, 1.0f, 0.0f, 0.0f, 1, op_init, op_scale, NULL },
        { "neg", 1.0f, 1.0f, 0.0f, 0.0f, 1, op_init, op_neg, NULL },
        { "affine", 1.0f, 1.0f, 0.0f, 0.0f, 1, op_init, op_affine, NULL },
    };
    fx_engine e;
    CHECK(fx_engine_init(&e, ops, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    fx_test_no_makeup = 1;   /* the order, not the chain's gain (test_makeup_*) */
    for (int p = 0; p < 200; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = r[i] = 0.5f;
        }
        run_period(&e, l, r, ol, or_, PERIOD, FX_ALL, 1.0f);
    }
    fx_test_no_makeup = 0;
    fx_engine_free(&e);
    return ol[PERIOD - 1];
}

static void test_chain_order(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    CHECK(FX_CHAIN_LEN == N_FX && e.order[0] == 1 && e.order[1] == 2 && e.order[2] == 4 &&
          e.order[3] == 0 && e.order[4] == 3,
          "run order is not drive, phaser, flanger, chorus, tremolo");
    fx_engine_free(&e);
    float y = chain_order_output();
    fx_test_reverse_order = 1;
    float c = chain_order_output();
    fx_test_reverse_order = 0;
    if (g_verbose) {
        printf("  chain    order: %.6f (drive, phaser, flanger, chorus, tremolo = -0.09); "
               "control (reversed) %.6f\n", (double)y, (double)c);
    }
    CHECK(fabsf(y + 0.09f) < 1e-6f, "chain order: 0.5 came out %.6f, not -0.09", (double)y);
    CHECK(fabsf(c + 0.09f) >= 1e-6f, "chain order: control (reversed) gave -0.09 too");
}

/* --- bypass: an empty mask --------------------------------------------- */

static size_t bypass_mismatches_mask(uint32_t mask, float depth, uint32_t first)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD], keep_l[PERIOD], keep_r[PERIOD];
    size_t mismatches = 0;
    /* `first` plays for 0.5 s before, at the same depth: an empty mask after
     * a full one must be the input again once the fade-out has run. */
    for (int p = 0; p < 100; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.5f * noise();
            r[i] = 0.5f * noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, first, depth);
    }
    for (int p = 0; p < 300; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.5f * noise();
            r[i] = 0.5f * noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, mask, depth);
        if (p < 10) {
            continue;   /* the 30 ms fade-out */
        }
        mismatches += memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0;
        memcpy(keep_l, l, sizeof(l));
        memcpy(keep_r, r, sizeof(r));
        run_period(&e, l, r, l, r, PERIOD, mask, depth);
        mismatches += memcmp(keep_l, l, sizeof(l)) != 0 || memcmp(keep_r, r, sizeof(r)) != 0;
    }
    fx_engine_free(&e);
    return mismatches;
}

static void test_chain_bypass(void)
{
    size_t m0 = bypass_mismatches_mask(0u, 1.0f, 0u);
    size_t m1 = bypass_mismatches_mask(0u, 1.0f, FX_ALL);
    size_t m2 = bypass_mismatches_mask(FX_ALL, 0.0f, FX_ALL);
    CHECK(m0 == 0, "empty mask at full depth: %zu periods differ from the input", m0);
    CHECK(m1 == 0, "empty mask after all five: %zu periods differ from the input", m1);
    CHECK(m2 == 0, "all five at depth 0: %zu periods differ from the input", m2);
    size_t c = bypass_mismatches_mask(FX_ALL, 1.0f / 127.0f, FX_ALL);
    CHECK(c > 0, "control (all five at depth 1/127) also identical: the comparison is blind");
    if (g_verbose) {
        printf("  chain    bypass: empty mask %zu, after all five %zu, depth 0 %zu differing "
               "periods; control (1/127) %zu\n", m0, m1, m2, c);
    }
}

/* --- toggles crossfade --------------------------------------------------- */

static void test_chain_toggles(void)
{
    const uint32_t masks[] = {
        0u, BIT_DRIVE, BIT_DRIVE | BIT_CHORUS, BIT_CHORUS, FX_ALL, BIT_PHASER | BIT_TREMOLO,
        0u, FX_ALL, BIT_TREMOLO, BIT_FLANGER, BIT_PHASER | BIT_FLANGER, BIT_PHASER,
    };
    const int segs = (int)(sizeof(masks) / sizeof(masks[0]));
    float depths[sizeof(masks) / sizeof(masks[0])];
    for (int i = 0; i < segs; i++) {
        depths[i] = 0.6f;
    }
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    steps s = measure_steps_sig(&e, masks, depths, segs, 190, 60, 0.1f, 110.0f);
    int landed = e.on[2] == 1.0f && e.on[0] == 0.0f && e.on[1] == 0.0f && e.on[3] == 0.0f &&
                 e.on[4] == 0.0f;
    fx_engine_free(&e);
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, 0.0) == 0, "init");
    steps c = measure_steps_sig(&e, masks, depths, segs, 190, 60, 0.1f, 110.0f);
    fx_engine_free(&e);
    if (g_verbose) {
        printf("  chain    toggles: worst %.5f / settled %.5f; control (instant) %.5f / %.5f\n",
               (double)s.worst, (double)s.settled, (double)c.worst, (double)c.settled);
    }
    CHECK(click_free(s), "chain toggles: worst %.5f vs settled %.5f", (double)s.worst,
          (double)s.settled);
    CHECK(landed, "chain toggles did not land on the last mask");
    CHECK(!click_free(c), "chain toggles: control (instant) passed too: %.5f vs %.5f",
          (double)c.worst, (double)c.settled);
}

/* --- never louder, any mask ---------------------------------------------- */

/* One mask (1..31) at 0, 50 and 100 %, every signal; at 0, the control: one
 * hot stage (phaser and chorus 0.5 dB hot) inside the chain. */
static void chain_level_part(int m, void *slot)
{
    level_result *out = slot;
    if (m == 0) {
        fx_test_level_hot = 1;
        *out = level_sweep(FX_EFFECTS, BIT_DRIVE | BIT_PHASER | BIT_CHORUS, MASK_ALL, 2, NULL);
        fx_test_level_hot = 0;
        return;
    }
    *out = level_sweep(FX_EFFECTS, (uint32_t)m, MASK_ALL, 2, NULL);   /* 0, 50, 100 % */
}

static int g_chain_case[FX_ALL + 1];   /* the case holding each mask's part */

static void test_chain_level_bound(int unused)
{
    (void)unused;
    float worst = 0.0f;
    uint32_t at_mask = 0;
    for (uint32_t m = 1; m <= FX_ALL; m++) {
        const level_result *r = slot_of(g_chain_case[m]);
        if (r->rms_max > worst) {
            worst = r->rms_max;
            at_mask = m;
        }
        CHECK(r->rms_max <= RMS_MARGIN, "chain mask %u: output RMS %.4fx input x makeup (depth "
              "%.2f, %s)",
              (unsigned)m, (double)r->rms_max, (double)r->at_depth,
              LEVEL_SIGNAL_LABEL[r->at_signal]);
    }
    const level_result *c = slot_of(g_chain_case[0]);
    CHECK(c->rms_max > RMS_MARGIN, "chain: control (hot level curve) passed: %+.3f dB",
          db(c->rms_max));
    if (g_verbose) {
        printf("  chain    level vs makeup: worst RMS over all 31 masks %+.3f dB (mask %u); "
               "control (hot) %+.3f dB\n", db(worst), (unsigned)at_mask, db(c->rms_max));
    }
}

static void test_chain_clean(void)
{
    float rec = 0.0f, fresh = 0.0f;
    size_t bad = clamp_violations(FX_ALL, &rec, 1);
    (void)clamp_violations(FX_ALL, &fresh, 0);
    CHECK(bad == 0, "chain: %zu samples non-finite or beyond ±1.0", bad);
    double off_db = fabs(db(rec / fresh));
    CHECK(rec > 0.1f && off_db < 0.3, "chain: output %.3f of normal after NaN/Inf, %.2f dB from "
          "a fresh engine", (double)rec, off_db);
    fx_test_no_clamp = 1;
    size_t cc = clamp_violations(FX_ALL, &rec, 1);
    fx_test_no_clamp = 0;
    CHECK(cc > 0, "chain: control (clamp off) passed too");

    clean_result c = long_run(FX_EFFECTS, FX_ALL);
    CHECK(clean(c), "chain: %.0f s run: %zu non-finite, %zu subnormal, %zu residue after silence",
          (double)LONG_RUN_S, c.nonfinite, c.subnormal, c.residue);
    clean_result m = long_run(table_with(2, &FX_TEST_MUTANTS[FX_MUT_PHASER_NO_FLUSH]), FX_ALL);
    CHECK(!clean(m), "chain: control (phaser no flush) also came out clean");
    if (g_verbose) {
        printf("  chain    NaN recovery %.3f dB; %.0f s clean; control (phaser no flush) %zu "
               "non-finite, %zu subnormal, %zu residue\n", off_db, (double)LONG_RUN_S, m.nonfinite,
               m.subnormal, m.residue);
    }
}

/* --- per-effect levels ----------------------------------------------------- */

/* Run `periods` of `sig` through a fresh engine at (mask, depth, levels) and
 * keep the last `keep` periods of the left output in `out`. */
static void levels_run(uint32_t mask, float depth, const float *levels, int periods, int keep,
                       float *out)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    signal_gen g = { SIG_KEYS, 0.5f, 0.0f, 0.0 };
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    const float *saved = g_levels;
    g_levels = levels;
    for (int p = 0; p < periods; p++) {
        sig_fill(&g, l, r, PERIOD);
        run_period(&e, l, r, ol, or_, PERIOD, mask, depth);
        if (p >= periods - keep) {
            memcpy(out + (size_t)(p - (periods - keep)) * PERIOD, ol, sizeof(ol));
        }
    }
    g_levels = saved;
    fx_engine_free(&e);
}

#define LV_PERIODS 120
#define LV_KEEP 40

static int same_output(const float *a, const float *b)
{
    return memcmp(a, b, sizeof(float) * LV_KEEP * PERIOD) == 0;
}

/* d_k = shared x level_k, bit for bit: depth 0.75 at level 0.5 is depth 0.375
 * at level 1 (all three exact in binary), for every effect, from a fresh
 * engine (so the smoothing from 0 is compared too). Control: the same depth
 * at levels 0.5 and 1.0 must differ, or the comparison cannot see a level. */
static void test_level_is_a_depth_product(void)
{
    static float a[LV_KEEP * PERIOD], b[LV_KEEP * PERIOD], c[LV_KEEP * PERIOD];
    float half[FX_MAX_EFFECTS], one[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        half[k] = 0.5f;
        one[k] = 1.0f;
    }
    int controls_differ = 1;
    for (int k = 0; k < N_FX; k++) {
        levels_run(BIT(k), 0.75f, half, LV_PERIODS, LV_KEEP, a);
        levels_run(BIT(k), 0.375f, one, LV_PERIODS, LV_KEEP, b);
        levels_run(BIT(k), 0.75f, one, LV_PERIODS, LV_KEEP, c);
        CHECK(same_output(a, b), "%s: depth 0.75 at level 0.5 is not depth 0.375", FX_LABEL[k]);
        controls_differ &= !same_output(a, c);
        CHECK(!same_output(a, c), "%s: control (level 0.5 vs 1.0) identical: the comparison "
              "cannot see a level", FX_LABEL[k]);
    }
    /* Levels NULL (the `chain` command) and every level 1.0 are one thing. */
    levels_run(FX_ALL, 0.6f, NULL, LV_PERIODS, LV_KEEP, a);
    levels_run(FX_ALL, 0.6f, one, LV_PERIODS, LV_KEEP, b);
    CHECK(same_output(a, b), "all five: levels 1.0 differ from no levels");
    /* Out of range and NaN are clamped: 7 is 1, -1 and NaN are 0. */
    float wild[FX_MAX_EFFECTS], tame[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        wild[k] = k % 3 == 0 ? 7.0f : k % 3 == 1 ? -1.0f : NAN;
        tame[k] = k % 3 == 0 ? 1.0f : 0.0f;
    }
    levels_run(FX_ALL, 0.6f, wild, LV_PERIODS, LV_KEEP, a);
    levels_run(FX_ALL, 0.6f, tame, LV_PERIODS, LV_KEEP, b);
    CHECK(same_output(a, b), "all five: levels 7 / -1 / NaN are not 1 / 0 / 0");
    if (g_verbose) {
        printf("  levels   d = shared x level, bit for bit for all five; controls (0.5 vs 1.0) "
               "differ: %s\n", controls_differ ? "yes" : "NO");
    }
}

/* A stage at level 0 is not in the chain: all five with drive and chorus at
 * level 0 is, bit for bit, phaser + flanger + tremolo alone. Control: level
 * 1/127 must differ. */
static void test_level_zero_is_off(void)
{
    static float a[LV_KEEP * PERIOD], b[LV_KEEP * PERIOD], c[LV_KEEP * PERIOD];
    float lv[FX_MAX_EFFECTS], tiny[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        lv[k] = tiny[k] = 1.0f;
    }
    lv[0] = lv[1] = 0.0f;                  /* chorus, drive */
    tiny[0] = tiny[1] = 1.0f / 127.0f;
    levels_run(FX_ALL, 0.8f, lv, LV_PERIODS, LV_KEEP, a);
    levels_run(BIT_PHASER | BIT_FLANGER | BIT_TREMOLO, 0.8f, NULL, LV_PERIODS, LV_KEEP, b);
    levels_run(FX_ALL, 0.8f, tiny, LV_PERIODS, LV_KEEP, c);
    CHECK(same_output(a, b), "level 0 is not the same as the effect off");
    CHECK(!same_output(c, b), "control (level 1/127) identical to off: the comparison is blind");
}

/* Every level 0 is bypass: the input, bit for bit, at any depth and mask, and
 * again after a ramp down from full levels. Control: levels at 1/127. */
static size_t levels_bypass_mismatches(const float *after)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD], keep_l[PERIOD], keep_r[PERIOD];
    size_t mismatches = 0;
    for (int p = 0; p < 100; p++) {   /* 0.5 s of all five at full levels first */
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.5f * noise();
            r[i] = 0.5f * noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, FX_ALL, 1.0f);
    }
    const float *saved = g_levels;
    g_levels = after;
    for (int p = 0; p < 400; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.5f * noise();
            r[i] = 0.5f * noise();
        }
        run_period(&e, l, r, ol, or_, PERIOD, FX_ALL, 1.0f);
        if (p < 120) {
            continue;   /* the 40 ms depth ramp, down to where it lands on 0 */
        }
        mismatches += memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0;
        memcpy(keep_l, l, sizeof(l));
        memcpy(keep_r, r, sizeof(r));
        run_period(&e, l, r, l, r, PERIOD, FX_ALL, 1.0f);
        mismatches += memcmp(keep_l, l, sizeof(l)) != 0 || memcmp(keep_r, r, sizeof(r)) != 0;
    }
    g_levels = saved;
    fx_engine_free(&e);
    return mismatches;
}

static void test_levels_zero_is_bypass(void)
{
    float zero[FX_MAX_EFFECTS], tiny[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        zero[k] = 0.0f;
        tiny[k] = 1.0f / 127.0f;
    }
    size_t m = levels_bypass_mismatches(zero);
    size_t c = levels_bypass_mismatches(tiny);
    CHECK(m == 0, "all five, every level 0: %zu periods differ from the input", m);
    CHECK(c > 0, "control (every level 1/127) also identical: the comparison is blind");
    if (g_verbose) {
        printf("  levels   every level 0 after full: %zu differing periods; control (1/127) %zu\n",
               m, c);
    }
}

/* Level moves are smoothed like knob moves: steps between segments at a fixed
 * depth, for each effect alone and for all five at once. Control: the same
 * moves with smoothing off must click. */
#define LV_SEGS 6
static const float LV_STEPS[LV_SEGS] = { 1.0f, 0.2f, 1.0f, 0.0f, 0.6f, 1.0f };

static steps measure_level_steps(fx_engine *e, uint32_t mask, float depth, float amp, float hz)
{
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    signal_gen g = { SIG_SINE, amp, hz, 0.0 };
    float prev = 0.0f;
    int have_prev = 0;
    steps s = { 0.0f, 0.0f };
    float lv[FX_MAX_EFFECTS];
    const float *saved = g_levels;
    g_levels = lv;
    for (int seg = 0; seg < LV_SEGS; seg++) {
        /* Each effect moves to its own value, so a chain's stages do not all
         * move together: effect k takes the step k segments later. */
        for (int k = 0; k < FX_MAX_EFFECTS; k++) {
            lv[k] = LV_STEPS[(seg + k) % LV_SEGS];
        }
        for (int p = 0; p < 190; p++) {
            sig_fill(&g, l, r, PERIOD);
            run_period(e, l, r, ol, or_, PERIOD, mask, depth);
            for (uint32_t i = 0; i < PERIOD; i++) {
                if (have_prev) {
                    float step = fabsf(ol[i] - prev);
                    s.worst = fmaxf(s.worst, step);
                    if (p >= 60) {
                        s.settled = fmaxf(s.settled, step);
                    }
                }
                prev = ol[i];
                have_prev = 1;
            }
        }
    }
    g_levels = saved;
    return s;
}

static void test_level_steps_have_no_click(void)
{
    for (int k = 0; k <= N_FX; k++) {
        const int chain = k == N_FX;
        const uint32_t mask = chain ? FX_ALL : BIT(k);
        float amp, hz;
        if (chain) {
            amp = 0.1f;   /* as the toggle check: the drive's edges stay soft */
            hz = 110.0f;
        } else {
            step_signal(k, &amp, &hz);
        }
        const char *label = chain ? "all five" : FX_LABEL[k];
        fx_engine e;
        CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0,
              "init");
        steps s = measure_level_steps(&e, mask, 1.0f, amp, hz);
        fx_engine_free(&e);
        CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 0.0, XFADE_MS) == 0, "init");
        steps c = measure_level_steps(&e, mask, 1.0f, amp, hz);
        fx_engine_free(&e);
        if (g_verbose) {
            printf("  %-8s level steps: worst %.5f / settled %.5f; control (unsmoothed) "
                   "%.5f / %.5f\n", label, (double)s.worst, (double)s.settled, (double)c.worst,
                   (double)c.settled);
        }
        CHECK(click_free(s), "%s level steps: worst %.5f vs settled %.5f", label,
              (double)s.worst, (double)s.settled);
        CHECK(!click_free(c), "%s: control (unsmoothed) passed too: worst %.5f vs settled %.5f",
              label, (double)c.worst, (double)c.settled);
    }
}

/* Never louder than the makeup at any levels, argued down to the cases
 * nothing else covers.
 * A level only sets a stage's depth (d_k = shared x level_k, bit for bit:
 * test_level_is_a_depth_product), so:
 *  - one stage on, any level: that effect at some depth, which its own sweep
 *    (test_pack_level_bound, 21 depths, every signal) already bounds;
 *  - a level 0 stage is not in the chain (test_level_zero_is_off), so "one at
 *    1, the rest 0" is the one-stage case again;
 *  - every level equal (all 1, all 0.5): the chain at one shared depth, which
 *    test_chain_level_bound sweeps over all 31 masks.
 * What is left is unequal levels inside a chain of two or more. The two
 * alternating vectors (and each the other's complement) give every adjacent
 * pair of stages both orders of loud-then-quiet, on every multi-stage mask, at
 * full depth, on loud noise and the keys. Control: the same check with the
 * level curve 0.5 dB hot must fail. A full level grid (31 masks x N^5) is
 * neither needed nor affordable: the self-test runs on every CI push. */
#define LV_VECTORS 2
static const float LEVEL_VECTORS[LV_VECTORS][N_FX] = {
    { 1.0f, 0.3f, 1.0f, 0.3f, 1.0f },       /* by id: chorus, drive, phaser, tremolo, flanger */
    { 0.3f, 1.0f, 0.3f, 1.0f, 0.3f },
};
#define LEVEL_VECTOR_SIGNALS ((1u << 0) | MASK_KEYS)   /* noise.5 and keys */

static int popcount5(uint32_t m)
{
    int n = 0;
    for (; m != 0; m &= m - 1) {
        n++;
    }
    return n;
}

typedef struct {
    float worst;
    uint32_t at_mask;
    int at_vector;
} vector_worst;

/* Masks mask_from..mask_to of two or more stages, level vectors vi_from..
 * vi_to, in that order: the first strictly loudest wins. */
static vector_worst levels_vector_worst(uint32_t mask_from, uint32_t mask_to, int vi_from,
                                        int vi_to)
{
    vector_worst w = { 0.0f, 0u, -1 };
    float lv[FX_MAX_EFFECTS];
    const float *saved = g_levels;
    g_levels = lv;
    for (int vi = vi_from; vi <= vi_to; vi++) {
        for (int k = 0; k < FX_MAX_EFFECTS; k++) {
            lv[k] = k < N_FX ? LEVEL_VECTORS[vi][k] : 1.0f;
        }
        for (uint32_t m = mask_from; m <= mask_to; m++) {
            if (popcount5(m) < 2) {
                continue;   /* one stage: its own depth sweep covers it (above) */
            }
            for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
                if (!(LEVEL_VECTOR_SIGNALS & (1u << sgi))) {
                    continue;
                }
                float rr, pr;
                level_at(FX_EFFECTS, m, sgi, 1.0f, &rr, &pr);
                rr /= makeup_gain(makeup_m(m, 1.0f));   /* the loudest stage's level */
                if (rr > w.worst) {
                    w.worst = rr;
                    w.at_mask = m;
                    w.at_vector = vi;
                }
            }
        }
    }
    g_levels = saved;
    return w;
}

/* Part `arg` = vector * 32 + mask, in a worker; LV_CONTROL_PART is the
 * control: the level curve 0.5 dB hot, phaser and chorus, both vectors. */
#define LV_CONTROL_PART (LV_VECTORS * 32)
static void levels_never_louder_part(int arg, void *slot)
{
    vector_worst *out = slot;
    if (arg == LV_CONTROL_PART) {
        fx_test_level_hot = 1;
        *out = levels_vector_worst(BIT_PHASER | BIT_CHORUS, BIT_PHASER | BIT_CHORUS, 0,
                                   LV_VECTORS - 1);
        fx_test_level_hot = 0;
        return;
    }
    const uint32_t m = (uint32_t)(arg % 32);
    *out = levels_vector_worst(m, m, arg / 32, arg / 32);
}

static int g_lv_case[LV_CONTROL_PART + 1];   /* -1: no such part (one stage) */

static void test_levels_never_louder(int unused)
{
    (void)unused;
    vector_worst w = { 0.0f, 0u, -1 };
    for (int arg = 0; arg < LV_CONTROL_PART; arg++) {
        if (g_lv_case[arg] < 0) {
            continue;
        }
        const vector_worst *part = slot_of(g_lv_case[arg]);
        if (part->worst > w.worst) {
            w = *part;
        }
    }
    CHECK(w.worst <= RMS_MARGIN, "levels: output RMS %+.3f dB over input x makeup (mask %u, level "
          "vector %d)", db(w.worst), (unsigned)w.at_mask, w.at_vector);
    const vector_worst *c = slot_of(g_lv_case[LV_CONTROL_PART]);
    CHECK(c->worst > RMS_MARGIN, "levels: control (hot level curve) passed: %+.3f dB",
          db(c->worst));
    if (g_verbose) {
        printf("  levels   worst RMS vs makeup over 26 multi-stage masks x %d alternating level "
               "vectors: %+.3f dB (mask %u, vector %d); control (hot) %+.3f dB\n", LV_VECTORS,
               db(w.worst), (unsigned)w.at_mask, w.at_vector, db(c->worst));
    }
}

/* A stage switched on later starts at its own depth (the knob times its
 * level), whether the engine was running other stages or bypassed: its depth
 * followed the knob while it was off, so the enable crossfade is its only
 * transition. An exact state check, so it has no broken twin here; the
 * mutation run (a stage whose depth stays where it was while off) is its
 * negative control. */
static void test_a_stage_enabled_later_starts_at_its_depth(void)
{
    float lv[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        lv[k] = 1.0f;
    }
    lv[1] = 0.5f;   /* drive */
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    const float *saved = g_levels;
    g_levels = lv;
    for (int first = 0; first < 2; first++) {   /* chorus running, then bypass (empty mask) */
        fx_engine e;
        CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
        for (int p = 0; p < 60; p++) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                l[i] = r[i] = 0.3f * noise();
            }
            run_period(&e, l, r, ol, or_, PERIOD, first == 0 ? BIT(0) : 0u, 0.6f);
        }
        run_period(&e, l, r, ol, or_, PERIOD, (first == 0 ? BIT(0) : 0u) | BIT_DRIVE, 0.6f);
        CHECK(e.depth[1] == 0.6f * 0.5f, "drive switched on after %s starts at depth %.6f, not 0.3",
              first == 0 ? "the chorus" : "bypass", (double)e.depth[1]);
        fx_engine_free(&e);
    }
    g_levels = saved;
}

/* --- the chain's makeup ---------------------------------------------------- */

/* RMS of the engine's output over the same chain with the makeup off, same
 * input, same start: keys at 0.35 (about -9 dBFS, the device-mode level), 0.4 s
 * to settle, 0.6 s measured. `broken` runs the measured engine with the makeup
 * off too: the control. The two engines differ only by G, so the ratio is the
 * makeup itself, wherever the clamp does not bite. */
static float makeup_ratio(uint32_t fx, float depth, int broken)
{
    fx_engine a, b;
    CHECK(fx_engine_init(&a, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    CHECK(fx_engine_init(&b, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    signal_gen g = { SIG_KEYS, 0.35f, 0.0f, 0.0 };
    float l[PERIOD], r[PERIOD], al[PERIOD], ar[PERIOD], bl[PERIOD], br[PERIOD];
    double sa = 0.0, sb = 0.0;
    const int settle = (int)(0.4f * RATE / PERIOD), measure = (int)(0.6f * RATE / PERIOD);
    for (int p = 0; p < settle + measure; p++) {
        sig_fill(&g, l, r, PERIOD);
        fx_test_no_makeup = broken;
        run_period(&a, l, r, al, ar, PERIOD, fx, depth);
        fx_test_no_makeup = 1;
        run_period(&b, l, r, bl, br, PERIOD, fx, depth);
        fx_test_no_makeup = 0;
        if (p >= settle) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                sa += (double)al[i] * al[i] + (double)ar[i] * ar[i];
                sb += (double)bl[i] * bl[i] + (double)br[i] * br[i];
            }
        }
    }
    fx_engine_free(&a);
    fx_engine_free(&b);
    return (float)sqrt(sa / sb);
}

/* The makeup is there, once per chain, and scales with depth. At full depth,
 * each effect alone and all five: at least +0.7 dB over the same chain
 * without it, and not more than FX_MAKEUP_DB + 0.1 dB (five stages each
 * adding it would be +5). At half depth: within 0.1 dB of half of it (a
 * makeup that ignored depth would be the whole of it). The upper bounds
 * elsewhere hold the output to the makeup; this is the bound under it, which
 * a makeup that silently did nothing would fail. Control: the makeup off. */
#define MAKEUP_FLOOR_DB 0.7
static void test_makeup_is_applied(void)
{
    static const uint32_t masks[N_FX + 1] = { BIT(0), BIT(1), BIT(2), BIT(3), BIT(4), FX_ALL };
    for (int j = 0; j <= N_FX; j++) {
        const char *label = j == N_FX ? "all five" : FX_LABEL[j];
        double full = db(makeup_ratio(masks[j], 1.0f, 0));
        double half = db(makeup_ratio(masks[j], 0.5f, 0));
        if (g_verbose) {
            printf("  %-8s makeup over the same chain without it: full %+.3f dB, half %+.3f dB\n",
                   label, full, half);
        }
        CHECK(full >= MAKEUP_FLOOR_DB && full <= FX_MAKEUP_DB + 0.1, "%s: makeup at full depth "
              "%+.3f dB, wanted %+.1f..%+.1f", label, full, MAKEUP_FLOOR_DB, FX_MAKEUP_DB + 0.1);
        CHECK(fabs(half - 0.5 * FX_MAKEUP_DB) <= 0.1, "%s: makeup at half depth %+.3f dB, "
              "wanted %+.2f", label, half, 0.5 * FX_MAKEUP_DB);
    }
    double c = db(makeup_ratio(FX_ALL, 1.0f, 1));
    if (g_verbose) {
        printf("  all five makeup control (makeup off): full %+.3f dB\n", c);
    }
    CHECK(c < MAKEUP_FLOOR_DB, "makeup: control (makeup off) met the floor: %+.3f dB", c);
}

/* The makeup moves smoothly. The generic click criterion (largest step vs
 * the settled signal's) is too coarse for a 1 dB gain step on a resonant
 * effect: on the phaser and on all five, a makeup that jumped to its target
 * passed it. So this reads the gain itself: two engines, one with the makeup
 * off, the same input and the same moves (depth, levels, mask). Their ratio,
 * frame by frame, is G(m) exactly (both clamp-free at this level), so its
 * largest change per frame (across any frames skipped near a zero of the
 * output) is the makeup's slew. Smoothed,
 * it is under 0.001 dB a frame (the depth's one-pole: 1 dB x 1/1920; the
 * enable ramp: 1 dB / 1440). Limit 0.005 dB. The ratio must also span most of
 * the makeup (0..1 dB), or the run saw nothing move. Control: the makeup
 * following the targets. */
#define MK_SEGS 9
#define MK_PERIODS 60   /* 0.32 s: the 40 ms smoother and the 30 ms ramp land */
#define MAKEUP_SLEW_DB 0.005

typedef struct {
    double slew_db;   /* largest frame-to-frame change of the gain */
    double lo_db, hi_db;
} makeup_motion;

static makeup_motion makeup_moves(int j, int broken)
{
    /* Effect j alone, or (j == N_FX) all five with the alternating levels, so
     * a mask change moves m too. */
    const int chain = j == N_FX;
    const uint32_t all = chain ? FX_ALL : BIT(j);
    static const float seg_depth[MK_SEGS] = { 0.0f, 1.0f, 0.3f, 1.0f, 1.0f, 1.0f, 1.0f, 1.0f, 0.6f };
    float lv[FX_MAX_EFFECTS];
    fx_engine a, b;
    CHECK(fx_engine_init(&a, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    CHECK(fx_engine_init(&b, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    signal_gen g = { SIG_SINE, 0.2f, 220.0f, 0.0 };
    float l[PERIOD], r[PERIOD], al[PERIOD], ar[PERIOD], bl[PERIOD], br[PERIOD];
    makeup_motion mm = { 0.0, 1e9, -1e9 };
    double prev = 0.0;
    long t = 0, t_prev = -1;
    const float *saved = g_levels;
    g_levels = lv;
    for (int seg = 0; seg < MK_SEGS; seg++) {
        uint32_t mask = all;
        for (int k = 0; k < FX_MAX_EFFECTS; k++) {
            lv[k] = chain && k < N_FX ? LEVEL_VECTORS[0][k] : 1.0f;
        }
        if (seg == 3) {
            lv[chain ? 0 : j] = 0.2f;              /* a level step down */
            if (chain) {
                lv[2] = lv[4] = 0.2f;              /* every level-1 stage */
            }
        } else if (seg == 4) {
            mask = chain ? BIT_TREMOLO : 0u;       /* toggled off (chain: to m = 0.3) */
        } else if (seg == 6) {
            for (int k = 0; k < FX_MAX_EFFECTS; k++) {
                lv[k] = 0.0f;                      /* every level to 0 */
            }
        }
        for (int p = 0; p < MK_PERIODS; p++) {
            sig_fill(&g, l, r, PERIOD);
            fx_test_makeup_unsmoothed = broken;
            run_period(&a, l, r, al, ar, PERIOD, mask, seg_depth[seg]);
            fx_test_makeup_unsmoothed = 0;
            fx_test_no_makeup = 1;
            run_period(&b, l, r, bl, br, PERIOD, mask, seg_depth[seg]);
            fx_test_no_makeup = 0;
            for (uint32_t i = 0; i < PERIOD; i++, t++) {
                if (fabsf(bl[i]) < 1e-3f) {
                    continue;   /* too near zero for the ratio to mean much */
                }
                double gdb = 20.0 * log10((double)al[i] / (double)bl[i]);
                if (t_prev >= 0) {   /* per frame, across any frames skipped */
                    mm.slew_db = fmax(mm.slew_db, fabs(gdb - prev) / (double)(t - t_prev));
                }
                mm.lo_db = fmin(mm.lo_db, gdb);
                mm.hi_db = fmax(mm.hi_db, gdb);
                prev = gdb;
                t_prev = t;
            }
        }
    }
    g_levels = saved;
    fx_engine_free(&a);
    fx_engine_free(&b);
    return mm;
}

static void test_makeup_moves_smoothly(void)
{
    for (int j = 0; j <= N_FX; j++) {
        const char *label = j == N_FX ? "all five" : FX_LABEL[j];
        makeup_motion m = makeup_moves(j, 0);
        makeup_motion c = makeup_moves(j, 1);
        if (g_verbose) {
            printf("  %-8s makeup moves: slew %.5f dB/frame over %+.3f..%+.3f dB; control "
                   "(makeup unsmoothed) %.5f dB/frame\n", label, m.slew_db, m.lo_db, m.hi_db,
                   c.slew_db);
        }
        CHECK(m.slew_db <= MAKEUP_SLEW_DB, "%s: the makeup stepped %.5f dB in one frame",
              label, m.slew_db);
        CHECK(m.lo_db <= 0.1 && m.hi_db >= FX_MAKEUP_DB - 0.1, "%s: the makeup only spanned "
              "%+.3f..%+.3f dB: the run did not see it move", label, m.lo_db, m.hi_db);
        CHECK(c.slew_db > MAKEUP_SLEW_DB, "%s: control (makeup unsmoothed) passed too: %.5f "
              "dB/frame", label, c.slew_db);
    }
}

static void print_parse(const char *text)
{
    uint32_t mask = 0;
    float depth = 0.0f;
    float levels[FX_MAX_EFFECTS];
    if (!fx_parse_control(text, &mask, &depth, levels)) {
        printf("reject\n");
        return;
    }
    printf("ok %u %.6f", (unsigned)mask, (double)depth);
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        printf(" %.6f", (double)levels[k]);
    }
    printf("\n");
}

/* ========================================================================= */
/* The runner                                                                 */
/* ========================================================================= */
/*
 * Every check is a case, run in its own forked worker, up to FX_SELFTEST_JOBS
 * at a time (default and cap: the cores this process may run on; 1 is the
 * serial run). A worker starts from the parent's state as it was before any
 * check ran: the same noise seed, every test switch off, the keys signal
 * built. So a case's result cannot depend on which cases ran before it, or
 * where, and the serial and parallel runs print the same bytes (checked by
 * tests/test_fx_host.py, numbers included).
 *
 * Output is printed in case order, never completion order: each worker's
 * stdout and stderr go to its own files, copied out when every case before
 * it has been printed. A check split across workers (the level sweeps) ends
 * in a parent-side case that folds the parts back together, in a fixed order,
 * when its turn to print comes. A worker that crashes is a failed check.
 *
 * FX_SELFTEST_TIMES=1 adds each case's wall time to stderr, after its output.
 */

typedef struct {
    char name[40];
    void (*run)(int arg, void *slot);   /* in a worker */
    void (*fold)(int arg);              /* in the parent, at its turn */
    int arg;
    int weight;                         /* launch heavy cases first */
} st_case;

#define MAX_CASES 512
static st_case g_cases[MAX_CASES];
static int g_ncases;
static int *g_case_failures;   /* shared: each worker's failed-check count */

static int add_case(const char *name, int arg, void (*run)(int, void *), void (*fold)(int),
                    int weight)
{
    if (g_ncases == MAX_CASES) {
        fprintf(stderr, "fx-selftest: more than %d cases\n", MAX_CASES);
        exit(2);
    }
    st_case *c = &g_cases[g_ncases];
    snprintf(c->name, sizeof(c->name), "%s", name);
    c->run = run;
    c->fold = fold;
    c->arg = arg;
    c->weight = weight;
    return g_ncases++;
}

#define CASE0(fn)                                \
    static void case_##fn(int arg, void *slot)   \
    {                                            \
        (void)arg;                               \
        (void)slot;                              \
        fn();                                    \
    }
#define CASEK(fn)                                \
    static void case_##fn(int arg, void *slot)   \
    {                                            \
        (void)slot;                              \
        fn(arg);                                 \
    }

static void test_long_run_with_control(void)
{
    test_long_run_stays_clean();
    test_two_long_run_control();
}

CASE0(test_depth_zero_is_the_input)
CASE0(test_output_is_bounded)
CASE0(test_long_run_with_control)
CASE0(test_depth_step_has_no_click)
CASE0(test_swap_crossfades)
CASE0(test_bypass_keeps_history)
CASE0(test_rate_limits)
CASEK(test_pack_clamp)
CASEK(test_pack_bypass_identity)
CASEK(test_pack_long_run)
CASEK(test_pack_depth_steps)
CASEK(test_pack_reentry)
CASEK(test_feedback_stability)
CASEK(test_notch_depth)
CASE0(test_drive_level_step)
CASE0(test_drive_aliasing)
CASE0(test_drive_smoothness)
CASE0(test_drive_asymmetry)
CASE0(test_pack_swaps)
CASE0(test_static_level)
CASE0(test_chain_order)
CASE0(test_chain_bypass)
CASE0(test_chain_toggles)
CASE0(test_chain_clean)
CASE0(test_a_stage_enabled_later_starts_at_its_depth)
CASE0(test_level_is_a_depth_product)
CASE0(test_level_zero_is_off)
CASE0(test_levels_zero_is_bypass)
CASE0(test_level_steps_have_no_click)
CASE0(test_makeup_is_applied)
CASE0(test_makeup_moves_smoothly)

/* The pack's level bound: part arg = (k * LB_N + v) * N_LEVEL_SIGNALS + sgi. */
static int g_lb_case[N_FX][LB_N][N_LEVEL_SIGNALS];

static void lb_part(int arg, void *slot)
{
    lb_sweep_signal(arg / (LB_N * N_LEVEL_SIGNALS), (arg / N_LEVEL_SIGNALS) % LB_N,
                    arg % N_LEVEL_SIGNALS, slot);
}

static void test_pack_level_bound(int k)
{
    level_result sw[LB_N];
    for (int v = 0; v < LB_N; v++) {
        sw[v] = lb_init();
        for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
            if (g_lb_case[k][v][sgi] >= 0) {
                lb_merge(&sw[v], slot_of(g_lb_case[k][v][sgi]), sgi);
            }
        }
    }
    check_pack_level_bound(k, sw);
}

static void add_pack_level_bound(int k)
{
    char name[40];
    for (int v = 0; v < LB_N; v++) {
        lb_spec sp;
        const int has = lb_spec_for(k, v, &sp);
        for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
            g_lb_case[k][v][sgi] = -1;
            if (has && (sp.mask & (1u << sgi))) {
                snprintf(name, sizeof(name), "level %s v%d %s", FX_LABEL[k], v,
                         LEVEL_SIGNAL_LABEL[sgi]);
                g_lb_case[k][v][sgi] = add_case(name, (k * LB_N + v) * N_LEVEL_SIGNALS + sgi,
                                                lb_part, NULL, sp.steps + 1);
            }
        }
    }
    snprintf(name, sizeof(name), "level %s", FX_LABEL[k]);
    add_case(name, k, NULL, test_pack_level_bound, 0);
}

/* Every check, in the order the serial self-test always ran and printed them. */
static void build_cases(void)
{
    char name[40];
    add_case("depth zero is the input", 0, case_test_depth_zero_is_the_input, NULL, 3);
    add_case("output is bounded", 0, case_test_output_is_bounded, NULL, 10);
    add_case("long run stays clean", 0, case_test_long_run_with_control, NULL, 12);
    add_case("depth step has no click", 0, case_test_depth_step_has_no_click, NULL, 2);
    add_case("swap crossfades", 0, case_test_swap_crossfades, NULL, 2);
    add_case("bypass keeps history", 0, case_test_bypass_keeps_history, NULL, 1);
    add_case("rate limits", 0, case_test_rate_limits, NULL, 1);

    /* The pack. The chorus has its own battery above; these add its level curve. */
    add_pack_level_bound(0);
    add_case("clamp chorus", 0, case_test_pack_clamp, NULL, 4);
    for (int k = 1; k < N_FX; k++) {
        snprintf(name, sizeof(name), "bypass identity %s", FX_LABEL[k]);
        add_case(name, k, case_test_pack_bypass_identity, NULL, 1);
        add_pack_level_bound(k);
        snprintf(name, sizeof(name), "clamp %s", FX_LABEL[k]);
        add_case(name, k, case_test_pack_clamp, NULL, 4);
        snprintf(name, sizeof(name), "long run %s", FX_LABEL[k]);
        add_case(name, k, case_test_pack_long_run, NULL, 12);
        snprintf(name, sizeof(name), "depth steps %s", FX_LABEL[k]);
        add_case(name, k, case_test_pack_depth_steps, NULL, 1);
        snprintf(name, sizeof(name), "re-entry %s", FX_LABEL[k]);
        add_case(name, k, case_test_pack_reentry, NULL, 1);
    }
    add_case("feedback phaser", 2, case_test_feedback_stability, NULL, 12);
    add_case("feedback flanger", FX_I_FLANGER, case_test_feedback_stability, NULL, 12);
    add_case("notch depth phaser", 2, case_test_notch_depth, NULL, 2);
    add_case("notch depth flanger", FX_I_FLANGER, case_test_notch_depth, NULL, 3);
    add_case("drive level step", 0, case_test_drive_level_step, NULL, 1);
    add_case("drive aliasing", 0, case_test_drive_aliasing, NULL, 1);
    add_case("drive smoothness", 0, case_test_drive_smoothness, NULL, 1);
    add_case("drive asymmetry", 0, case_test_drive_asymmetry, NULL, 1);
    add_case("swaps", 0, case_test_pack_swaps, NULL, 2);

    /* The chain. */
    add_case("chain static level", 0, case_test_static_level, NULL, 1);
    add_case("chain order", 0, case_test_chain_order, NULL, 1);
    add_case("chain bypass", 0, case_test_chain_bypass, NULL, 1);
    add_case("chain toggles", 0, case_test_chain_toggles, NULL, 1);
    for (uint32_t m = 0; m <= FX_ALL; m++) {
        snprintf(name, sizeof(name), "chain level mask %u%s", (unsigned)m, m == 0 ? " (control)" : "");
        g_chain_case[m] = add_case(name, (int)m, chain_level_part, NULL, 8 + popcount5(m));
    }
    add_case("chain level", 0, NULL, test_chain_level_bound, 0);
    add_case("chain clean", 0, case_test_chain_clean, NULL, 14);
    add_case("chain makeup", 0, case_test_makeup_is_applied, NULL, 2);
    add_case("chain makeup moves", 0, case_test_makeup_moves_smoothly, NULL, 2);

    /* Per-effect levels. */
    add_case("level enabled later", 0, case_test_a_stage_enabled_later_starts_at_its_depth,
             NULL, 1);
    add_case("level is a depth product", 0, case_test_level_is_a_depth_product, NULL, 1);
    add_case("level zero is off", 0, case_test_level_zero_is_off, NULL, 1);
    add_case("levels zero is bypass", 0, case_test_levels_zero_is_bypass, NULL, 1);
    add_case("level steps", 0, case_test_level_steps_have_no_click, NULL, 1);
    for (int arg = 0; arg <= LV_CONTROL_PART; arg++) {
        g_lv_case[arg] = -1;
        if (arg != LV_CONTROL_PART && popcount5((uint32_t)(arg % 32)) < 2) {
            continue;
        }
        snprintf(name, sizeof(name), "levels louder %d", arg);
        g_lv_case[arg] = add_case(name, arg, levels_never_louder_part, NULL, 4);
    }
    add_case("levels never louder", 0, NULL, test_levels_never_louder, 0);
}

static int selftest_jobs(void)
{
    int cores = 1;
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(0, sizeof(set), &set) == 0) {
        cores = CPU_COUNT(&set);
    } else {
        long n = sysconf(_SC_NPROCESSORS_ONLN);
        cores = n > 0 ? (int)n : 1;
    }
    const char *env = getenv("FX_SELFTEST_JOBS");
    if (env == NULL || *env == '\0') {
        return cores;
    }
    char *end = NULL;
    errno = 0;
    long v = strtol(env, &end, 10);
    if (errno != 0 || end == env || *end != '\0' || v < 1) {
        fprintf(stderr, "fx-selftest: FX_SELFTEST_JOBS=%s is not a positive whole number\n", env);
        exit(2);
    }
    return v < cores ? (int)v : cores;
}

static double now_s(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

static void copy_out(FILE *from, FILE *to)
{
    char buf[4096];
    size_t n;
    rewind(from);
    while ((n = fread(buf, 1, sizeof(buf), from)) > 0) {
        fwrite(buf, 1, n, to);
    }
    fflush(to);
}

static int run_cases(int jobs, int times)
{
    static pid_t pid[MAX_CASES];
    static FILE *out[MAX_CASES], *err[MAX_CASES];
    static int status[MAX_CASES], done[MAX_CASES], order[MAX_CASES];
    static double t0[MAX_CASES], t1[MAX_CASES];
    const int n = g_ncases;

    /* Launch order: heaviest first (stable), so the long ones are not left to
     * the end; print order is always case order. */
    int nlaunch = 0;
    for (int i = 0; i < n; i++) {
        if (g_cases[i].run != NULL) {
            order[nlaunch++] = i;
        }
    }
    for (int i = 1; i < nlaunch; i++) {
        int x = order[i], j = i;
        while (j > 0 && g_cases[order[j - 1]].weight < g_cases[x].weight) {
            order[j] = order[j - 1];
            j--;
        }
        order[j] = x;
    }

    int next_launch = 0, running = 0, next_print = 0;
    const double start = now_s();
    while (next_print < n) {
        while (running < jobs && next_launch < nlaunch) {
            const int i = order[next_launch++];
            out[i] = tmpfile();
            err[i] = tmpfile();
            if (out[i] == NULL || err[i] == NULL) {
                perror("fx-selftest: tmpfile");
                exit(2);
            }
            fflush(stdout);
            fflush(stderr);
            t0[i] = now_s();
            pid[i] = fork();
            if (pid[i] < 0) {
                perror("fx-selftest: fork");
                exit(2);
            }
            if (pid[i] == 0) {
                if (dup2(fileno(out[i]), STDOUT_FILENO) < 0 ||
                    dup2(fileno(err[i]), STDERR_FILENO) < 0) {
                    _exit(3);
                }
                g_failures = 0;
                g_rng = RNG_SEED;
                g_cases[i].run(g_cases[i].arg, g_slots + (size_t)i * SLOT_BYTES);
                g_case_failures[i] = g_failures;
                fflush(stdout);
                fflush(stderr);
                _exit(0);
            }
            running++;
        }
        while (next_print < n && (g_cases[next_print].fold != NULL || done[next_print])) {
            const int i = next_print++;
            const st_case *c = &g_cases[i];
            if (c->fold != NULL) {
                c->fold(c->arg);
                fflush(stdout);
                fflush(stderr);
                continue;
            }
            copy_out(out[i], stdout);
            copy_out(err[i], stderr);
            fclose(out[i]);
            fclose(err[i]);
            if (!WIFEXITED(status[i]) || WEXITSTATUS(status[i]) != 0) {
                g_failures++;
                if (WIFSIGNALED(status[i])) {
                    fprintf(stderr, "FAIL %s: worker killed by signal %d\n", c->name,
                            WTERMSIG(status[i]));
                } else {
                    fprintf(stderr, "FAIL %s: worker exited %d\n", c->name,
                            WEXITSTATUS(status[i]));
                }
            } else {
                g_failures += g_case_failures[i];
            }
            if (times) {
                fprintf(stderr, "time %-36s %7.2f s\n", c->name, t1[i] - t0[i]);
            }
        }
        if (next_print >= n) {
            break;
        }
        if (running == 0) {
            fprintf(stderr, "fx-selftest: runner stalled at case %d\n", next_print);
            exit(2);
        }
        int st = 0;
        const pid_t w = waitpid(-1, &st, 0);
        if (w < 0) {
            if (errno == EINTR) {
                continue;
            }
            perror("fx-selftest: waitpid");
            exit(2);
        }
        for (int i = 0; i < n; i++) {
            if (g_cases[i].run != NULL && !done[i] && pid[i] == w) {
                status[i] = st;
                done[i] = 1;
                t1[i] = now_s();
                running--;
                break;
            }
        }
    }
    if (times) {
        fprintf(stderr, "time %d cases, %d jobs: %.2f s\n", n, jobs, now_s() - start);
    }
    return g_failures;
}

/* The runner's own controls (fx-selftest --runner-check MODE), driven by
 * tests/test_fx_host.py: a failed check in a worker, and a worker that dies,
 * must each fail the run; cases that finish out of order must still print in
 * order, and a fold must see every part. */
#define RC_CASES 12
static int g_rc_case[RC_CASES];

static void rc_order(int i, void *slot)
{
    /* The later the case, the sooner it finishes. */
    struct timespec ts = { 0, (long)(RC_CASES - i) * 15000000L };
    nanosleep(&ts, NULL);
    *(int *)slot = i + 1;
    printf("case %d\n", i);
}

static void rc_fold(int unused)
{
    (void)unused;
    int sum = 0;
    for (int i = 0; i < RC_CASES; i++) {
        sum += *(const int *)slot_of(g_rc_case[i]);
    }
    printf("fold %d\n", sum);
}

static void rc_fail(int arg, void *slot)
{
    (void)slot;
    CHECK(arg == 0, "deliberate failure (runner control)");
    printf("ran %d\n", arg);
}

static void rc_crash(int arg, void *slot)
{
    (void)slot;
    printf("ran %d\n", arg);
    if (arg == 1) {
        fflush(stdout);
        abort();
    }
}

static int build_runner_check(const char *mode)
{
    if (strcmp(mode, "order") == 0) {
        for (int i = 0; i < RC_CASES; i++) {
            g_rc_case[i] = add_case("order", i, rc_order, NULL, 1);
        }
        add_case("fold", 0, NULL, rc_fold, 0);
    } else if (strcmp(mode, "fail") == 0 || strcmp(mode, "crash") == 0) {
        void (*run)(int, void *) = mode[0] == 'f' ? rc_fail : rc_crash;
        for (int i = 0; i < 3; i++) {
            add_case(mode, i, run, NULL, 1);
        }
    } else {
        return 0;
    }
    return 1;
}

int main(int argc, char **argv)
{
    const char *full = getenv("FX_SELFTEST_FULL");
    g_full = full != NULL && strcmp(full, "1") == 0;
    if (argc == 3 && strcmp(argv[1], "--parse") == 0) {
        print_parse(argv[2]);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--parse-lines") == 0) {
        char line[512];
        while (fgets(line, sizeof(line), stdin) != NULL) {
            line[strcspn(line, "\n")] = '\0';
            print_parse(line);
        }
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--measure") == 0) {
        g_full = 1;   /* a report: the full-length level windows */
        measure_report();
        return g_failures != 0;
    }
    if (argc == 2 && strcmp(argv[1], "--measure-unlevelled") == 0) {
        fx_test_no_level = 1;   /* the mixes as #149 had them: no level curve */
        g_full = 1;
        measure_report();
        return g_failures != 0;
    }
    if (argc == 2 && strcmp(argv[1], "--measure-no-makeup") == 0) {
        fx_test_no_makeup = 1;   /* the levels before the chain's makeup */
        g_full = 1;
        measure_report();
        return g_failures != 0;
    }
    if (argc == 2 && strcmp(argv[1], "--bench") == 0) {
        bench();
        return 0;
    }
    const int runner_check = argc == 3 && strcmp(argv[1], "--runner-check") == 0;
    if (!runner_check && (argc > 2 || (argc == 2 && strcmp(argv[1], "-v") != 0))) {
        fprintf(stderr, "usage: fx-selftest [-v | --parse TEXT | --parse-lines | --measure | "
                        "--measure-unlevelled | --measure-no-makeup | --bench | --runner-check order|fail|crash]\n");
        return 2;
    }
    g_verbose = argc == 2;
    const char *times = getenv("FX_SELFTEST_TIMES");

    g_slots = mmap(NULL, (size_t)MAX_CASES * SLOT_BYTES + MAX_CASES * sizeof(int),
                   PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (g_slots == MAP_FAILED) {
        perror("fx-selftest: mmap");
        return 2;
    }
    g_case_failures = (int *)(g_slots + (size_t)MAX_CASES * SLOT_BYTES);
    if (runner_check) {
        if (!build_runner_check(argv[2])) {
            fprintf(stderr, "fx-selftest: --runner-check order|fail|crash\n");
            return 2;
        }
    } else {
        keys_build();   /* once, before the workers fork: they share it */
        build_cases();
    }
    run_cases(selftest_jobs(), times != NULL && strcmp(times, "1") == 0);

    if (g_failures != 0) {
        fprintf(stderr, "fx-selftest: %d check(s) failed\n", g_failures);
        return 1;
    }
    printf("fx-selftest: all checks passed\n");
    return 0;
}
