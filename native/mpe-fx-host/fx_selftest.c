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
 *   fx-selftest --unity-raw K DEPTH   effect K alone at DEPTH, no level
 *                               curve, no makeup: the raw loss on each level
 *                               signal, what the level fits are fitted to
 *   fx-selftest --unity MASK DEPTH [BASS_HZ]   the chain MASK at DEPTH as
 *                               shipped (the bass bypass at BASS_HZ, Off if
 *                               not given): each
 *                               signal in dB re the input x makeup, the worst
 *                               peak over the input, and the keys at -9 dBFS
 *                               (device mode): peak dBFS and clamped samples
 *   fx-selftest --fit-bass K    effect K's bass-bypass level correction,
 *                               fitted on the keys (fx_dsp.h, "Bass bypass")
 *   fx-selftest --tremolo-shape one cycle of the tremolo's gain, before (the
 *                               rounded triangle) and after (the sine with a
 *                               floor), and their worst per-sample slopes
 *   fx-selftest --bench         ns per stereo frame for each effect, and for
 *                               the engine with each alone and all seven on,
 *                               and all seven at mixed levels, settled and
 *                               moving every period, and the bass bypass's
 *                               cost (Off, 120 Hz, the crossover gliding)
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
#include <sys/prctl.h>
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
/* Set while a chain sweep of a mask with the rotary or the auto-wah runs:
 * one keys loop (2.4 s) and at most 2.5 s of the others. Those masks are
 * swept for an old stage's curve meeting a reshaped spectrum, not for a slow
 * sweep's worst phase, which the old masks' long windows already catch
 * (chain_level_part). */
static int g_short_windows;
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
    g_two[1] = (fx_effect_desc){ "flip", 0.5f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, flip_init, flip_process, NULL, NULL };
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

/* The bass bypass's crossover every run_period plays at: 0 (Off) unless a
 * bass check sets it, so every other check runs the chain as it was. */
static float g_bass_hz;

/* One period with the effects in `mask` enabled (BIT(k) for effect k alone). */
static void run_period(fx_engine *e, float *l, float *r, float *ol, float *or_,
                       uint32_t n, uint32_t mask, float depth)
{
    const float *const in[FX_CHANNELS] = { l, r };
    float *const out[FX_CHANNELS] = { ol, or_ };
    fx_engine_set_bass(e, g_bass_hz);
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
/* The starter pack: drive (1), phaser (2), tremolo (3); flanger (4);         */
/* rotary (5), auto-wah (6)                                                   */
/* ========================================================================= */
/*
 * The same battery as the chorus, per effect, each check paired with a
 * deliberately broken run it must fail. The broken effects are FX_TEST_MUTANTS
 * (fx_dsp.c, compiled only with FX_SELFTEST) or built here from the shipped
 * descriptor (feed removed, wet made 0.5 dB hotter).
 */

#define N_FX 7
static const char *const FX_LABEL[N_FX] = { "chorus", "drive", "phaser", "tremolo", "flanger",
                                            "rotary", "auto-wah" };
#define FX_I_FLANGER 4
#define FX_I_ROTARY 5
#define FX_I_WAH 6
#define FX_I_DRIVE 1

/* Every effect has a static level curve (fx_dsp.h, 2026-10-13). This picks
 * the ones whose curve at half depth is far enough from 0 dB (0.4 dB and up)
 * to serve as the NaN-recovery control in test_pack_clamp: all but the
 * drive, whose curve stays within 0.1 dB of it. */
static int has_level_curve(int k)
{
    return k != 1;
}

static int popcount(uint32_t m)
{
    int n = 0;
    for (; m != 0; m &= m - 1) {
        n++;
    }
    return n;
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
static const fx_effect_desc LEAKY = { "leaky", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, sizeof(leaky_state),
                                      leaky_init, leaky_process, leaky_feed, NULL };

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

/* Unity (2026-10-13): with at least one effect on, the output is the dry
 * signal times the chain's makeup G(m), on the keys. Every reading below is
 * out / (in x G(m)) in dB.
 *
 * One effect alone, any knob position: within UNITY_TOL_DB. The level fits
 * (fx_dsp.c) measured -0.07..+0.10 dB at 32 depths per effect, full
 * windows; 0.3 leaves the 0.5 dB controls (level curve hot, cold) outside.
 *
 * A chain of two or more, at 50 and 100 %, and the level vectors: CHAIN_LO_DB
 * ..CHAIN_HI_DB. The stages' interactions do not cancel: all 120 multi-stage
 * masks at full measured -0.39..+1.11 dB (phaser and flanger together, which
 * stack their combs, are the top 4; without that pair, -0.39..+0.59), at 50 %
 * -0.26..+0.56. No per-mask correction: a pairwise model of the
 * interactions fitted the pairs and made the 3..7-stage masks worse (residual
 * -1.6..+0.3 dB), and a 127-entry table per depth is a parameter per mask
 * nobody could hear the difference between. The bounds are the self-test's
 * own readings (shorter windows) with 0.3 dB of margin.
 *
 * Any signal, any mask, any knob position: at most CEIL_DB. Unity holds on
 * the keys only: a narrow tone through a resonance can sit above them (the
 * auto-wah's 3 kHz tone at full, +2.0 dB; the rotary's 80 Hz tone in its
 * fade-in, +2.5 dB). The ceiling is the runaway check: a resonance far above
 * unity crosses it. */
#define UNITY_TOL_DB 0.3
#define CHAIN_LO_DB (-0.85)
#define CHAIN_HI_DB 1.65
#define CEIL_DB 3.0

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
 * tremolo 5 Hz (0.2 s); the drive and the auto-wah have none. The rotary's
 * rotors turn at 0.67 (drum) and 0.8 Hz (horn) at the bottom of their range,
 * faster above it: 2.5 s is 1.7 drum turns at the slowest, and its level is
 * even across a turn by construction (fx_dsp.c: the two microphones' powers
 * sum to the input's), so a part-turn moves only how the power splits
 * between the channels, which the RMS over both does not see. So 5 s with
 * the flanger, or the chorus and the phaser together; 2.5 s for the phaser
 * or the rotary, one chorus sweep for the chorus, 1 s (five tremolo
 * cycles) for the drive, the auto-wah and the tremolo. The keys: two loops
 * (4.8 s, 1.9 phaser cycles) with a sweep or rotor slower than the loop, one
 * loop (2.4 s) without. Until 2026-10-13 every mask had 5 s, keys 4.8 s
 * (FX_SELFTEST_FULL=1). */
static float level_window_s(uint32_t fx, int kind)
{
    const uint32_t sweeps = BIT(0) | BIT(2) | BIT(FX_I_FLANGER) | BIT(FX_I_ROTARY);
    if (g_full) {
        return kind == SIG_KEYS ? 4.8f : 5.0f;
    }
    if (g_short_windows) {
        return kind == SIG_KEYS ? 2.4f : ((fx & (BIT(0) | BIT(2) | BIT(FX_I_FLANGER) |
                                                 BIT(FX_I_ROTARY))) != 0 ? 2.5f : 1.0f);
    }
    if (kind == SIG_KEYS) {
        return (fx & sweeps) != 0 ? 4.8f : 2.4f;
    }
    if ((fx & BIT(FX_I_FLANGER)) != 0 || (fx & (BIT(0) | BIT(2))) == (BIT(0) | BIT(2))) {
        return 5.0f;
    }
    if ((fx & (BIT(2) | BIT(FX_I_ROTARY))) != 0) {
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
    float rms_hi[N_LEVEL_SIGNALS];    /* worst out/in, upward, per signal */
} level_result;

/* `steps` + 1 knob positions from 0 to 1, on the signals in `mask`. Every
 * reading is out / (in x G(m)): relative to the chain's makeup at that knob
 * position: the unity bounds (UNITY_TOL_DB) are around it. */
static level_result level_sweep_from(const fx_effect_desc *table, uint32_t fx, unsigned mask,
                                     int first, int steps, double (*floor_db)(float depth))
{
    level_result res = { 0.0f, 0.0f, 0.0f, -1, { 0 }, { 0 }, 1e9f, { 0 } };
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        res.rms_min[sgi] = 1e9f;
        if (!(mask & (1u << sgi))) {
            continue;
        }
        for (int step = first; step <= steps; step++) {
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
            res.rms_hi[sgi] = fmaxf(res.rms_hi[sgi], rr);
            res.peak_max = fmaxf(res.peak_max, pr);
            if (floor_db != NULL) {
                res.floor_margin_db = fminf(res.floor_margin_db,
                                            (float)(20.0 * log10(rr) - floor_db(depth)));
            }
        }
    }
    return res;
}

static level_result level_sweep(const fx_effect_desc *table, uint32_t fx, unsigned mask,
                                int steps, double (*floor_db)(float depth))
{
    return level_sweep_from(table, fx, mask, 0, steps, floor_db);
}

/* The tremolo's trough (fx_dsp.c), restated here rather than read from the
 * DSP, so a changed trough shows as a failed shape check, not a moved bound. */
#define TREMOLO_TROUGH 0.25

/* The largest static level gain effect k's table holds (fx_dsp.c), any knob
 * position: what bounds the tremolo's and the drive's peaks. */
static float level_gain_max(int k)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float g = 0.0f;
    for (int j = 0; j <= FX_LEVEL_POINTS; j++) {
        g = fmaxf(g, e.level[k][j]);
    }
    fx_engine_free(&e);
    return g;
}

static double db(float ratio)
{
    return 20.0 * log10((double)ratio);
}

/* The level bound runs as one sweep per (effect, variant, signal), each in
 * its own worker, merged back in signal order (lb_merge): the same result a
 * single sweep over every signal gives. The variants are the main sweep and
 * the controls below. */
enum { LB_MAIN, LB_HOT, LB_COLD, LB_UPPER, LB_PEAK, LB_LOWER, LB_N };

typedef struct {
    const fx_effect_desc *table;
    unsigned mask;
    int steps;
    double (*floor_db)(float depth);
    int level_hot, level_cold, no_level;
} lb_spec;

/* The sweep for effect k, variant v; 0 if k has no such variant. */
static int lb_spec_for(int k, int v, lb_spec *sp)
{
    static fx_effect_desc hot, weak;
    *sp = (lb_spec){ FX_EFFECTS, MASK_ALL, 4, NULL, 0, 0, 0 };
    switch (v) {
    case LB_MAIN:
        sp->steps = 20;
        return 1;
    case LB_HOT:
        sp->mask = MASK_KEYS;
        sp->level_hot = 1;
        return 1;
    case LB_COLD:
        sp->mask = MASK_KEYS;
        sp->level_cold = 1;
        return 1;
    case LB_UPPER:
        if (k == 1) {
            hot = hot_version(k);
            sp->table = table_with(k, &hot);
            sp->mask = MASK_KEYS;
        } else if (k == 2) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_PHASER_HOT_FEEDBACK]);
        } else if (k == FX_I_FLANGER) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_FLANGER_HOT_FEEDBACK]);
        } else if (k == 3) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_TREMOLO_OVERSHOOT]);
            sp->mask = MASK_KEYS;
        } else if (k == FX_I_WAH) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_WAH_WHISTLE]);
        } else {
            return 0;
        }
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
        } else if (k == 1) {
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_DRIVE_NO_MAKEUP]);
        } else if (k == FX_I_ROTARY) {
            /* The delays centred on 1 ms: the fade-in's comb reaches the keys. */
            sp->table = table_with(k, &FX_TEST_MUTANTS[FX_MUT_ROTARY_WIDE_CENTER]);
            sp->steps = 20;
        } else if (k == FX_I_WAH) {
            /* Mixed further toward the band-pass: more taken away. */
            weak = FX_EFFECTS[k];
            weak.wet_max = 0.9f;
            sp->table = table_with(k, &weak);
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
    fx_test_level_cold = sp.level_cold;
    fx_test_no_level = sp.no_level;
    *out = level_sweep(sp.table, BIT(k), 1u << sgi, sp.steps, sp.floor_db);
    fx_test_level_hot = 0;
    fx_test_level_cold = 0;
    fx_test_no_level = 0;
}

static level_result lb_init(void)
{
    level_result res = { 0.0f, 0.0f, 0.0f, -1, { 0 }, { 0 }, 1e9f, { 0 } };
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
    acc->rms_hi[sgi] = part->rms_hi[sgi];
    acc->peak_max = fmaxf(acc->peak_max, part->peak_max);
    acc->floor_margin_db = fminf(acc->floor_margin_db, part->floor_margin_db);
}

/* The checks, on the merged sweeps (`sw[v]`, for each variant k has). */
static void check_pack_level_bound(int k, const level_result sw[LB_N])
{
    const level_result r = sw[LB_MAIN];
    const double hi = db(r.rms_hi[SIG_I_KEYS]), lo = db(r.rms_min[SIG_I_KEYS]);
    if (g_verbose) {
        printf("  %-8s keys vs makeup, 21 depths: %+.3f..%+.3f dB; worst any signal %+.3f dB "
               "(depth %.2f, %s), worst peak %+.3f dB\n", FX_LABEL[k], lo, hi, db(r.rms_max),
               (double)r.at_depth, r.at_signal >= 0 ? LEVEL_SIGNAL_LABEL[r.at_signal] : "-",
               db(r.peak_max));
    }

    /* Unity on the keys, both ways, and its controls: the level curve 0.5
     * dB hot, and 0.5 dB cold. */
    CHECK(hi <= UNITY_TOL_DB, "%s: keys %+.3f dB over dry x makeup, tolerance %.1f", FX_LABEL[k],
          hi, UNITY_TOL_DB);
    CHECK(lo >= -UNITY_TOL_DB, "%s: keys %+.3f dB under dry x makeup (depth %.2f), tolerance "
          "%.1f", FX_LABEL[k], lo, (double)r.min_depth[SIG_I_KEYS], UNITY_TOL_DB);
    const double chot = db(sw[LB_HOT].rms_hi[SIG_I_KEYS]);
    const double ccold = db(sw[LB_COLD].rms_min[SIG_I_KEYS]);
    CHECK(chot > UNITY_TOL_DB, "%s: control (level curve 0.5 dB hot) passed: keys %+.3f dB",
          FX_LABEL[k], chot);
    CHECK(ccold < -UNITY_TOL_DB, "%s: control (level curve 0.5 dB cold) passed: keys %+.3f dB",
          FX_LABEL[k], ccold);
    if (g_verbose) {
        printf("  %-8s controls: curve hot keys %+.3f dB, curve cold keys %+.3f dB\n",
               FX_LABEL[k], chot, ccold);
    }

    /* The ceiling, every signal, and its controls: a resonance far above
     * unity (phaser and flanger feedback 0.95, the auto-wah's whistle: Q 12
     * unnormalised). */
    CHECK(r.rms_max <= (float)pow(10.0, CEIL_DB / 20.0), "%s: output RMS %+.3f dB over dry x "
          "makeup (depth %.2f, %s), ceiling %+.1f", FX_LABEL[k], db(r.rms_max),
          (double)r.at_depth, LEVEL_SIGNAL_LABEL[r.at_signal], CEIL_DB);
    if (k == 2 || k == FX_I_FLANGER || k == FX_I_WAH) {
        const level_result c = sw[LB_UPPER];
        CHECK(db(c.rms_max) > CEIL_DB, "%s: control (%s) passed the ceiling: %+.3f dB",
              FX_LABEL[k], k == FX_I_WAH ? "whistle" : "hot feedback", db(c.rms_max));
        if (g_verbose) {
            printf("  %-8s ceiling control (%s): %+.3f dB (%s)\n", FX_LABEL[k],
                   k == FX_I_WAH ? "whistle" : "hot feedback", db(c.rms_max),
                   c.at_signal >= 0 ? LEVEL_SIGNAL_LABEL[c.at_signal] : "-");
        }
    }
    if (k == 1) {
        const double c = db(sw[LB_UPPER].rms_hi[SIG_I_KEYS]);
        CHECK(c > UNITY_TOL_DB, "drive: control (wet +0.5 dB) passed: keys %+.3f dB", c);
    }

    /* Peak, where the effect can promise it: the tremolo's gain is in [0, 1]
     * and the drive's limiter holds it under the input's recent peak, so
     * either's output peak is at most its static level gain (the largest in
     * its table) times the makeup (r is already relative to the makeup).
     * Controls: an LFO that overshoots unity; the drive with no ceiling and
     * no limiter. The chorus's is test_output_is_bounded. The phaser and the
     * flanger cannot promise one: an all-pass moves phase and raises the
     * crest factor, a resonance swells what it crosses. The rotary's Doppler
     * and the auto-wah's band-pass move phase as well. The ±1 clamp holds for
     * every effect. */
    if (k == 3 || k == 1) {
        const float gmax = level_gain_max(k);
        const level_result cp = sw[k == 3 ? LB_UPPER : LB_PEAK];
        CHECK(r.peak_max <= gmax * (1.0f + 1e-4f), "%s: output peak %+.3f dB over the input, "
              "its level gain at most %+.3f dB", FX_LABEL[k], db(r.peak_max), db(gmax));
        CHECK(cp.peak_max > gmax * (1.0f + 1e-4f), "%s: control (%s) kept the peak: %+.3f dB",
              FX_LABEL[k], k == 3 ? "overshoot" : "no ceiling, no limiter", db(cp.peak_max));
        if (g_verbose) {
            printf("  %-8s peak %+.3f dB, level gain at most %+.3f dB; control (%s) %+.3f dB\n",
                   FX_LABEL[k], db(r.peak_max), db(gmax),
                   k == 3 ? "overshoot" : "no ceiling, no limiter", db(cp.peak_max));
        }
    }

    /* A second control under the floor, from the effect itself: the tremolo
     * with its top at 0.8, the drive without its makeup, the rotary centred
     * on 1 ms, the auto-wah mixed 0.9 toward the band-pass, and the others
     * with no level curve at all. */
    const level_result cl = sw[LB_LOWER];
    const char *control = k == 1 ? "no makeup" : k == FX_I_WAH ? "wet 0.9"
                        : k == FX_I_ROTARY ? "1 ms centre" : k == 3 ? "top at 0.8"
                        : "no level curve";
    CHECK(db(cl.rms_min[SIG_I_KEYS]) < -UNITY_TOL_DB, "%s: control (%s) met unity: keys %+.3f "
          "dB", FX_LABEL[k], control, db(cl.rms_min[SIG_I_KEYS]));
    if (g_verbose) {
        printf("  %-8s lower control (%s): keys %+.3f dB\n", FX_LABEL[k], control,
               db(cl.rms_min[SIG_I_KEYS]));
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
                                 : k == FX_I_ROTARY ? &FX_TEST_MUTANTS[FX_MUT_ROTARY_NO_FLUSH]
                                 : k == FX_I_WAH ? &FX_TEST_MUTANTS[FX_MUT_WAH_NO_FLUSH]
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

/* --- 9. feedback stability (phaser, flanger, auto-wah) -------------------- */

/* 115.2 s (FEEDBACK_WINDOWS) at full depth with the clamp off, so the clamp
 * cannot hide a loop that grows: keys at 0.5 with a 0.1 s burst of noise at the start of
 * every 9.6 s window (four keys loops), then 2 s of silence. The RMS out/in of
 * each window against the first's, the largest output sample against the
 * input's, non-finite samples, and non-zero samples in the last second of the
 * silence. A loop at or above unity gain grows, overflows, or rings on.
 * The auto-wah runs 3 windows (28.8 s): its loop is one state-variable
 * filter, stable for any damping above 0 and gone in milliseconds below it
 * (its runaway control is non-finite inside the first window), not a long
 * delay line whose growth needs minutes to show. */
#define FEEDBACK_WINDOWS_WAH 3
static int feedback_windows(int k)
{
    return k == FX_I_WAH ? FEEDBACK_WINDOWS_WAH : FEEDBACK_WINDOWS;
}
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
    const int window = (int)(9.6f * RATE / PERIOD), windows = feedback_windows(k), burst = (int)(0.1f * RATE / PERIOD);
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
                                  : k == FX_I_WAH ? &FX_TEST_MUTANTS[FX_MUT_WAH_RUNAWAY]
                                                  : &FX_TEST_MUTANTS[FX_MUT_FLANGER_RUNAWAY];
    stability st = feedback_stability(FX_EFFECTS, k);
    stability c = feedback_stability(table_with(k, runaway), k);
    if (g_verbose) {
        printf("  %-8s feedback, %.1f s at full depth, clamp off: growth %+.2f dB, peak %+.2f dB "
               "over input, %zu non-finite, %zu residue; control (%s) growth %+.1f dB, peak "
               "%+.1f dB, %zu non-finite, %zu residue\n", FX_LABEL[k], 9.6 * feedback_windows(k),
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
    /* The whole engine, as the client runs it: one effect, then all seven. */
    static const uint32_t masks[9] = { 0u, 1u << 0, 1u << 1, 1u << 2, 1u << 3, 1u << 4,
                                       1u << 5, 1u << 6, (1u << 7) - 1u };
    static const char *const names[9] = { "bypass", "chorus", "drive", "phaser", "tremolo",
                                          "flanger", "rotary", "auto-wah", "all seven" };
    for (int m = 0; m < 9; m++) {
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
    /* All seven with per-effect levels: settled (each depth reached its
     * target, the smoother is a fill), and moving (the levels change every
     * period, so every stage's smoother runs every frame: the worst case). */
    static const float mixed[FX_MAX_EFFECTS] = { 0.8f, 0.35f, 1.0f, 0.15f, 0.6f, 0.7f, 0.5f, 1.0f };
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
            fx_engine_process_levels(&e, in2, out2, 256, (1u << 7) - 1u, 0.7f, lv);
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        double ns = ((double)(t1.tv_sec - t0.tv_sec) * 1e9 + (double)(t1.tv_nsec - t0.tv_nsec)) /
                    ((double)periods * 256);
        printf("engine, all seven, levels %-8s %6.1f ns/stereo frame\n",
               moving ? "moving" : "settled", ns);
        fx_engine_free(&e);
    }
    /* The bass bypass: each case with the split Off and at 120 Hz, and with
     * the crossover moving every period (the glide recomputes coefficients
     * every chunk: the worst case). Same input, same masks. */
    static const struct {
        const char *name;
        uint32_t mask;
        float depth;
    } bass_cases[4] = {
        { "bypass, chorus ticked at 0", 1u << 0, 0.0f },
        { "chorus", 1u << 0, 0.7f },
        { "rotary", 1u << 5, 0.7f },
        { "all seven", (1u << 7) - 1u, 0.7f },
    };
    for (int c = 0; c < 4; c++) {
        double ns_mode[3];
        for (int mode = 0; mode < 3; mode++) {
            fx_engine e;
            fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, 40.0, 30.0);
            float bl[256], br[256], ol[256], or_[256];
            const int periods = 50000;
            struct timespec t0, t1;
            g_rng = RNG_SEED;
            clock_gettime(CLOCK_MONOTONIC, &t0);
            for (int p = 0; p < periods; p++) {
                for (uint32_t i = 0; i < 256; i++) {
                    bl[i] = 0.3f * noise();
                    br[i] = 0.3f * noise();
                }
                fx_engine_set_bass(&e, mode == 0 ? 0.0f : mode == 1 ? 120.0f : ((p / 4) & 1 ? 80.0f : 300.0f));
                const float *const in2[FX_CHANNELS] = { bl, br };
                float *const out2[FX_CHANNELS] = { ol, or_ };
                fx_engine_process(&e, in2, out2, 256, bass_cases[c].mask, bass_cases[c].depth);
            }
            clock_gettime(CLOCK_MONOTONIC, &t1);
            ns_mode[mode] = ((double)(t1.tv_sec - t0.tv_sec) * 1e9 +
                             (double)(t1.tv_nsec - t0.tv_nsec)) / ((double)periods * 256);
            fx_engine_free(&e);
        }
        printf("engine, bass, %-27s Off %6.1f, 120 Hz %6.1f (%+5.1f), gliding %6.1f (%+5.1f) "
               "ns/stereo frame\n", bass_cases[c].name, ns_mode[0], ns_mode[1],
               ns_mode[1] - ns_mode[0], ns_mode[2], ns_mode[2] - ns_mode[0]);
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
    static const uint32_t chains[11] = { 0x3u, 0x5u, 0x7u, 0xFu, 0x14u, 0x11u, 0x1Fu, 0x21u,
                                         0x42u, 0x60u, 0x7Fu };
    static const char *const chain_names[11] = { "ch+drv", "ch+ph", "ch+dr+ph", "all4", "ph+fl",
                                                 "ch+fl", "all5", "ch+rot", "dr+wah", "rot+wah",
                                                 "all7" };
    for (int c = 0; c < 11; c++) {
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

/* --unity-raw K DEPTH: effect K alone at DEPTH, level curve and makeup off,
 * out/in RMS in dB on every level signal (full windows): one tab-separated
 * row of the raw loss the level fits (fx_dsp.c) are calibrated against. The
 * calibration ran it at 33 depths per effect, plus 12 inside the drive's
 * fade-in (0..0.05) and 12 inside the rotary's (0..0.1). */
static void unity_raw_line(int k, float depth)
{
    fx_test_no_level = 1;
    fx_test_no_makeup = 1;
    g_full = 1;
    printf("%s\t%.5f", FX_LABEL[k], (double)depth);
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        float rr, pr;
        level_at(FX_EFFECTS, BIT(k), sgi, depth, &rr, &pr);
        printf("\t%+.3f", 20.0 * log10((double)rr));
    }
    printf("\n");
    fx_test_no_level = 0;
    fx_test_no_makeup = 0;
}

/* --unity MASK DEPTH: one line, the chain `MASK` at DEPTH with its level
 * curves and makeup, out/in RMS minus the makeup in dB on every level signal
 * (full windows), then the worst peak over the input and the clamp's count
 * on the keys at -9 dBFS (device mode). The calibration and spread reports
 * (2026-10-13) run it for every effect and every mask. */
static void unity_line(uint32_t mask, float depth)
{
    g_full = 1;
    const double gm = 20.0 * log10((double)makeup_gain(makeup_m(mask, depth)));
    printf("%u\t%.5f", (unsigned)mask, (double)depth);
    float pk = 0.0f;
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        float rr, pr;
        level_at(FX_EFFECTS, mask, sgi, depth, &rr, &pr);
        printf("\t%+.3f", 20.0 * log10((double)rr) - gm);
        pk = fmaxf(pk, pr);
    }
    /* Device mode: keys at 0.35 (about -9 dBFS), 5 s after 0.4 s, clamp off,
     * counting the samples the clamp would have cut, and the peak in dBFS. */
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    signal_gen g = { SIG_KEYS, 0.35f, 0.0f, 0.0 };
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD], dpk = 0.0f;
    long clipped = 0;
    const int settle = (int)(0.4f * RATE / PERIOD), measure = (int)(5.0f * RATE / PERIOD);
    fx_test_no_clamp = 1;
    for (int p = 0; p < settle + measure; p++) {
        sig_fill(&g, l, r, PERIOD);
        run_period(&e, l, r, ol, or_, PERIOD, mask, depth);
        for (uint32_t i = 0; p >= settle && i < PERIOD; i++) {
            clipped += (fabsf(ol[i]) > 1.0f) + (fabsf(or_[i]) > 1.0f);
            dpk = fmaxf(dpk, fmaxf(fabsf(ol[i]), fabsf(or_[i])));
        }
    }
    fx_test_no_clamp = 0;
    fx_engine_free(&e);
    printf("\t%+.3f\t%+.2f\t%ld\n", 20.0 * log10((double)pk), 20.0 * log10((double)dpk), clipped);
}

/* Smoother at full drive, as an objective proxy for the ear: on a 250 Hz
 * tone at 0.5, the odd harmonics fall off fast. Shipped: h5 -19.3, h7 -25.8
 * dB re the fundamental. The round-2 voicing measured h5 -14.6, h7 -18.2.
 * Limits h5 <= -17, h7 <= -22. Control: the round-2 cubic shaper on the new
 * drive law must fail them. Measured with the top third off (G 8 at full, the
 * law these numbers belong to): this checks the shaper's shape; the top
 * third's extra drive is test_drive_top's. */
static int drive_smooth(const fx_effect_desc *table, double h[7])
{
    fx_test_no_top = 1;   /* the shaper at the law it was voiced on: G 8 at full */
    harmonic_profile(table, 1, 0.5f, 1.0f, h);
    fx_test_no_top = 0;
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
 * -21.7 dB re the fundamental; the round-3 bias measured -27.7. Limit -24.
 * Top third off, as above: harder drive squares the wave toward symmetry
 * whatever the bias (at the new top the shipped bias reads ~-29, the control
 * ~-35), so this is where the bias is visible. */
static void test_drive_asymmetry(void)
{
    double h[7], c[7];
    fx_test_no_top = 1;   /* as the smoothness check: the bias, at G 8 */
    harmonic_profile(FX_EFFECTS, 1, 0.2f, 1.0f, h);
    harmonic_profile(table_with(1, &FX_TEST_MUTANTS[FX_MUT_DRIVE_ROUND3_BIAS]), 1, 0.2f, 1.0f, c);
    fx_test_no_top = 0;
    if (g_verbose) {
        printf("  drive    asymmetry, 250 Hz 0.2 at full: h2 %+.1f dB; control (bias 0.1) "
               "%+.1f dB; limit -24\n", h[1], c[1]);
    }
    CHECK(h[1] >= -24.0, "drive: h2 %+.1f dB at 0.2 in, full drive: asymmetry below the 0.2 "
          "bias's", h[1]);
    CHECK(c[1] < -24.0, "drive: control (bias 0.1) passed the asymmetry check: h2 %+.1f", c[1]);
}

/* ========================================================================= */
/* Round 6 (2026-10-13): a smoother tremolo, the rotary, the auto-wah         */
/* ========================================================================= */

/* --- the tremolo swells, it does not chop ---------------------------------- */

/* One second (five cycles) of the tremolo's left gain at 48 kHz, read as the
 * wet of a constant 1.0 through the descriptor: the trough (the smallest
 * gain), the worst per-sample slope, and the worst of harmonics 2-5 of the
 * gain against its fundamental. Shipped: a sine between 0.25 and 1, slope
 * (1 - 0.25) / 2 x 2 pi 5 / 48000 = 2.45e-4, harmonics at the polynomial's
 * error (-107 dB). The pre-2026-10-13 rounded triangle: trough 0 (silent a
 * fifth of every cycle), slope 3.13e-4, third harmonic -38 dB. A sine with no
 * floor: trough 0, slope 3.27e-4. Limits: trough >= 0.25 - 1e-4, slope <= the
 * shipped sine's x 1.01, harmonics <= -60 dB; each fails at least one
 * control. */
typedef struct {
    double trough, slope, harm_db;
} trem_shape;

static trem_shape tremolo_shape(const fx_effect_desc *d)
{
    enum { N = 48000 };
    static float g[N];
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float one[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK];
    static const float zero[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        one[i] = 1.0f;
    }
    const float *const in[FX_CHANNELS] = { one, one };
    float *const wet[FX_CHANNELS] = { wl, wr };
    for (uint32_t off = 0; off < N; off += FX_CHUNK) {
        d->process(st, in, wet, zero, FX_CHUNK);
        memcpy(g + off, wl, sizeof(wl));
    }
    free(st);
    trem_shape t = { 1e9, 0.0, -1e9 };
    for (uint32_t i = 0; i < N; i++) {
        t.trough = fmin(t.trough, (double)g[i]);
        if (i > 0) {
            t.slope = fmax(t.slope, fabs((double)g[i] - (double)g[i - 1]));
        }
    }
    double mag[6];
    for (int h = 1; h <= 5; h++) {   /* 5 Hz x h: bin 5h of a 1 s DFT */
        double re = 0.0, im = 0.0;
        for (uint32_t i = 0; i < N; i++) {
            re += g[i] * cos(2.0 * M_PI * 5.0 * h * i / N);
            im -= g[i] * sin(2.0 * M_PI * 5.0 * h * i / N);
        }
        mag[h] = sqrt(re * re + im * im);
    }
    for (int h = 2; h <= 5; h++) {
        t.harm_db = fmax(t.harm_db, 20.0 * log10(mag[h] / mag[1] + 1e-30));
    }
    return t;
}

#define TREMOLO_SLOPE_MAX (1.01 * 0.5 * (1.0 - TREMOLO_TROUGH) * 2.0 * M_PI * 5.0 / 48000.0)

static int tremolo_smooth(trem_shape t)
{
    return t.trough >= TREMOLO_TROUGH - 1e-4 && t.slope <= TREMOLO_SLOPE_MAX && t.harm_db <= -60.0;
}

static void test_tremolo_smoothness(void)
{
    trem_shape t = tremolo_shape(&FX_EFFECTS[3]);
    trem_shape r = tremolo_shape(&FX_TEST_MUTANTS[FX_MUT_TREMOLO_ROUNDED_TRI]);
    trem_shape f = tremolo_shape(&FX_TEST_MUTANTS[FX_MUT_TREMOLO_NO_FLOOR]);
    if (g_verbose) {
        printf("  tremolo  shape: trough %.4f, slope %.3e/sample, harmonics %.1f dB; control "
               "(rounded triangle) %.4f, %.3e, %.1f dB; control (sine, no floor) %.4f, %.3e, "
               "%.1f dB; limits >= %.2f, <= %.3e, <= -60\n", t.trough, t.slope, t.harm_db,
               r.trough, r.slope, r.harm_db, f.trough, f.slope, f.harm_db, TREMOLO_TROUGH,
               TREMOLO_SLOPE_MAX);
    }
    CHECK(tremolo_smooth(t), "tremolo: trough %.4f, slope %.3e, harmonics %.1f dB", t.trough,
          t.slope, t.harm_db);
    /* Each criterion fails at least one control. */
    CHECK(r.trough < TREMOLO_TROUGH - 1e-4 && f.trough < TREMOLO_TROUGH - 1e-4,
          "tremolo: a control met the trough");
    CHECK(r.slope > TREMOLO_SLOPE_MAX && f.slope > TREMOLO_SLOPE_MAX,
          "tremolo: a control met the slope limit");
    CHECK(r.harm_db > -60.0, "tremolo: control (rounded triangle) met the purity limit: %.1f dB",
          r.harm_db);
}

/* The before/after report (--tremolo-shape): 21 points of one cycle's gain at
 * full depth, and the worst per-sample slope at 48 kHz. */
static void tremolo_shape_report(void)
{
    printf("Tremolo gain over one cycle at full depth (phase, before = rounded triangle to 0, "
           "after = sine to %.2f)\n", TREMOLO_TROUGH);
    for (int j = 0; j <= 20; j++) {
        float ph = (float)j / 20.0f;
        double before = 0.5 + 0.5 * fx_test_lfo_rounded(ph);
        double after = TREMOLO_TROUGH + (1.0 - TREMOLO_TROUGH) * (0.5 + 0.5 * fx_test_lfo_sine(ph));
        printf("  %.2f  before %.3f %-22.*s after %.3f %.*s\n", (double)ph, before,
               (int)(before * 20.0 + 0.5), "######################", after,
               (int)(after * 20.0 + 0.5), "######################");
    }
    trem_shape t = tremolo_shape(&FX_EFFECTS[3]);
    trem_shape r = tremolo_shape(&FX_TEST_MUTANTS[FX_MUT_TREMOLO_ROUNDED_TRI]);
    printf("worst per-sample gain slope at 48 kHz: before %.3e, after %.3e\n", r.slope, t.slope);
    printf("trough: before %.4f, after %.4f; harmonics 2-5: before %.1f dB, after %.1f dB\n",
           r.trough, t.trough, r.harm_db, t.harm_db);
}

/* --- the rotary actually moves the pitch ----------------------------------- */

/* A 2 kHz tone (horn band), both channels, through the rotary at full depth:
 * 3 s to spin up, then 1 s of the left output, demodulated one 24-sample
 * cycle at a time. The phase step between cycles is the frequency offset;
 * returns the largest, as a fraction of 2 kHz. Shipped: about 1.7 % (the
 * horn's 0.40 ms excursion at 6.7 Hz). Control: the rotary with its delays
 * held still (level modulation only), which moves no phase. Limit 0.5 %. */
#define ROTARY_PITCH_MIN 0.005

static double rotary_pitch_dev(const fx_effect_desc *d)
{
    enum { P = 24, SPIN = 3 * 48000, N = 48000 };
    static float y[N];
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], full[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        full[i] = 1.0f;
    }
    const float *const in[FX_CHANNELS] = { l, l };
    float *const wet[FX_CHANNELS] = { wl, wr };
    long t = 0;
    for (long off = 0; off < SPIN + N; off += FX_CHUNK) {
        for (uint32_t i = 0; i < FX_CHUNK; i++, t++) {
            l[i] = 0.25f * (float)sin(2.0 * M_PI * (double)(t % P) / P);
        }
        d->process(st, in, wet, full, FX_CHUNK);
        if (off >= SPIN) {
            memcpy(y + (off - SPIN), wl, sizeof(wl));
        }
    }
    free(st);
    double worst = 0.0, prev = 0.0;
    for (int c = 0; c < N / P; c++) {
        double re = 0.0, im = 0.0;
        for (int i = 0; i < P; i++) {
            re += y[c * P + i] * cos(2.0 * M_PI * i / P);
            im -= y[c * P + i] * sin(2.0 * M_PI * i / P);
        }
        double ph = atan2(im, re);
        if (c > 0) {
            double step = ph - prev;
            step -= 2.0 * M_PI * floor((step + M_PI) / (2.0 * M_PI));   /* to (-pi, pi] */
            worst = fmax(worst, fabs(step) / (2.0 * M_PI));   /* cycles per cycle */
        }
        prev = ph;
    }
    return worst;
}

static void test_rotary_pitch(void)
{
    double p = rotary_pitch_dev(&FX_EFFECTS[FX_I_ROTARY]);
    double c = rotary_pitch_dev(&FX_TEST_MUTANTS[FX_MUT_ROTARY_NO_DOPPLER]);
    if (g_verbose) {
        printf("  rotary   pitch, 2 kHz at full speed: +-%.2f %% (%.0f cents); control (no Doppler) "
               "+-%.3f %%; limit %.1f %%\n", 100.0 * p, 1200.0 * log2(1.0 + p), 100.0 * c,
               100.0 * ROTARY_PITCH_MIN);
    }
    CHECK(p >= ROTARY_PITCH_MIN, "rotary: the pitch moves only +-%.3f %%", 100.0 * p);
    CHECK(c < ROTARY_PITCH_MIN, "rotary: control (no Doppler) moved the pitch +-%.3f %% too: the "
          "check cannot tell level from pitch", 100.0 * c);
}

/* --- the rotors have inertia ----------------------------------------------- */

/* From rest at the slow speed, the knob to the top for 12 s, then to 0 for
 * 12 s: how long each rotor takes to cover 90 % of the way, up and down.
 * Shipped: horn ~0.8 s up and ~1.4 s down, drum ~3.5 s and ~4.6 s (one-pole
 * time constants 0.35 / 0.60 and 1.5 / 2.0 s). Limits: the horn 0.4-2 s up
 * and 0.6-3 s down; the drum 2-6 s and 2.5-8 s, and slower than the horn
 * both ways. Control: a rotor whose speed jumps to the knob's. */
typedef struct {
    double up[2], down[2];   /* seconds, horn then drum */
} spin_times;

static spin_times rotary_spin(const fx_effect_desc *d)
{
    static const float slow[2] = { 0.80f, 0.67f }, fast[2] = { 6.7f, 5.7f };
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], amt[FX_CHUNK];
    memset(l, 0, sizeof(l));
    const float *const in[FX_CHANNELS] = { l, l };
    float *const wet[FX_CHANNELS] = { wl, wr };
    spin_times s = { { -1.0, -1.0 }, { -1.0, -1.0 } };
    const long half = 12L * 48000L;
    for (long t = 0; t < 2 * half; t += FX_CHUNK) {
        const int going_up = t < half;
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            amt[i] = going_up ? 1.0f : 0.0f;
        }
        d->process(st, in, wet, amt, FX_CHUNK);
        for (int r = 0; r < 2; r++) {
            const float hz = fx_test_rotary_speed(st, r);
            const double sec = (double)(going_up ? t + FX_CHUNK : t + FX_CHUNK - half) / RATE;
            if (going_up && s.up[r] < 0.0 && hz >= slow[r] + 0.9f * (fast[r] - slow[r])) {
                s.up[r] = sec;
            }
            if (!going_up && s.down[r] < 0.0 && hz <= fast[r] - 0.9f * (fast[r] - slow[r])) {
                s.down[r] = sec;
            }
        }
    }
    free(st);
    return s;
}

static int realistic_spin(spin_times s)
{
    return s.up[0] >= 0.4 && s.up[0] <= 2.0 && s.down[0] >= 0.6 && s.down[0] <= 3.0 &&
           s.up[1] >= 2.0 && s.up[1] <= 6.0 && s.down[1] >= 2.5 && s.down[1] <= 8.0 &&
           s.up[1] > s.up[0] && s.down[1] > s.down[0];
}

static void test_rotary_inertia(void)
{
    spin_times s = rotary_spin(&FX_EFFECTS[FX_I_ROTARY]);
    spin_times c = rotary_spin(&FX_TEST_MUTANTS[FX_MUT_ROTARY_NO_INERTIA]);
    if (g_verbose) {
        printf("  rotary   spin, 90 %% of the way: horn up %.2f s down %.2f s, drum up %.2f s down "
               "%.2f s; control (no inertia) %.3f / %.3f / %.3f / %.3f s\n", s.up[0], s.down[0],
               s.up[1], s.down[1], c.up[0], c.down[0], c.up[1], c.down[1]);
    }
    CHECK(realistic_spin(s), "rotary: spin times horn %.2f / %.2f s, drum %.2f / %.2f s", s.up[0],
          s.down[0], s.up[1], s.down[1]);
    CHECK(!realistic_spin(c), "rotary: control (no inertia) passed the spin times");
}

/* --- the rotary's two sides keep the input's power ------------------------- */

/* A 3 kHz tone (the horn's band) at 0.3 through the rotary alone at full
 * depth (fast), 3 s to spin up, then 2 s in 10 ms windows: each window's
 * L^2 + R^2 out over in. The horn's and the drum's gains are constant-power
 * pairs, sqrt(1 + a c) and sqrt(1 - a c), so as each rotor turns the sound
 * moves across the image and the sum stays put: what is left is the Doppler
 * and the crossover. The spread (loudest window over quietest) is the check:
 * shipped 1.25 dB. Limit 1.8 dB. Control: gains 1 + a c and 1 - a c, whose
 * power sum swells by 2 a^2 c^2 (+1.7 dB at the horn's a = 0.7): 2.58 dB. */
#define ROTARY_POWER_SPREAD_DB 1.8

static double rotary_power_spread_db(const fx_effect_desc *d)
{
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], full[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        full[i] = 1.0f;
    }
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    signal_gen g = { SIG_SINE, 0.3f, 3000.0f, 0.0 };
    const long settle = 3 * (long)RATE, total = 5 * (long)RATE, win = (long)RATE / 100;
    double so = 0.0, si = 0.0, lo = 1e9, hi = 0.0;
    long n = 0;
    for (long t = 0; t < total; t += FX_CHUNK) {
        sig_fill(&g, l, r, FX_CHUNK);
        d->process(st, in, wet, full, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK && t >= settle; i++) {
            so += (double)wl[i] * wl[i] + (double)wr[i] * wr[i];
            si += (double)l[i] * l[i] + (double)r[i] * r[i];
            if (++n == win) {
                lo = fmin(lo, so / si);
                hi = fmax(hi, so / si);
                so = si = 0.0;
                n = 0;
            }
        }
    }
    free(st);
    return 10.0 * log10(hi / lo);
}

static void test_rotary_power(void)
{
    double s = rotary_power_spread_db(&FX_EFFECTS[FX_I_ROTARY]);
    double c = rotary_power_spread_db(&FX_TEST_MUTANTS[FX_MUT_ROTARY_LINEAR_AM]);
    if (g_verbose) {
        printf("  rotary   power, 3 kHz at full, 10 ms windows: spread %.2f dB; control (linear "
               "AM) %.2f dB; limit %.1f\n", s, c, ROTARY_POWER_SPREAD_DB);
    }
    CHECK(s <= ROTARY_POWER_SPREAD_DB, "rotary: L^2 + R^2 swings %.2f dB as the rotors turn", s);
    CHECK(c > ROTARY_POWER_SPREAD_DB, "rotary: control (linear AM) swung only %.2f dB", c);
}

/* --- the auto-wah's band-pass peaks at 1 ------------------------------------- */

/* 48 tones, log-spaced 250 Hz..2.5 kHz, at 0.4 through the auto-wah alone at
 * full depth: 0.2 s to settle (the follower holds the band still on a steady
 * tone), then the wet's RMS over the input's for 0.1 s. The band-pass is
 * normalised to a peak gain of exactly 1, and the tones are 5 % apart, so
 * the loudest lands on or next to the centre: shipped 1.0000. Limits
 * 0.97..1.01 (a wet 6 % hot reads 1.06). The level curve gives back what the band takes from broadband
 * material; this is what it must not have to give back twice. Control: the
 * whistle (Q 12, unnormalised). */
static double wah_peak_gain(const fx_effect_desc *d)
{
    double worst = 0.0;
    for (int j = 0; j < 48; j++) {
        void *st = calloc(1, d->state_bytes);
        CHECK(st != NULL && d->init(st, RATE) == 0, "init");
        float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], full[FX_CHUNK];
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            full[i] = 1.0f;
        }
        const float *const in[FX_CHANNELS] = { l, r };
        float *const wet[FX_CHANNELS] = { wl, wr };
        signal_gen g = { SIG_SINE, 0.4f, (float)(250.0 * pow(10.0, j / 47.0)), 0.0 };
        double so = 0.0, si = 0.0;
        for (long t = 0; t < (long)(0.3 * RATE); t += FX_CHUNK) {
            sig_fill(&g, l, r, FX_CHUNK);
            d->process(st, in, wet, full, FX_CHUNK);
            for (uint32_t i = 0; i < FX_CHUNK && t >= (long)(0.2 * RATE); i++) {
                so += (double)wl[i] * wl[i];
                si += (double)l[i] * l[i];
            }
        }
        free(st);
        worst = fmax(worst, sqrt(so / si));
    }
    return worst;
}

static void test_wah_peak_gain(void)
{
    double s = wah_peak_gain(&FX_EFFECTS[FX_I_WAH]);
    double c = wah_peak_gain(&FX_TEST_MUTANTS[FX_MUT_WAH_WHISTLE]);
    if (g_verbose) {
        printf("  auto-wah band-pass peak, 48 tones 250 Hz..2.5 kHz: %.4f; control (whistle) "
               "%.4f; limits 0.97..1.01\n", s, c);
    }
    CHECK(s >= 0.97 && s <= 1.01, "auto-wah: band-pass peak gain %.4f, wanted 0.97..1.01", s);
    CHECK(c > 1.01, "auto-wah: control (whistle) peaked at %.4f", c);
}

/* --- the auto-wah opens on the attack ---------------------------------------- */

/* Noise at 0.01 for 0.5 s, then at 0.4: the wet's RMS frequency (as in
 * wah_sweep_ratio) over 5..25 ms after the step, over the quiet half's last
 * 0.25 s. The envelope's 4 ms attack has the band most of the way open by
 * then, so a note's attack is what sweeps it, the point of an envelope wah.
 * Shipped 2.37x. Limit 1.8x. Control: a 400 ms attack, which leaves the band
 * near where the quiet noise had it: 1.28x. */
#define WAH_ONSET_MIN 1.8

static double wah_onset_ratio(const fx_effect_desc *d)
{
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], full[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        full[i] = 1.0f;
    }
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    const long step = 24000, a0 = step + 240, a1 = step + 1200, total = step + 1200;
    double sd[2] = { 0.0, 0.0 }, sy[2] = { 0.0, 0.0 };
    float prev = 0.0f;
    for (long t = 0; t < total; t += FX_CHUNK) {
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            const float amp = t + (long)i < step ? 0.01f : 0.4f;
            l[i] = amp * noise();
            r[i] = amp * noise();
        }
        d->process(st, in, wet, full, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            const long u = t + (long)i;
            const int k = u >= a0 && u < a1 ? 0 : (u >= step / 2 && u < step ? 1 : -1);
            if (k >= 0) {
                sd[k] += (double)(wl[i] - prev) * (wl[i] - prev);
                sy[k] += (double)wl[i] * wl[i];
            }
            prev = wl[i];
        }
    }
    free(st);
    return sqrt(sd[0] / sy[0]) / sqrt(sd[1] / sy[1]);
}

static void test_wah_onset(void)
{
    double s = wah_onset_ratio(&FX_EFFECTS[FX_I_WAH]);
    double c = wah_onset_ratio(&FX_TEST_MUTANTS[FX_MUT_WAH_SLOW_ATTACK]);
    if (g_verbose) {
        printf("  auto-wah onset, 5..25 ms after a 32 dB step up: band at %.2fx the quiet one's; "
               "control (400 ms attack) %.2fx; limit %.1fx\n", s, c, WAH_ONSET_MIN);
    }
    CHECK(s >= WAH_ONSET_MIN, "auto-wah: 5..25 ms into a loud note the band has moved only %.2fx",
          s);
    CHECK(c < WAH_ONSET_MIN, "auto-wah: control (400 ms attack) moved it %.2fx too", c);
}

/* --- the auto-wah actually sweeps ------------------------------------------ */

/* White noise through the auto-wah at full depth, 0.5 s loud (0.4) then
 * 0.5 s quiet (0.01), four times. In the last 0.25 s of each half, the wet's
 * RMS frequency, rate / 2 pi x sqrt(E[dy^2] / E[y^2]): where the band-pass
 * sits. Returns loud over quiet. Shipped: 3045 / 1510 Hz, 2.02x (the filter
 * opens on the loud noise and closes on the quiet; the centre itself moves
 * further, the band-pass's skirts pull this measure toward the middle).
 * Control: the envelope never reaching the filter, 1.00x. Limit 1.5x. */
#define WAH_SWEEP_MIN 1.5

static double wah_sweep_ratio(const fx_effect_desc *d, double *loud_hz, double *quiet_hz)
{
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], full[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        full[i] = 1.0f;
    }
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    const long half = 24000;
    double sd[2] = { 0.0, 0.0 }, sy[2] = { 0.0, 0.0 };
    float prev = 0.0f;
    for (long t = 0; t < 8 * half; t += FX_CHUNK) {
        const int quiet = (t / half) % 2;
        const float amp = quiet ? 0.01f : 0.4f;
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            l[i] = amp * noise();
            r[i] = amp * noise();
        }
        d->process(st, in, wet, full, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            if ((t + (long)i) % half >= half / 2) {
                sd[quiet] += (double)(wl[i] - prev) * (wl[i] - prev);
                sy[quiet] += (double)wl[i] * wl[i];
            }
            prev = wl[i];
        }
    }
    free(st);
    *loud_hz = RATE / (2.0 * M_PI) * sqrt(sd[0] / sy[0]);
    *quiet_hz = RATE / (2.0 * M_PI) * sqrt(sd[1] / sy[1]);
    return *loud_hz / *quiet_hz;
}

static void test_wah_sweeps(void)
{
    double lh, qh, clh, cqh;
    double s = wah_sweep_ratio(&FX_EFFECTS[FX_I_WAH], &lh, &qh);
    double c = wah_sweep_ratio(&FX_TEST_MUTANTS[FX_MUT_WAH_NO_SWEEP], &clh, &cqh);
    if (g_verbose) {
        printf("  auto-wah sweep: loud %.0f Hz / quiet %.0f Hz = %.2fx; control (no sweep) %.0f / "
               "%.0f = %.2fx; limit %.1fx\n", lh, qh, s, clh, cqh, c, WAH_SWEEP_MIN);
    }
    CHECK(s >= WAH_SWEEP_MIN, "auto-wah: loud vs quiet moves the band only %.2fx", s);
    CHECK(c < WAH_SWEEP_MIN, "auto-wah: control (no sweep) moved it %.2fx too", c);
}

/* --- the auto-wah never whistles ------------------------------------------- */

/* A 20 ms noise burst (0.5) and then silence, through the auto-wah at 30 %
 * (the band stays low, 280-530 Hz, where a resonance rings longest): how long
 * after the burst the wet stays above -40 dB re its peak. A band-pass rings
 * for about 4.6 x 2Q / (2 pi f): ~6 ms at Q 2.2 and 530 Hz, ~33 ms at Q 12.
 * Limit 15 ms. Control: Q 12 (the whistle mutant). */
#define WAH_RING_MAX_MS 15.0

static double wah_ring_ms_at(const fx_effect_desc *d, float depth, float burst_amp)
{
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], amt[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        amt[i] = depth;
    }
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    const long burst = 960, total = 960 + 24000;
    float peak = 0.0f;
    long last_loud = burst;
    for (long t = 0; t < total; t += FX_CHUNK) {
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            l[i] = t + (long)i < burst ? burst_amp * noise() : 0.0f;
            r[i] = l[i];
        }
        d->process(st, in, wet, amt, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK; i++) {
            const float a = fabsf(wl[i]);
            if (t + (long)i < burst) {
                peak = fmaxf(peak, a);
            } else if (a > 0.01f * peak) {
                last_loud = t + (long)i;
            }
        }
    }
    free(st);
    return 1000.0 * (double)(last_loud - burst) / RATE;
}

static double wah_ring_ms(const fx_effect_desc *d)
{
    return wah_ring_ms_at(d, 0.3f, 0.5f);
}

static void test_wah_never_whistles(void)
{
    double m = wah_ring_ms(&FX_EFFECTS[FX_I_WAH]);
    double c = wah_ring_ms(&FX_TEST_MUTANTS[FX_MUT_WAH_WHISTLE]);
    if (g_verbose) {
        printf("  auto-wah ring-down to -40 dB after a burst: %.1f ms; control (Q 12) %.1f ms; "
               "limit %.0f ms\n", m, c, WAH_RING_MAX_MS);
    }
    CHECK(m <= WAH_RING_MAX_MS, "auto-wah: rings %.1f ms after the input stops", m);
    CHECK(c > WAH_RING_MAX_MS, "auto-wah: control (Q 12) rang only %.1f ms", c);
}

/* --- the top thirds (2026-10-15) -------------------------------------------- */

/* Below depth 2/3 the drive and the auto-wah play exactly what they did
 * before their top thirds were made stronger: keys through each alone, at
 * nine depths up to 2/3, with the top third on and off, bit for bit. Control:
 * the same at full depth must differ (the comparison can see a change). */
static long top_diff(int k, float depth)
{
    float out[2][PERIOD * 40];
    for (int pass = 0; pass < 2; pass++) {
        fx_test_no_top = pass;
        fx_engine e;
        CHECK(fx_engine_init(&e, FX_EFFECTS, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
        signal_gen g = { SIG_KEYS, 0.5f, 0.0f, 0.0 };
        float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
        for (int p = 0; p < 40; p++) {
            sig_fill(&g, l, r, PERIOD);
            run_period(&e, l, r, ol, or_, PERIOD, BIT(k), depth);
            memcpy(out[pass] + p * PERIOD, ol, sizeof(ol));
        }
        fx_engine_free(&e);
    }
    fx_test_no_top = 0;
    long n = 0;
    for (uint32_t i = 0; i < PERIOD * 40; i++) {
        n += memcmp(&out[0][i], &out[1][i], sizeof(float)) != 0;
    }
    return n;
}

static void test_top_third_below(void)
{
    static const int ks[2] = { FX_I_DRIVE, FX_I_WAH };
    for (int q = 0; q < 2; q++) {
        long worst = 0;
        for (int j = 1; j <= 9; j++) {
            long n = top_diff(ks[q], (2.0f / 3.0f) * (float)j / 9.0f);
            worst = n > worst ? n : worst;
        }
        long c = top_diff(ks[q], 1.0f);
        if (g_verbose) {
            printf("  %-8s top third off vs on, 9 depths to 2/3: %ld samples differ; control "
                   "(full) %ld\n", FX_LABEL[ks[q]], worst, c);
        }
        CHECK(worst == 0, "%s: the top third reaches below 2/3 (%ld samples)", FX_LABEL[ks[q]],
              worst);
        CHECK(c > 0, "%s: control (full depth) the same with the top third off",
              FX_LABEL[ks[q]]);
    }
}

/* The drive's top is much stronger: on the smoothness check's tone (250 Hz at
 * 0.5, full), h5 and h7 rise by at least DRIVE_TOP_RISE_DB over the top third
 * off (round 4: h5 -19.5, h7 -26.3; shipped -13.6 / -16.8). Control: the top
 * third off, a rise of 0. The aliasing check holds the new top to -35 dB. */
#define DRIVE_TOP_RISE_DB 4.0

static void test_drive_top(void)
{
    double h[7], c[7];
    harmonic_profile(FX_EFFECTS, FX_I_DRIVE, 0.5f, 1.0f, h);
    fx_test_no_top = 1;
    harmonic_profile(FX_EFFECTS, FX_I_DRIVE, 0.5f, 1.0f, c);
    fx_test_no_top = 0;
    const double r5 = h[4] - c[4], r7 = h[6] - c[6];
    if (g_verbose) {
        printf("  drive    top: h3 %+.1f h5 %+.1f h7 %+.1f dB at full; top third off h3 %+.1f h5 "
               "%+.1f h7 %+.1f; rise h5 %+.1f h7 %+.1f, limit %+.0f\n", h[2], h[4], h[6], c[2],
               c[4], c[6], r5, r7, DRIVE_TOP_RISE_DB);
    }
    CHECK(r5 >= DRIVE_TOP_RISE_DB && r7 >= DRIVE_TOP_RISE_DB, "drive: the top third adds only "
          "h5 %+.1f / h7 %+.1f dB", r5, r7);
}

/* The band-pass's gain on a sine at full depth, wet over input RMS over the
 * last 0.1 s of 0.3 s: near 1 when the tone opens the band onto itself. */
static double wah_tone_gain(const fx_effect_desc *d, float hz, float amp)
{
    void *st = calloc(1, d->state_bytes);
    CHECK(st != NULL && d->init(st, RATE) == 0, "init");
    float l[FX_CHUNK], r[FX_CHUNK], wl[FX_CHUNK], wr[FX_CHUNK], full[FX_CHUNK];
    for (uint32_t i = 0; i < FX_CHUNK; i++) {
        full[i] = 1.0f;
    }
    const float *const in[FX_CHANNELS] = { l, r };
    float *const wet[FX_CHANNELS] = { wl, wr };
    signal_gen g = { SIG_SINE, amp, hz, 0.0 };
    double so = 0.0, si = 0.0;
    for (long t = 0; t < (long)(0.3 * RATE); t += FX_CHUNK) {
        sig_fill(&g, l, r, FX_CHUNK);
        d->process(st, in, wet, full, FX_CHUNK);
        for (uint32_t i = 0; i < FX_CHUNK && t >= (long)(0.2 * RATE); i++) {
            so += (double)wl[i] * wl[i];
            si += (double)l[i] * l[i];
        }
    }
    free(st);
    return sqrt(so / si);
}

/* The auto-wah's top is deeper, wider and more resonant. At full depth:
 *   - wider: a loud 2.5 kHz tone (0.9, which opens it 95 %) has the band on
 *     itself, gain at least WAH_TOP_OPEN_MIN (the old range put it at
 *     2.1 kHz, below the tone);
 *   - deeper: a quiet 200 Hz tone (0.001) sits in the band near its new
 *     bottom, gain at least WAH_TOP_OPEN_MIN (the old 280 Hz bottom, lower);
 *   - more resonant: a loud burst (the band high, Q 4) rings longer than
 *     with the top third off;
 *   - no whistle: a quiet burst (0.01, the band near the bottom, where a
 *     resonance rings longest) still dies within WAH_RING_MAX_MS.
 * Controls: the top third off must miss both gains; Q 12 must exceed the
 * ring limit on the quiet burst. */
#define WAH_TOP_OPEN_MIN 0.9

static void test_wah_top(void)
{
    const fx_effect_desc *d = &FX_EFFECTS[FX_I_WAH];
    double hi = wah_tone_gain(d, 2500.0f, 0.9f);
    double lo = wah_tone_gain(d, 200.0f, 0.001f);
    double ring = wah_ring_ms_at(d, 1.0f, 0.5f);
    double quiet = wah_ring_ms_at(d, 1.0f, 0.01f);
    fx_test_no_top = 1;
    double chi = wah_tone_gain(d, 2500.0f, 0.9f);
    double clo = wah_tone_gain(d, 200.0f, 0.001f);
    double cring = wah_ring_ms_at(d, 1.0f, 0.5f);
    double cquiet = wah_ring_ms_at(d, 1.0f, 0.01f);
    fx_test_no_top = 0;
    double wring = wah_ring_ms_at(&FX_TEST_MUTANTS[FX_MUT_WAH_WHISTLE], 1.0f, 0.01f);
    if (g_verbose) {
        printf("  auto-wah top: band gain at full, 2.5 kHz loud %.3f (off %.3f), 200 Hz quiet %.3f "
               "(off %.3f), limit %.2f; ring, loud burst %.1f ms (off %.1f), quiet %.1f ms (off "
               "%.1f), control (Q 12, quiet) %.1f, limit %.0f\n", hi, chi, lo, clo,
               WAH_TOP_OPEN_MIN, ring, cring, quiet, cquiet, wring, WAH_RING_MAX_MS);
    }
    CHECK(hi >= WAH_TOP_OPEN_MIN, "auto-wah: at full the band does not reach 2.5 kHz (%.3f)", hi);
    CHECK(lo >= WAH_TOP_OPEN_MIN, "auto-wah: at full the band does not reach 200 Hz (%.3f)", lo);
    CHECK(chi < WAH_TOP_OPEN_MIN && clo < WAH_TOP_OPEN_MIN, "auto-wah: control (top third off) "
          "reached both ends too: %.3f / %.3f", chi, clo);
    CHECK(ring > cring, "auto-wah: no more resonance at the top (ring %.1f vs %.1f ms)", ring,
          cring);
    CHECK(quiet <= WAH_RING_MAX_MS, "auto-wah: a quiet note rings %.1f ms at full", quiet);
    CHECK(wring > WAH_RING_MAX_MS, "auto-wah: control (Q 12) rang only %.1f ms at full", wring);
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
#define BIT_ROTARY BIT(FX_I_ROTARY)
#define BIT_WAH BIT(FX_I_WAH)

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
    /* Every effect but two. The drive's own makeup is dynamic. The rotary's
     * rotors integrate the knob's path into their angle, so a reference fed
     * the target from the first frame runs at another angle for good (the
     * residual reads about -9 dB with or without the control). Its level
     * gain is the same table lookup as the other five's. */
    static const int ks[5] = { 0, 2, 3, FX_I_FLANGER, FX_I_WAH };
    for (int j = 0; j < 5; j++) {
        int k = ks[j];
        const float at = 0.7f;
        float gain = 0.0f, cg = 0.0f;
        double res = static_level_residual_db(k, at, &gain);
        fx_test_level_wobble = 1;
        double c = static_level_residual_db(k, at, &cg);
        fx_test_level_wobble = 0;
        if (g_verbose) {
            printf("  %-8s static level at %.0f%%: gain %+.2f dB, residual %.1f dB; control "
                   "(gain wobbling +-0.5 dB) %.1f dB\n", FX_LABEL[k], 100.0 * at, db(gain), res, c);
        }
        CHECK(res < -100.0, "%s: output is not the #149 mix times a constant: residual %.1f dB",
              FX_LABEL[k], res);
        CHECK(gain > 1.0f, "%s: no level gain at %.0f%%: %+.2f dB", FX_LABEL[k], 100.0 * at,
              db(gain));
        CHECK(c >= -100.0, "%s: control (wobbling gain) passed too: %.1f dB", FX_LABEL[k], c);
    }
}

/* --- order ----------------------------------------------------------------- */

/* Seven test effects that do not commute, in the shipped table's slots: the
 * drive's clips at ±0.1, the auto-wah's subtracts 0.02, the phaser's scales
 * by 0.25, the flanger's is 2x + 0.03, the chorus's adds 0.01, the rotary's
 * is x^2 + 0.005, the tremolo's inverts. A constant 0.5 through all seven in
 * the shipped order (drive, auto-wah, phaser, flanger, chorus, rotary,
 * tremolo): 0.1, 0.08, 0.02, 0.07, 0.08, 0.0114, -0.0114. Swapping any two
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
OP(op_square, x * x + 0.005f)
OP(op_sub, x - 0.02f)
#define ORDER_OUT (-0.0114f)

static float chain_order_output(void)
{
    const fx_effect_desc ops[N_FX] = {
        { "add", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_add, NULL, NULL },
        { "clip", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_clip, NULL, NULL },
        { "scale", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_scale, NULL, NULL },
        { "neg", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_neg, NULL, NULL },
        { "affine", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_affine, NULL, NULL },
        { "square", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_square, NULL, NULL },
        { "sub", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1, op_init, op_sub, NULL, NULL },
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
    CHECK(FX_CHAIN_LEN == N_FX && e.order[0] == 1 && e.order[1] == FX_I_WAH && e.order[2] == 2 &&
          e.order[3] == FX_I_FLANGER && e.order[4] == 0 && e.order[5] == FX_I_ROTARY &&
          e.order[6] == 3,
          "run order is not drive, auto-wah, phaser, flanger, chorus, rotary, tremolo");
    fx_engine_free(&e);
    float y = chain_order_output();
    fx_test_reverse_order = 1;
    float c = chain_order_output();
    fx_test_reverse_order = 0;
    if (g_verbose) {
        printf("  chain    order: %.6f (drive, auto-wah, phaser, flanger, chorus, rotary, "
               "tremolo = %.4f); control (reversed) %.6f\n", (double)y, (double)ORDER_OUT,
               (double)c);
    }
    CHECK(fabsf(y - ORDER_OUT) < 1e-6f, "chain order: 0.5 came out %.6f, not %.4f", (double)y,
          (double)ORDER_OUT);
    CHECK(fabsf(c - ORDER_OUT) >= 1e-6f, "chain order: control (reversed) gave %.4f too",
          (double)ORDER_OUT);
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
    CHECK(m1 == 0, "empty mask after all seven: %zu periods differ from the input", m1);
    CHECK(m2 == 0, "all seven at depth 0: %zu periods differ from the input", m2);
    size_t c = bypass_mismatches_mask(FX_ALL, 1.0f / 127.0f, FX_ALL);
    CHECK(c > 0, "control (all seven at depth 1/127) also identical: the comparison is blind");
    if (g_verbose) {
        printf("  chain    bypass: empty mask %zu, after all seven %zu, depth 0 %zu differing "
               "periods; control (1/127) %zu\n", m0, m1, m2, c);
    }
}

/* --- toggles crossfade --------------------------------------------------- */

static void test_chain_toggles(void)
{
    const uint32_t masks[] = {
        0u, BIT_DRIVE, BIT_DRIVE | BIT_CHORUS, BIT_CHORUS, FX_ALL, BIT_PHASER | BIT_TREMOLO,
        0u, FX_ALL, BIT_TREMOLO, BIT_FLANGER, BIT_ROTARY, BIT_WAH | BIT_ROTARY, BIT_WAH,
        BIT_DRIVE | BIT_WAH, BIT_PHASER | BIT_FLANGER, BIT_PHASER,
    };
    const int segs = (int)(sizeof(masks) / sizeof(masks[0]));
    float depths[sizeof(masks) / sizeof(masks[0])];
    for (int i = 0; i < segs; i++) {
        depths[i] = 0.6f;
    }
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    steps s = measure_steps_sig(&e, masks, depths, segs, 190, 60, 0.1f, 110.0f);
    int landed = 1;
    for (int k = 0; k < N_FX; k++) {
        landed &= e.on[k] == (k == 2 ? 1.0f : 0.0f);
    }
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

/* --- unity, any mask ------------------------------------------------------- */

/* One mask (1..127), or a control (CHAIN_CTL_*): the bounds are CHAIN_LO_DB..
 * CHAIN_HI_DB on the keys and CEIL_DB on every signal (see UNITY_TOL_DB).
 *
 * The 31 masks of the first five effects: 0, 50 and 100 %, every signal. The
 * 96 with the rotary or the auto-wah: 50 and 100 %, on loud noise and the
 * keys, in shorter windows (g_short_windows). Depth 0 is the input bit for
 * bit (test_chain_bypass), so it reads 0 dB by construction. The pure tones
 * of the old masks are the narrowest spectrum there is, and they are swept
 * with every old stage; the noise and the keys carry the broadband and the
 * real-material cases for the new ones. All 96 x 8 signals x 3 depths would
 * have cost about 20 s of wall time on a 16-core laptop; these cost about 3.
 *
 * Controls, each on the mask nearest the bound it tests: phaser, flanger and
 * rotary (the loudest at full) with every level curve 0.5 dB hot; chorus,
 * drive, tremolo and flanger (the quietest) 0.5 dB cold; phaser and flanger
 * with the phaser's feedback at 0.95, for the ceiling. */
#define NEW_FX (BIT_ROTARY | BIT_WAH)
#define CHAIN_NEW_SIGNALS ((1u << 0) | MASK_KEYS)
#define CHAIN_CTL_HOT ((int)FX_ALL + 1)
#define CHAIN_CTL_COLD ((int)FX_ALL + 2)
#define CHAIN_CTL_CEIL ((int)FX_ALL + 3)
#define CHAIN_HOT_MASK (BIT_PHASER | BIT_FLANGER | BIT_ROTARY)
#define CHAIN_COLD_MASK (BIT_CHORUS | BIT_DRIVE | BIT_TREMOLO | BIT_FLANGER)
static void chain_level_part(int m, void *slot)
{
    level_result *out = slot;
    if (m == CHAIN_CTL_HOT || m == CHAIN_CTL_COLD) {
        fx_test_level_hot = m == CHAIN_CTL_HOT;
        fx_test_level_cold = m == CHAIN_CTL_COLD;
        g_short_windows = 1;
        *out = level_sweep_from(FX_EFFECTS, m == CHAIN_CTL_HOT ? CHAIN_HOT_MASK : CHAIN_COLD_MASK,
                                MASK_KEYS, 1, 2, NULL);
        g_short_windows = 0;
        fx_test_level_hot = 0;
        fx_test_level_cold = 0;
        return;
    }
    if (m == CHAIN_CTL_CEIL) {
        *out = level_sweep_from(table_with(2, &FX_TEST_MUTANTS[FX_MUT_PHASER_HOT_FEEDBACK]),
                                BIT_PHASER | BIT_FLANGER, MASK_ALL, 1, 2, NULL);
        return;
    }
    if ((uint32_t)m & NEW_FX) {
        g_short_windows = 1;
        *out = level_sweep_from(FX_EFFECTS, (uint32_t)m, CHAIN_NEW_SIGNALS, 1, 2, NULL);   /* 50, 100 % */
        g_short_windows = 0;
        return;
    }
    *out = level_sweep(FX_EFFECTS, (uint32_t)m, MASK_ALL, 2, NULL);   /* 0, 50, 100 % */
}

static int g_chain_case[CHAIN_CTL_CEIL + 1];   /* the case holding each part; [0] unused */

static void test_chain_level_bound(int unused)
{
    (void)unused;
    double hi = -1e9, lo = 1e9, top = -1e9;
    uint32_t at_hi = 0, at_lo = 0, at_top = 0;
    for (uint32_t m = 1; m <= FX_ALL; m++) {
        const level_result *r = slot_of(g_chain_case[m]);
        const double h = db(r->rms_hi[SIG_I_KEYS]), l = db(r->rms_min[SIG_I_KEYS]);
        if (h > hi) {
            hi = h;
            at_hi = m;
        }
        if (l < lo) {
            lo = l;
            at_lo = m;
        }
        if (db(r->rms_max) > top) {
            top = db(r->rms_max);
            at_top = m;
        }
        CHECK(h <= CHAIN_HI_DB && l >= CHAIN_LO_DB, "chain mask %u: keys %+.3f..%+.3f dB vs dry x "
              "makeup, bounds %+.2f..%+.2f", (unsigned)m, l, h, CHAIN_LO_DB, CHAIN_HI_DB);
        CHECK(db(r->rms_max) <= CEIL_DB, "chain mask %u: output RMS %+.3f dB over dry x makeup "
              "(depth %.2f, %s), ceiling %+.1f", (unsigned)m, db(r->rms_max),
              (double)r->at_depth, LEVEL_SIGNAL_LABEL[r->at_signal], CEIL_DB);
    }
    const double chot = db(((const level_result *)slot_of(g_chain_case[CHAIN_CTL_HOT]))->rms_hi[SIG_I_KEYS]);
    const double ccold = db(((const level_result *)slot_of(g_chain_case[CHAIN_CTL_COLD]))->rms_min[SIG_I_KEYS]);
    const double cceil = db(((const level_result *)slot_of(g_chain_case[CHAIN_CTL_CEIL]))->rms_max);
    CHECK(chot > CHAIN_HI_DB, "chain: control (curves hot) passed: keys %+.3f dB", chot);
    CHECK(ccold < CHAIN_LO_DB, "chain: control (curves cold) passed: keys %+.3f dB", ccold);
    CHECK(cceil > CEIL_DB, "chain: control (phaser feedback 0.95) passed the ceiling: %+.3f dB",
          cceil);
    if (g_verbose) {
        printf("  chain    keys vs makeup over all 127 masks: %+.3f dB (mask %u) .. %+.3f dB "
               "(mask %u); any signal at most %+.3f dB (mask %u); controls: hot %+.3f, cold "
               "%+.3f, ceiling %+.3f dB\n", lo, (unsigned)at_lo, hi, (unsigned)at_hi, top,
               (unsigned)at_top, chot, ccold, cceil);
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
    CHECK(same_output(a, b), "all seven: levels 1.0 differ from no levels");
    /* Out of range and NaN are clamped: 7 is 1, -1 and NaN are 0. */
    float wild[FX_MAX_EFFECTS], tame[FX_MAX_EFFECTS];
    for (int k = 0; k < FX_MAX_EFFECTS; k++) {
        wild[k] = k % 3 == 0 ? 7.0f : k % 3 == 1 ? -1.0f : NAN;
        tame[k] = k % 3 == 0 ? 1.0f : 0.0f;
    }
    levels_run(FX_ALL, 0.6f, wild, LV_PERIODS, LV_KEEP, a);
    levels_run(FX_ALL, 0.6f, tame, LV_PERIODS, LV_KEEP, b);
    CHECK(same_output(a, b), "all seven: levels 7 / -1 / NaN are not 1 / 0 / 0");
    if (g_verbose) {
        printf("  levels   d = shared x level, bit for bit for all seven; controls (0.5 vs 1.0) "
               "differ: %s\n", controls_differ ? "yes" : "NO");
    }
}

/* A stage at level 0 is not in the chain: all seven with drive and chorus at
 * level 0 is, bit for bit, the other five alone. Control: level 1/127 must
 * differ. */
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
    levels_run(FX_ALL & ~(BIT_CHORUS | BIT_DRIVE), 0.8f, NULL, LV_PERIODS, LV_KEEP, b);
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
    for (int p = 0; p < 100; p++) {   /* 0.5 s of all seven at full levels first */
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
    CHECK(m == 0, "all seven, every level 0: %zu periods differ from the input", m);
    CHECK(c > 0, "control (every level 1/127) also identical: the comparison is blind");
    if (g_verbose) {
        printf("  levels   every level 0 after full: %zu differing periods; control (1/127) %zu\n",
               m, c);
    }
}

/* Level moves are smoothed like knob moves: steps between segments at a fixed
 * depth, for each effect alone and for all seven at once. Control: the same
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
        const char *label = chain ? "all seven" : FX_LABEL[k];
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

/* Unity at any levels, argued down to the cases nothing else covers.
 * A level only sets a stage's depth (d_k = shared x level_k, bit for bit:
 * test_level_is_a_depth_product), so:
 *  - one stage on, any level: that effect at some depth, which its own sweep
 *    (test_pack_level_bound, 21 depths) already holds to unity;
 *  - a level 0 stage is not in the chain (test_level_zero_is_off), so "one at
 *    1, the rest 0" is the one-stage case again;
 *  - every level equal (all 1, all 0.5): the chain at one shared depth, which
 *    test_chain_level_bound sweeps over all 127 masks.
 * What is left is unequal levels inside a chain of two or more. Two level
 * vectors, each the other's complement, so every pair of stages whose levels
 * differ is heard both loud-then-quiet and quiet-then-loud, on every multi-
 * stage mask, at full depth: the 26 masks of the first five effects on loud
 * noise and the keys, the 94 with the rotary or the auto-wah on the keys
 * alone, in shorter windows. The bounds are the chain's (CHAIN_LO_DB..
 * CHAIN_HI_DB on the keys, CEIL_DB on the noise), against the makeup of the
 * loudest stage's level. Controls: the curves 0.5 dB hot on phaser, flanger
 * and rotary, and 0.5 dB cold on chorus, drive, tremolo and flanger (the
 * chain controls' masks), both vectors. A full level grid (127 masks x N^7)
 * is neither needed nor affordable: the self-test runs on every CI push. */
#define LV_VECTORS 2
static const float LEVEL_VECTORS[LV_VECTORS][N_FX] = {
    /* by id: chorus, drive, phaser, tremolo, flanger, rotary, auto-wah */
    { 1.0f, 0.3f, 1.0f, 0.3f, 1.0f, 0.3f, 1.0f },
    { 0.3f, 1.0f, 0.3f, 1.0f, 0.3f, 1.0f, 0.3f },
};
#define LEVEL_VECTOR_SIGNALS ((1u << 0) | MASK_KEYS)   /* noise.5 and keys */
#define LEVEL_VECTOR_SIGNALS_NEW MASK_KEYS

typedef struct {
    float hi, lo, top;          /* keys max and min, any signal max: out / (in x G) */
    uint32_t at_hi, at_lo;
    int v_hi, v_lo;
} vector_worst;

static vector_worst vector_init(void)
{
    return (vector_worst){ 0.0f, 1e9f, 0.0f, 0u, 0u, -1, -1 };
}

static void vector_fold(vector_worst *w, const vector_worst *p)
{
    if (p->hi > w->hi) {
        w->hi = p->hi;
        w->at_hi = p->at_hi;
        w->v_hi = p->v_hi;
    }
    if (p->lo < w->lo) {
        w->lo = p->lo;
        w->at_lo = p->at_lo;
        w->v_lo = p->v_lo;
    }
    w->top = fmaxf(w->top, p->top);
}

/* Masks mask_from..mask_to of two or more stages, level vectors vi_from..
 * vi_to, in that order. */
static vector_worst levels_vector_worst(uint32_t mask_from, uint32_t mask_to, int vi_from,
                                        int vi_to)
{
    vector_worst w = vector_init();
    float lv[FX_MAX_EFFECTS];
    const float *saved = g_levels;
    g_levels = lv;
    for (int vi = vi_from; vi <= vi_to; vi++) {
        for (int k = 0; k < FX_MAX_EFFECTS; k++) {
            lv[k] = k < N_FX ? LEVEL_VECTORS[vi][k] : 1.0f;
        }
        for (uint32_t m = mask_from; m <= mask_to; m++) {
            if (popcount(m) < 2) {
                continue;   /* one stage: its own depth sweep covers it (above) */
            }
            const unsigned sigs = (m & NEW_FX) ? LEVEL_VECTOR_SIGNALS_NEW : LEVEL_VECTOR_SIGNALS;
            for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
                if (!(sigs & (1u << sgi))) {
                    continue;
                }
                float rr, pr;
                level_at(FX_EFFECTS, m, sgi, 1.0f, &rr, &pr);
                rr /= makeup_gain(makeup_m(m, 1.0f));   /* the loudest stage's level */
                const vector_worst p = { sgi == SIG_I_KEYS ? rr : 0.0f,
                                         sgi == SIG_I_KEYS ? rr : 1e9f, rr, m, m, vi, vi };
                vector_fold(&w, &p);
            }
        }
    }
    g_levels = saved;
    return w;
}

/* Part `arg` = vector * 128 + mask, in a worker; LV_CTL_HOT and LV_CTL_COLD
 * are the controls. */
#define LV_MASKS ((int)FX_ALL + 1)
#define LV_CTL_HOT (LV_VECTORS * LV_MASKS)
#define LV_CTL_COLD (LV_CTL_HOT + 1)
static void levels_unity_part(int arg, void *slot)
{
    vector_worst *out = slot;
    if (arg == LV_CTL_HOT || arg == LV_CTL_COLD) {
        const uint32_t m = arg == LV_CTL_HOT ? CHAIN_HOT_MASK : CHAIN_COLD_MASK;
        fx_test_level_hot = arg == LV_CTL_HOT;
        fx_test_level_cold = arg == LV_CTL_COLD;
        g_short_windows = 1;
        *out = levels_vector_worst(m, m, 0, LV_VECTORS - 1);
        g_short_windows = 0;
        fx_test_level_hot = 0;
        fx_test_level_cold = 0;
        return;
    }
    const uint32_t m = (uint32_t)(arg % LV_MASKS);
    g_short_windows = (m & NEW_FX) != 0;
    *out = levels_vector_worst(m, m, arg / LV_MASKS, arg / LV_MASKS);
    g_short_windows = 0;
}

static int g_lv_case[LV_CTL_COLD + 1];   /* -1: no such part (one stage) */

static void test_levels_unity(int unused)
{
    (void)unused;
    vector_worst w = vector_init();
    for (int arg = 0; arg < LV_CTL_HOT; arg++) {
        if (g_lv_case[arg] >= 0) {
            vector_fold(&w, slot_of(g_lv_case[arg]));
        }
    }
    CHECK(db(w.hi) <= CHAIN_HI_DB, "levels: keys %+.3f dB over dry x makeup (mask %u, level "
          "vector %d), bound %+.2f", db(w.hi), (unsigned)w.at_hi, w.v_hi, CHAIN_HI_DB);
    CHECK(db(w.lo) >= CHAIN_LO_DB, "levels: keys %+.3f dB under dry x makeup (mask %u, level "
          "vector %d), bound %+.2f", db(w.lo), (unsigned)w.at_lo, w.v_lo, CHAIN_LO_DB);
    CHECK(db(w.top) <= CEIL_DB, "levels: output RMS %+.3f dB over dry x makeup, ceiling %+.1f",
          db(w.top), CEIL_DB);
    const double chot = db(((const vector_worst *)slot_of(g_lv_case[LV_CTL_HOT]))->hi);
    const double ccold = db(((const vector_worst *)slot_of(g_lv_case[LV_CTL_COLD]))->lo);
    CHECK(chot > CHAIN_HI_DB, "levels: control (curves hot) passed: keys %+.3f dB", chot);
    CHECK(ccold < CHAIN_LO_DB, "levels: control (curves cold) passed: keys %+.3f dB", ccold);
    if (g_verbose) {
        printf("  levels   keys vs makeup over 120 multi-stage masks x %d complementary level "
               "vectors: %+.3f dB (mask %u, vector %d) .. %+.3f dB (mask %u, vector %d); any "
               "signal at most %+.3f dB; controls: hot %+.3f, cold %+.3f dB\n", LV_VECTORS,
               db(w.lo), (unsigned)w.at_lo, w.v_lo, db(w.hi), (unsigned)w.at_hi, w.v_hi,
               db(w.top), chot, ccold);
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
 * each effect alone and all seven: at least +0.7 dB over the same chain
 * without it, and not more than FX_MAKEUP_DB + 0.1 dB (seven stages each
 * adding it would be +7). At half depth: within 0.1 dB of half of it (a
 * makeup that ignored depth would be the whole of it). The upper bounds
 * elsewhere hold the output to the makeup; this is the bound under it, which
 * a makeup that silently did nothing would fail. Control: the makeup off. */
#define MAKEUP_FLOOR_DB 0.7
static void test_makeup_is_applied(void)
{
    static const uint32_t masks[N_FX + 1] = { BIT(0), BIT(1), BIT(2), BIT(3), BIT(4), BIT(5),
                                               BIT(6), FX_ALL };
    for (int j = 0; j <= N_FX; j++) {
        const char *label = j == N_FX ? "all seven" : FX_LABEL[j];
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
        printf("  all seven makeup control (makeup off): full %+.3f dB\n", c);
    }
    CHECK(c < MAKEUP_FLOOR_DB, "makeup: control (makeup off) met the floor: %+.3f dB", c);
}

/* The makeup moves smoothly. The generic click criterion (largest step vs
 * the settled signal's) is too coarse for a 1 dB gain step on a resonant
 * effect: on the phaser and on all seven, a makeup that jumped to its target
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
    /* Effect j alone, or (j == N_FX) all seven with the alternating levels, so
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
            if (chain) {
                for (int k = 0; k < N_FX; k++) {
                    if (lv[k] == 1.0f) {
                        lv[k] = 0.2f;              /* every level-1 stage steps down */
                    }
                }
            } else {
                lv[j] = 0.2f;                      /* a level step down */
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
        const char *label = j == N_FX ? "all seven" : FX_LABEL[j];
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

/* ========================================================================= */
/* The bass bypass (#173)                                                    */
/* ========================================================================= */

/* A test-only effect whose wet signal is its input: the chain live and
 * transparent, so what the split does is all there is to see. */
static int thru_init(void *s, float r) { (void)s; (void)r; return 0; }
static void thru_process(void *s, const float *const in[FX_CHANNELS],
                         float *const wet[FX_CHANNELS], const float *amount, uint32_t n)
{
    (void)s;
    (void)amount;
    for (uint32_t i = 0; i < n; i++) {
        float l = in[0][i];
        float r = in[1][i];
        wet[0][i] = l;
        wet[1][i] = r;
    }
}
static const fx_effect_desc THRU = { "thru", 1.0f, 1.0f, { 0.0f, 0.0f, 0.0f, 0.0f }, 1,
                                     thru_init, thru_process, NULL, NULL };

#define BASS_FLAT_TOL_DB 0.01
#define BASS_N_FREQS 31   /* third octaves, 20 Hz .. 20 kHz */
static const float BASS_XOVERS[3] = { 80.0f, 120.0f, 300.0f };

/* Split and recombined with the chain bypassed: depth 0 with the split forced
 * on (fx_test_bass_split_at_depth0), a mono sine at `hz`. Returns out/in RMS
 * in dB over a whole number of cycles after 1.2 s; `diff` is RMS(out - in) /
 * RMS(in), how far the waveform moved (the allpass's phase). */
static double bass_flat_db(float xover, float hz, double *diff)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, &THRU, 1, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    /* 1.2 s: the split fades in parked at 10 Hz and the corner then glides
     * up (100 ms one-pole); flatness is a property of the corner at rest. */
    const uint32_t settle = (uint32_t)(1.2f * RATE);
    const uint32_t cycles = (uint32_t)ceil(0.5 * hz) < 4u ? 4u : (uint32_t)ceil(0.5 * hz);
    const uint32_t measure = (uint32_t)lround((double)cycles * RATE / hz);
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    double si = 0.0, so = 0.0, sd = 0.0;
    uint32_t t = 0;
    g_bass_hz = xover;
    fx_test_bass_split_at_depth0 = 1;
    while (t < settle + measure) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = r[i] = 0.5f * (float)sin(2.0 * M_PI * hz * (double)(t + i) / RATE);
        }
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0), 0.0f);
        for (uint32_t i = 0; i < PERIOD && t + i < settle + measure; i++) {
            if (t + i >= settle) {
                si += (double)l[i] * l[i];
                so += (double)ol[i] * ol[i];
                sd += (double)(ol[i] - l[i]) * (ol[i] - l[i]);
            }
        }
        t += PERIOD;
    }
    fx_test_bass_split_at_depth0 = 0;
    g_bass_hz = 0.0f;
    fx_engine_free(&e);
    *diff = sqrt(sd / si);
    return 10.0 * log10(so / si);
}

/* The worst |out/in| in dB over 20 Hz .. 20 kHz and the crossover itself,
 * and the waveform change at the crossover. */
static double bass_flat_worst(float xover, double *diff_at_xover)
{
    double worst = 0.0, diff = 0.0;
    for (int j = 0; j <= BASS_N_FREQS; j++) {
        const float hz = j == BASS_N_FREQS ? xover : 20.0f * powf(1000.0f, (float)j / (BASS_N_FREQS - 1));
        const double d = bass_flat_db(xover, hz, &diff);
        worst = fmax(worst, fabs(d));
    }
    *diff_at_xover = diff;
    return worst;
}

/* LR4's bands sum flat: dry against split-and-recombined with the chain
 * bypassed, within 0.01 dB from 20 Hz to 20 kHz at 80, 120 and 300 Hz. And the
 * split really ran: at the crossover the sum is the input turned 180 degrees
 * (RMS(out - in) ~ 2 x the input's). Control: an LR2-style split (one
 * Butterworth section per band), whose sum has a notch at the corner. */
static void test_bass_flat_sum(void)
{
    for (int x = 0; x < 3; x++) {
        double diff = 0.0;
        const double worst = bass_flat_worst(BASS_XOVERS[x], &diff);
        fx_test_bass_lr2 = 1;
        double cdiff = 0.0;
        const double ctl = bass_flat_worst(BASS_XOVERS[x], &cdiff);
        fx_test_bass_lr2 = 0;
        if (g_verbose) {
            printf("  bass %3.0f Hz  flat sum: worst %.5f dB over 20 Hz-20 kHz; waveform moved "
                   "%.3f x at the crossover; control (LR2) worst %.2f dB\n",
                   (double)BASS_XOVERS[x], worst, diff, ctl);
        }
        CHECK(worst <= BASS_FLAT_TOL_DB, "bass %.0f Hz: split and recombined is %.5f dB off "
              "dry, wanted <= %.2f", (double)BASS_XOVERS[x], worst, BASS_FLAT_TOL_DB);
        CHECK(diff > 1.5, "bass %.0f Hz: the waveform moved only %.3f x at the crossover: the "
              "split did not run", (double)BASS_XOVERS[x], diff);
        CHECK(ctl > 1.0, "bass %.0f Hz: control (LR2) passed too: %.3f dB",
              (double)BASS_XOVERS[x], ctl);
    }
}

/* Depth 0 skips the split: with a crossover set, depth 0 (an effect ticked)
 * and no effect ticked (any depth) are the input bit for bit, separate
 * buffers and in place, from the start and after a ramp down. And Off is the
 * chain bit for bit: an engine that had a crossover while bypassed, then Off,
 * plays the chain exactly as one that never had one. Control: the split run
 * at depth 0. */
static size_t bass_identity_mismatches(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD], keep_l[PERIOD], keep_r[PERIOD];
    size_t bad = 0;
    g_bass_hz = 120.0f;
    for (int p = 0; p < 900; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.5f * noise();
            r[i] = 0.5f * noise();
        }
        /* 0-299: depth 0, chorus ticked. 300-499: no effect, depth 0.7.
         * 500-599: chorus at 1 (live, not checked). 600-899: depth 0 again,
         * checked from 800 (the depth's ramp and the split's fade land). */
        const uint32_t mask = (p >= 300 && p < 500) ? 0u : BIT(0);
        const float depth = (p >= 300 && p < 500) ? 0.7f : (p >= 500 && p < 600) ? 1.0f : 0.0f;
        /* The swap at 300 (chorus fading out while the depth rises to 0.7)
         * is live for a few periods, with or without the split: skipped. */
        const int checked = p < 300 || (p >= 330 && p < 500) || p >= 800;
        memcpy(keep_l, l, sizeof(l));
        memcpy(keep_r, r, sizeof(r));
        if (p % 2 == 0) {
            run_period(&e, l, r, ol, or_, PERIOD, mask, depth);
            bad += checked && (memcmp(l, ol, sizeof(l)) != 0 || memcmp(r, or_, sizeof(r)) != 0);
        } else {
            run_period(&e, l, r, l, r, PERIOD, mask, depth);
            bad += checked && (memcmp(keep_l, l, sizeof(l)) != 0 || memcmp(keep_r, r, sizeof(r)) != 0);
        }
    }
    g_bass_hz = 0.0f;
    fx_engine_free(&e);
    return bad;
}

static size_t bass_off_mismatches(void)
{
    fx_engine a, b;
    CHECK(fx_engine_init(&a, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    CHECK(fx_engine_init(&b, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], al[PERIOD], ar[PERIOD], bl[PERIOD], br[PERIOD];
    size_t bad = 0;
    const uint32_t mask = BIT(0) | BIT(2) | BIT(1);
    for (int p = 0; p < 400; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 0.4f * noise();
            r[i] = 0.4f * noise();
        }
        /* a: a crossover while bypassed (its filters warm), then Off, then
         * live. b: never a crossover. */
        g_bass_hz = p < 100 ? 120.0f : 0.0f;
        run_period(&a, l, r, al, ar, PERIOD, mask, p < 150 ? 0.0f : 0.8f);
        g_bass_hz = 0.0f;
        run_period(&b, l, r, bl, br, PERIOD, mask, p < 150 ? 0.0f : 0.8f);
        bad += memcmp(al, bl, sizeof(al)) != 0 || memcmp(ar, br, sizeof(ar)) != 0;
    }
    fx_engine_free(&a);
    fx_engine_free(&b);
    return bad;
}

static void test_bass_bit_identity(void)
{
    const size_t bad = bass_identity_mismatches();
    const size_t off = bass_off_mismatches();
    fx_test_bass_split_at_depth0 = 1;
    const size_t ctl = bass_identity_mismatches();
    fx_test_bass_split_at_depth0 = 0;
    if (g_verbose) {
        printf("  bass bypass identity: %zu of 670 periods differ (depth 0 / no effect, 120 Hz "
               "set); Off vs never set: %zu of 400; control (split at depth 0): %zu\n",
               bad, off, ctl);
    }
    CHECK(bad == 0, "bass 120 Hz: %zu bypass periods differ from their input", bad);
    CHECK(off == 0, "bass Off: %zu periods differ from an engine that never had a crossover",
          off);
    CHECK(ctl > 0, "bass: control (split at depth 0) was bit-identical too: the check cannot "
                   "see the split");
}

/* No click when the crossover moves, comes in or goes out, or when the chain
 * leaves or re-enters bypass under it. The chain is live and transparent (the
 * thru effect), the signal 70 Hz + 190 Hz either side of the crossovers. The
 * generic criterion: the largest sample step anywhere against the settled
 * signal's. Control: the fade and the glide instant. */
#define BASS_SEGS 11
static steps bass_steps(void)
{
    static const float hz[BASS_SEGS] = { 0, 120, 80, 300, 0, 300, 120, 120, 120, 200, 0 };
    static const float depth[BASS_SEGS] = { 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1 };
    fx_engine e;
    CHECK(fx_engine_init(&e, &THRU, 1, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    double t = 0.0;
    float prev = 0.0f;
    int have_prev = 0;
    steps st = { 0.0f, 0.0f };
    for (int seg = 0; seg < BASS_SEGS; seg++) {
        g_bass_hz = hz[seg];
        for (int p = 0; p < 190; p++) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                const double ts = (t + i) / RATE;
                l[i] = r[i] = (float)(0.35 * sin(2.0 * M_PI * 70.0 * ts) +
                                      0.35 * sin(2.0 * M_PI * 190.0 * ts + 0.3));
            }
            t += PERIOD;
            run_period(&e, l, r, ol, or_, PERIOD, BIT(0), depth[seg]);
            for (uint32_t i = 0; i < PERIOD; i++) {
                if (have_prev) {
                    const float step = fabsf(ol[i] - prev);
                    st.worst = fmaxf(st.worst, step);
                    if (p >= 60) {
                        st.settled = fmaxf(st.settled, step);
                    }
                }
                prev = ol[i];
                have_prev = 1;
            }
        }
    }
    g_bass_hz = 0.0f;
    fx_engine_free(&e);
    return st;
}

static void test_bass_no_click(void)
{
    const steps s = bass_steps();
    fx_test_bass_unsmoothed = 1;
    const steps c = bass_steps();
    fx_test_bass_unsmoothed = 0;
    if (g_verbose) {
        printf("  bass moves: worst step %.5f vs settled %.5f; control (instant) %.5f vs %.5f\n",
               (double)s.worst, (double)s.settled, (double)c.worst, (double)c.settled);
    }
    CHECK(click_free(s), "bass moves: worst %.5f vs settled %.5f", (double)s.worst,
          (double)s.settled);
    CHECK(!click_free(c), "bass moves: control (instant) passed too: worst %.5f vs settled %.5f",
          (double)c.worst, (double)c.settled);
}

/* Below the split the lows stay dry and mono through the modulation effects.
 * A 50 Hz tone through chorus + phaser + flanger at full, crossover 120 Hz:
 *   mono: L and R a quarter-cycle apart; the output's side (L - R) against
 *         the input's. The split folds it to mono: at most -24 dB. Controls:
 *         Off (the chorus keeps it stereo) and the lows left per channel.
 *   dry:  L = R; the level over 0.1 s windows across 10 s (two flanger
 *         sweeps) moves at most 1 dB (0.50 measured: the high band's -30 dB
 *         leak of 50 Hz, through the sweeps). Control: Off, where the sweeps
 *         move it 15 dB. */
#define BASS_SIDE_MAX_DB (-24.0)
#define BASS_WANDER_MAX_DB 1.0
static void bass_lows(float xover, double *side_db, double *wander_db)
{
    const uint32_t mask = BIT(0) | BIT(2) | BIT(FX_I_FLANGER);
    for (int pass = 0; pass < 2; pass++) {
        fx_engine e;
        CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0,
              "init");
        float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
        const int settle = (int)(0.5f * RATE / PERIOD);
        const int measure = (int)((pass == 0 ? 2.0f : 10.0f) * RATE / PERIOD);
        const int win = (int)(0.1f * RATE / PERIOD);
        double si = 0.0, so = 0.0, w = 0.0, wmin = 1e30, wmax = 0.0;
        g_bass_hz = xover;
        for (int p = 0; p < settle + measure; p++) {
            for (uint32_t i = 0; i < PERIOD; i++) {
                const double ph = 2.0 * M_PI * 50.0 * (double)(p * PERIOD + i) / RATE;
                l[i] = 0.4f * (float)sin(ph);
                r[i] = 0.4f * (float)sin(pass == 0 ? ph + 0.5 * M_PI : ph);
            }
            run_period(&e, l, r, ol, or_, PERIOD, mask, 1.0f);
            if (p < settle) {
                continue;
            }
            for (uint32_t i = 0; i < PERIOD; i++) {
                const double sd_in = 0.5 * (l[i] - r[i]), sd_out = 0.5 * (ol[i] - or_[i]);
                si += sd_in * sd_in;
                so += sd_out * sd_out;
                w += (double)ol[i] * ol[i] + (double)or_[i] * or_[i];
            }
            if ((p - settle + 1) % win == 0) {
                wmin = fmin(wmin, w);
                wmax = fmax(wmax, w);
                w = 0.0;
            }
        }
        g_bass_hz = 0.0f;
        fx_engine_free(&e);
        if (pass == 0) {
            *side_db = 10.0 * log10(so / si);
        } else {
            *wander_db = 10.0 * log10(wmax / wmin);
        }
    }
}

static void test_bass_lows_dry_and_mono(void)
{
    double side, wander, c_side, c_wander, m_side, m_wander;
    bass_lows(120.0f, &side, &wander);
    bass_lows(0.0f, &c_side, &c_wander);
    fx_test_bass_stereo = 1;
    bass_lows(120.0f, &m_side, &m_wander);
    fx_test_bass_stereo = 0;
    if (g_verbose) {
        printf("  bass 120 Hz, 50 Hz through chorus+phaser+flanger: side %+.1f dB re the "
               "input's, level wander %.2f dB; Off: side %+.1f dB, wander %.2f dB; lows "
               "per channel: side %+.1f dB\n", side, wander, c_side, c_wander, m_side);
    }
    CHECK(side <= BASS_SIDE_MAX_DB, "bass: the lows are not mono: side %+.1f dB, wanted <= "
          "%+.0f", side, BASS_SIDE_MAX_DB);
    CHECK(wander <= BASS_WANDER_MAX_DB, "bass: the lows wander %.2f dB, wanted <= %.1f",
          wander, BASS_WANDER_MAX_DB);
    CHECK(c_side > BASS_SIDE_MAX_DB && c_wander > BASS_WANDER_MAX_DB, "bass: control (Off) "
          "passed too: side %+.1f dB, wander %.2f dB", c_side, c_wander);
    CHECK(m_side > BASS_SIDE_MAX_DB, "bass: control (lows per channel) passed the mono check: "
          "%+.1f dB", m_side);
}

/* The split fades in and out without a dip (review of #185). Before, b faded
 * with the corner already at the target: mid-fade the dry lows and LR4's
 * (half a turn behind at the corner) cancelled, -13 dB for ~30 ms. A mono
 * tone at 0.7, 1 and 1.4 x the corner (120 and 300 Hz), the thru effect at
 * full: Off 0.4 s, the corner set 0.8 s, Off 0.8 s. The envelope is the RMS
 * over one tone period, every quarter period; its minimum after the first
 * 0.3 s, against the settled level just before the split comes in, must be
 * at least BASS_FADE_MIN_DB. Control: the pre-review fade (no parking). */
#define BASS_FADE_MIN_DB (-1.0)
static double bass_fade_min_db(float xover, float hz)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, &THRU, 1, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    const int seg[3] = { (int)(0.4f * RATE / PERIOD), (int)(0.8f * RATE / PERIOD),
                         (int)(0.8f * RATE / PERIOD) };
    const int total = seg[0] + seg[1] + seg[2];
    float *out = malloc(sizeof(float) * (size_t)total * PERIOD);
    CHECK(out != NULL, "alloc");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    for (int p = 0; p < total; p++) {
        g_bass_hz = (p >= seg[0] && p < seg[0] + seg[1]) ? xover : 0.0f;
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = r[i] = 0.4f * (float)sin(2.0 * M_PI * hz * (double)(p * PERIOD + i) / RATE);
        }
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0), 1.0f);
        memcpy(out + (size_t)p * PERIOD, ol, sizeof(ol));
    }
    g_bass_hz = 0.0f;
    fx_engine_free(&e);
    const int win = (int)lround(RATE / hz), hop = win / 4 > 0 ? win / 4 : 1;
    const int start = (int)(0.3f * RATE), split_at = seg[0] * (int)PERIOD;
    double ref = 0.0, worst = 1e30;
    for (int a = start; a + win <= total * (int)PERIOD; a += hop) {
        double acc = 0.0;
        for (int i = a; i < a + win; i++) {
            acc += (double)out[i] * out[i];
        }
        if (a + win <= split_at) {
            ref = acc;
        } else {
            worst = fmin(worst, acc);
        }
    }
    free(out);
    return 10.0 * log10(worst / ref + 1e-30);
}

static void test_bass_fade_no_dip(void)
{
    static const float xo[2] = { 120.0f, 300.0f };
    static const float mul[3] = { 0.7f, 1.0f, 1.4f };
    double worst = 0.0, cworst = 0.0;
    for (int x = 0; x < 2; x++) {
        for (int m = 0; m < 3; m++) {
            double d = bass_fade_min_db(xo[x], xo[x] * mul[m]);
            fx_test_bass_no_park = 1;
            double c = bass_fade_min_db(xo[x], xo[x] * mul[m]);
            fx_test_bass_no_park = 0;
            if (g_verbose) {
                printf("  bass fade in/out at %3.0f Hz, tone %5.1f Hz: envelope min %+.2f dB; "
                       "control (no parking) %+.2f dB; limit %+.1f\n", (double)xo[x],
                       (double)(xo[x] * mul[m]), d, c, BASS_FADE_MIN_DB);
            }
            worst = fmin(worst, d);
            cworst = fmin(cworst, c);
        }
    }
    CHECK(worst >= BASS_FADE_MIN_DB, "bass: the lows dip %+.2f dB while the split fades", worst);
    CHECK(cworst < BASS_FADE_MIN_DB, "bass: control (no parking) dipped only %+.2f dB", cworst);
}

/* The drive is inside the bypass (FX_BASS_DRIVE_INSIDE): a 60 Hz tone at
 * full drive, crossover 120 Hz, comes out clean. Distortion = what is left
 * after the 60 Hz component (a DFT over whole cycles), against the output:
 * inside, under -20 dB; with the drive moved outside the split, the same
 * reading over -20 dB (the check sees where the drive is). */
#define BASS_DRIVE_THD_DB (-20.0)
static double bass_drive_thd_db(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    const int settle = 100, measure = 375;   /* 2 s: 120 cycles exactly */
    double re = 0.0, im = 0.0, tot = 0.0;
    g_bass_hz = 120.0f;
    for (int p = 0; p < settle + measure; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = r[i] = 0.5f * (float)sin(2.0 * M_PI * 60.0 * (double)(p * PERIOD + i) / RATE);
        }
        run_period(&e, l, r, ol, or_, PERIOD, BIT(1), 1.0f);
        if (p < settle) {
            continue;
        }
        for (uint32_t i = 0; i < PERIOD; i++) {
            const double ph = 2.0 * M_PI * 60.0 * (double)((p - settle) * PERIOD + i) / RATE;
            re += ol[i] * cos(ph);
            im += ol[i] * sin(ph);
            tot += (double)ol[i] * ol[i];
        }
    }
    g_bass_hz = 0.0f;
    fx_engine_free(&e);
    const double n = (double)measure * PERIOD;
    const double fund = 2.0 * (re * re + im * im) / n;   /* sum of squares of the 60 Hz part */
    return 10.0 * log10(fmax(tot - fund, 1e-30) / tot);
}

static void test_bass_drive_inside(void)
{
    const double inside = bass_drive_thd_db();
    fx_test_bass_drive_outside = 1;
    const double outside = bass_drive_thd_db();
    fx_test_bass_drive_outside = 0;
    if (g_verbose) {
        printf("  bass 120 Hz, 60 Hz at full drive: distortion %+.1f dB (drive inside, "
               "FX_BASS_DRIVE_INSIDE=%d); drive outside %+.1f dB\n", inside,
               FX_BASS_DRIVE_INSIDE, outside);
    }
    if (FX_BASS_DRIVE_INSIDE) {
        CHECK(inside <= BASS_DRIVE_THD_DB, "bass: the drive reaches the lows: %+.1f dB", inside);
    }
    CHECK(outside > BASS_DRIVE_THD_DB, "bass: control (drive outside) came out clean too: "
          "%+.1f dB", outside);
}

/* The level still holds with the split. Each effect alone on the keys, at
 * four knob positions and crossovers 120, 175 (between grid points) and
 * 300 Hz: within UNITY_TOL_DB of G(m) x the input, the bound it has without
 * the split. And no signal past CEIL_DB at full depth. Controls: the makeup
 * off (chorus at full: -1 dB is past the lower bound) and, at 300 Hz, the
 * split's level correction off (the rotary at full: -1.9 dB). */
static const float BASS_LEVEL_XOVERS[3] = { 120.0f, 175.0f, 300.0f };
static void test_bass_level(int xi)
{
    const float xover = BASS_LEVEL_XOVERS[xi];
    double lo = 1e9, hi = -1e9, ceil_db = -1e9;
    g_bass_hz = xover;
    for (int k = 0; k < N_FX; k++) {
        for (int j = 1; j <= 4; j++) {
            const float depth = 0.25f * (float)j;
            float rr, pr;
            level_at(FX_EFFECTS, BIT(k), SIG_I_KEYS, depth, &rr, &pr);
            const double d = db(rr / makeup_gain(makeup_m(BIT(k), depth)));
            lo = fmin(lo, d);
            hi = fmax(hi, d);
        }
        for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
            float rr, pr;
            level_at(FX_EFFECTS, BIT(k), sgi, 1.0f, &rr, &pr);
            ceil_db = fmax(ceil_db, db(rr / makeup_gain(makeup_m(BIT(k), 1.0f))));
        }
    }
    float rr, pr;
    fx_test_no_makeup = 1;
    level_at(FX_EFFECTS, BIT(0), SIG_I_KEYS, 1.0f, &rr, &pr);
    fx_test_no_makeup = 0;
    const double ctl = db(rr / makeup_gain(1.0f));
    double ctl_corr = 0.0;
    if (xi == 2) {
        fx_test_bass_no_corr = 1;
        level_at(FX_EFFECTS, BIT(FX_I_ROTARY), SIG_I_KEYS, 1.0f, &rr, &pr);
        fx_test_bass_no_corr = 0;
        ctl_corr = db(rr / makeup_gain(1.0f));
    }
    g_bass_hz = 0.0f;
    if (g_verbose) {
        printf("  bass %3.0f Hz keys: each effect alone %+.3f..%+.3f dB re input x makeup, any "
               "signal at most %+.2f; controls: no makeup %+.3f", (double)xover, lo, hi,
               ceil_db, ctl);
        if (xi == 2) {
            printf(", no correction (rotary) %+.3f", ctl_corr);
        }
        printf("\n");
    }
    CHECK(lo >= -UNITY_TOL_DB && hi <= UNITY_TOL_DB, "bass %.0f Hz: an effect alone reads "
          "%+.3f..%+.3f dB on keys, wanted within %.1f", (double)xover, lo, hi, UNITY_TOL_DB);
    CHECK(ceil_db <= CEIL_DB, "bass %.0f Hz: a signal reads %+.2f dB, ceiling %+.1f",
          (double)xover, ceil_db, CEIL_DB);
    CHECK(ctl < -UNITY_TOL_DB, "bass %.0f Hz: control (no makeup) met the bound: %+.3f dB",
          (double)xover, ctl);
    CHECK(xi != 2 || ctl_corr < -UNITY_TOL_DB, "bass 300 Hz: control (no correction) met the "
          "bound: %+.3f dB", ctl_corr);
}

/* Every chain with the split, on the keys at half and full depth: at 120 and
 * 200 Hz within the chain bounds it has without the split (CHAIN_LO_DB ..
 * CHAIN_HI_DB). At 300 Hz the correction cannot hold every chain there (it is
 * one static gain for what is a decorrelation): measured -1.01..+1.96 dB, so
 * the bounds there are BASS_300_LO_DB..BASS_300_HI_DB. All seven, every
 * signal, at most BASS_300_CEIL_DB at 300 Hz and CEIL_DB below. At 300 Hz it
 * measured +3.8 (the 3 kHz tone); since the auto-wah's stronger top
 * (2026-10-15) +4.2, loud noise, which opens the wah onto its Q 4 top. */
#define BASS_300_LO_DB (-1.2)
#define BASS_300_HI_DB 2.15
#define BASS_300_CEIL_DB 4.5
#define BASS_CHAIN_XOVERS 3
static const float BASS_CHAIN_XOVER[BASS_CHAIN_XOVERS] = { 120.0f, 200.0f, 300.0f };
typedef struct {
    float lo[BASS_CHAIN_XOVERS], hi[BASS_CHAIN_XOVERS];
} bass_chain_result;
static int g_bass_chain_case[(int)FX_ALL + 1];

static void bass_chain_part(int m, void *slot)
{
    bass_chain_result *res = (bass_chain_result *)slot;
    /* One keys loop, as the chain sweep's newer masks: the masks are swept
     * for the split's correction meeting a chain, not for a slow sweep's
     * worst phase (chain_level_part has that). */
    g_short_windows = 1;
    for (int x = 0; x < BASS_CHAIN_XOVERS; x++) {
        res->lo[x] = 1e9f;
        res->hi[x] = -1e9f;
        g_bass_hz = BASS_CHAIN_XOVER[x];
        for (int j = 1; j <= 2; j++) {
            const float depth = 0.5f * (float)j;
            float rr, pr;
            level_at(FX_EFFECTS, (uint32_t)m, SIG_I_KEYS, depth, &rr, &pr);
            const float d = (float)db(rr / makeup_gain(makeup_m((uint32_t)m, depth)));
            res->lo[x] = fminf(res->lo[x], d);
            res->hi[x] = fmaxf(res->hi[x], d);
        }
        g_bass_hz = 0.0f;
    }
    if (m == (int)FX_ALL) {
        /* The ceiling, all seven at full, every signal, in the normal windows:
         * one mask, so they cost little, and the short ones read up to 2 dB
         * high here (a 2.5 s window catches part of the flanger's 5 s sweep;
         * 120 Hz read +3.65 short, +1.64 over whole sweeps). */
        g_short_windows = 0;
        float worst[BASS_CHAIN_XOVERS];
        for (int x = 0; x < BASS_CHAIN_XOVERS; x++) {
            worst[x] = -1e9f;
            g_bass_hz = BASS_CHAIN_XOVER[x];
            for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
                float rr, pr;
                level_at(FX_EFFECTS, FX_ALL, sgi, 1.0f, &rr, &pr);
                worst[x] = fmaxf(worst[x], (float)db(rr / makeup_gain(1.0f)));
            }
            g_bass_hz = 0.0f;
            const double ceil = x == 2 ? BASS_300_CEIL_DB : CEIL_DB;
            if (g_verbose) {
                printf("  bass %3.0f Hz all seven, any signal at full: %+.2f dB (ceiling %+.1f)\n",
                       (double)BASS_CHAIN_XOVER[x], (double)worst[x], ceil);
            }
            CHECK(worst[x] <= ceil, "bass %.0f Hz: all seven read %+.2f dB on a signal, "
                  "ceiling %+.1f", (double)BASS_CHAIN_XOVER[x], (double)worst[x], ceil);
        }
    }
}

static void test_bass_chain_level(int unused)
{
    (void)unused;
    float lo[BASS_CHAIN_XOVERS], hi[BASS_CHAIN_XOVERS];
    int lo_m[BASS_CHAIN_XOVERS] = { 0 }, hi_m[BASS_CHAIN_XOVERS] = { 0 };
    for (int x = 0; x < BASS_CHAIN_XOVERS; x++) {
        lo[x] = 1e9f;
        hi[x] = -1e9f;
    }
    for (int m = 1; m <= (int)FX_ALL; m++) {
        if (popcount((uint32_t)m) < 2) {
            continue;
        }
        const bass_chain_result *r = (const bass_chain_result *)slot_of(g_bass_chain_case[m]);
        for (int x = 0; x < BASS_CHAIN_XOVERS; x++) {
            if (r->lo[x] < lo[x]) {
                lo[x] = r->lo[x];
                lo_m[x] = m;
            }
            if (r->hi[x] > hi[x]) {
                hi[x] = r->hi[x];
                hi_m[x] = m;
            }
        }
    }
    for (int x = 0; x < BASS_CHAIN_XOVERS; x++) {
        const double blo = x == 2 ? BASS_300_LO_DB : CHAIN_LO_DB;
        const double bhi = x == 2 ? BASS_300_HI_DB : CHAIN_HI_DB;
        if (g_verbose) {
            printf("  bass %3.0f Hz chains of 2+ on keys: %+.3f (mask %d) .. %+.3f (mask %d) dB, "
                   "bounds %+.2f..%+.2f\n", (double)BASS_CHAIN_XOVER[x], (double)lo[x], lo_m[x],
                   (double)hi[x], hi_m[x], blo, bhi);
        }
        CHECK(lo[x] >= blo && hi[x] <= bhi, "bass %.0f Hz: a chain reads %+.3f (mask %d) .. "
              "%+.3f (mask %d) dB, wanted %+.2f..%+.2f", (double)BASS_CHAIN_XOVER[x],
              (double)lo[x], lo_m[x], (double)hi[x], hi_m[x], blo, bhi);
    }
}

/* Silence after audio is exact silence with the split running, and nothing
 * goes non-finite or subnormal on the way. */
static void test_bass_clean(void)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    size_t bad = 0, residue = 0;
    g_bass_hz = 80.0f;
    for (int p = 0; p < 900; p++) {
        const int quiet = p >= 400;
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = quiet ? 0.0f : 0.3f * noise();
            r[i] = quiet ? 0.0f : 0.3f * noise();
        }
        g_bass_hz = (p / 50) % 2 ? 80.0f : 250.0f;
        run_period(&e, l, r, ol, or_, PERIOD, BIT(0) | BIT(3), 0.7f);
        for (uint32_t i = 0; i < PERIOD; i++) {
            bad += !isfinite(ol[i]) || !isfinite(or_[i]);
            if (!quiet) {
                bad += fpclassify(ol[i]) == FP_SUBNORMAL || fpclassify(or_[i]) == FP_SUBNORMAL;
            } else if (p >= 600) {
                residue += (ol[i] != 0.0f) + (or_[i] != 0.0f);
            }
        }
    }
    g_bass_hz = 0.0f;
    fx_engine_free(&e);
    CHECK(bad == 0, "bass: %zu non-finite or subnormal samples", bad);
    CHECK(residue == 0, "bass: %zu non-zero samples 1 s after silence", residue);
}

/* fx-selftest --fit-bass K: effect K's bass_loss table, fitted. At each
 * crossover on the grid, the loss (dB) that brings the keys at full depth
 * with the split back to their level without it, by bisection; printed as
 * the C initializer fx_dsp.c carries. */
static void fit_bass(int k)
{
    static float tab[FX_BASS_GRID];
    float rr, pr;
    level_at(FX_EFFECTS, BIT(k), SIG_I_KEYS, 1.0f, &rr, &pr);
    const double off = db(rr);
    fx_test_bass_fit[k] = tab;
    for (int j = 1; j < FX_BASS_GRID; j++) {
        /* The table holds the level change the split makes (negative: a
         * loss); the gain given back is its negative. More negative, louder. */
        double lo = -8.0, hi = 4.0;
        for (int it = 0; it < 14; it++) {
            const double mid = 0.5 * (lo + hi);
            tab[j] = (float)mid;
            g_bass_hz = FX_BASS_GRID_HZ[j];
            level_at(FX_EFFECTS, BIT(k), SIG_I_KEYS, 1.0f, &rr, &pr);
            g_bass_hz = 0.0f;
            if (db(rr) < off) {
                hi = mid;
            } else {
                lo = mid;
            }
        }
        tab[j] = (float)(0.5 * (lo + hi));
    }
    fx_test_bass_fit[k] = NULL;
    printf("%s:", FX_LABEL[k]);
    for (int j = 0; j < FX_BASS_GRID; j++) {
        printf(" %.3ff,", (double)tab[j]);
    }
    printf("   (keys off %+.3f dB)\n", off);
}

static void print_parse(const char *text)
{
    uint32_t mask = 0;
    float depth = 0.0f;
    float levels[FX_MAX_EFFECTS];
    float hz = 0.0f;
    if (!fx_parse_control(text, &mask, &depth, levels)) {
        if (fx_parse_bass(text, &hz)) {
            printf("bass %.6f\n", (double)hz);
        } else {
            printf("reject\n");
        }
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

#define MAX_CASES 1024
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
CASE0(test_tremolo_smoothness)
CASE0(test_rotary_pitch)
CASE0(test_rotary_inertia)
CASE0(test_rotary_power)
CASE0(test_wah_onset)
CASE0(test_wah_peak_gain)
CASE0(test_wah_sweeps)
CASE0(test_wah_never_whistles)
CASE0(test_top_third_below)
CASE0(test_drive_top)
CASE0(test_wah_top)
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
CASE0(test_bass_flat_sum)
CASE0(test_bass_bit_identity)
CASE0(test_bass_no_click)
CASE0(test_bass_lows_dry_and_mono)
CASE0(test_bass_fade_no_dip)
CASE0(test_bass_drive_inside)
CASEK(test_bass_level)
CASE0(test_bass_clean)

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
    add_case("tremolo smoothness", 0, case_test_tremolo_smoothness, NULL, 1);
    add_case("rotary pitch", 0, case_test_rotary_pitch, NULL, 2);
    add_case("rotary inertia", 0, case_test_rotary_inertia, NULL, 1);
    add_case("rotary power", 0, case_test_rotary_power, NULL, 1);
    add_case("auto-wah onset", 0, case_test_wah_onset, NULL, 1);
    add_case("auto-wah peak gain", 0, case_test_wah_peak_gain, NULL, 1);
    add_case("feedback auto-wah", FX_I_WAH, case_test_feedback_stability, NULL, 3);
    add_case("auto-wah sweep", 0, case_test_wah_sweeps, NULL, 1);
    add_case("auto-wah whistle", 0, case_test_wah_never_whistles, NULL, 1);
    add_case("top thirds below 2/3", 0, case_test_top_third_below, NULL, 1);
    add_case("drive top third", 0, case_test_drive_top, NULL, 1);
    add_case("auto-wah top third", 0, case_test_wah_top, NULL, 1);

    /* The chain. */
    add_case("chain static level", 0, case_test_static_level, NULL, 1);
    add_case("chain order", 0, case_test_chain_order, NULL, 1);
    add_case("chain bypass", 0, case_test_chain_bypass, NULL, 1);
    add_case("chain toggles", 0, case_test_chain_toggles, NULL, 1);
    for (int m = 1; m <= CHAIN_CTL_CEIL; m++) {
        if (m <= (int)FX_ALL) {
            snprintf(name, sizeof(name), "chain level mask %d", m);
        } else {
            snprintf(name, sizeof(name), "chain level control %d", m - (int)FX_ALL);
        }
        g_chain_case[m] = add_case(name, m, chain_level_part, NULL,
                                   m <= (int)FX_ALL ? 8 + popcount((uint32_t)m) : 10);
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
    for (int arg = 0; arg <= LV_CTL_COLD; arg++) {
        g_lv_case[arg] = -1;
        if (arg < LV_CTL_HOT && popcount((uint32_t)(arg % LV_MASKS)) < 2) {
            continue;
        }
        snprintf(name, sizeof(name), "levels unity %d", arg);
        g_lv_case[arg] = add_case(name, arg, levels_unity_part, NULL, 4);
    }
    add_case("levels unity", 0, NULL, test_levels_unity, 0);

    /* The bass bypass. */
    add_case("bass flat sum", 0, case_test_bass_flat_sum, NULL, 2);
    add_case("bass bit identity", 0, case_test_bass_bit_identity, NULL, 1);
    add_case("bass no click", 0, case_test_bass_no_click, NULL, 1);
    add_case("bass lows dry and mono", 0, case_test_bass_lows_dry_and_mono, NULL, 3);
    add_case("bass fade no dip", 0, case_test_bass_fade_no_dip, NULL, 1);
    add_case("bass drive inside", 0, case_test_bass_drive_inside, NULL, 1);
    for (int x = 0; x < 3; x++) {
        snprintf(name, sizeof(name), "bass level %.0f", (double)BASS_LEVEL_XOVERS[x]);
        add_case(name, x, case_test_bass_level, NULL, 8);
    }
    for (int m = 1; m <= (int)FX_ALL; m++) {
        if (popcount((uint32_t)m) < 2) {
            continue;
        }
        snprintf(name, sizeof(name), "bass chain level mask %d", m);
        g_bass_chain_case[m] = add_case(name, m, bass_chain_part, NULL,
                                        m == (int)FX_ALL ? 12 : 3 + popcount((uint32_t)m) / 2);
    }
    add_case("bass chain level", 0, NULL, test_bass_chain_level, 0);
    add_case("bass clean", 0, case_test_bass_clean, NULL, 1);
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
    /* Crashes here are expected: the runner check aborts on purpose and
     * mutants trip CHECKs. Make the process non-dumpable so the kernel
     * writes no core, and the desktop crash reporter stays quiet. The
     * runner still sees the signal. FX_SELFTEST_COREDUMP=1 keeps dumps. */
    const char *dump = getenv("FX_SELFTEST_COREDUMP");
    if (dump == NULL || strcmp(dump, "1") != 0) {
        prctl(PR_SET_DUMPABLE, 0);
    }
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
    if (argc == 4 && strcmp(argv[1], "--unity-raw") == 0) {
        const int k = atoi(argv[2]);
        if (k < 0 || k >= N_FX) {
            return 2;
        }
        keys_build();
        unity_raw_line(k, (float)atof(argv[3]));
        return 0;
    }
    if ((argc == 4 || argc == 5) && strcmp(argv[1], "--unity") == 0) {
        keys_build();
        g_bass_hz = argc == 5 ? (float)atof(argv[4]) : 0.0f;
        unity_line((uint32_t)atoi(argv[2]), (float)atof(argv[3]));
        return 0;
    }
    if (argc == 3 && strcmp(argv[1], "--fit-bass") == 0) {
        const int k = atoi(argv[2]);
        if (k < 0 || k >= N_FX) {
            return 2;
        }
        keys_build();
        fit_bass(k);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--tremolo-shape") == 0) {
        tremolo_shape_report();
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--bench") == 0) {
        bench();
        return 0;
    }
    const int runner_check = argc == 3 && strcmp(argv[1], "--runner-check") == 0;
    if (!runner_check && (argc > 2 || (argc == 2 && strcmp(argv[1], "-v") != 0))) {
        fprintf(stderr, "usage: fx-selftest [-v | --parse TEXT | --parse-lines | --measure | "
                        "--measure-unlevelled | --measure-no-makeup | --tremolo-shape | --bench | "
                        "--unity-raw K DEPTH | --unity MASK DEPTH [BASS_HZ] | --fit-bass K | "
                        "--runner-check order|fail|crash]\n");
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
