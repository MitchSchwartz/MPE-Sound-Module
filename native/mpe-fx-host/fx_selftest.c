/*
 * fx_selftest — the listener-protecting properties of fx_dsp, run without JACK.
 *
 *   fx-selftest                 run every check; exit 0 only if all pass
 *   fx-selftest --parse TEXT    print "ok <id> <depth>" or "reject" for one
 *                               datagram, through the client's real parser
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
                         float *const wet[FX_CHANNELS], uint32_t n)
{
    (void)s;
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
    g_two[1] = (fx_effect_desc){ "flip", 0.5f, 1, flip_init, flip_process, NULL };
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
static void test_output_is_bounded(void)
{
    fx_engine e;
    build_two();
    CHECK(fx_engine_init(&e, g_two, 2, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    const float amp = 0.5f;
    float peak = 0.0f;
    for (int p = 0; p < 6000; p++) {          /* 32 s */
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = amp * noise();
            r[i] = amp * noise();
        }
        float depth = (p % 37 == 0) ? 0.0f : (float)((p * 7919) % 101) / 100.0f;
        int effect = (p / 300) % 2;
        run_period(&e, l, r, ol, or_, PERIOD, effect, depth);
        for (uint32_t i = 0; i < PERIOD; i++) {
            peak = fmaxf(peak, fmaxf(fabsf(ol[i]), fabsf(or_[i])));
        }
    }
    CHECK(peak <= amp + 1e-6f, "output peak %.7f exceeds input peak %.7f", peak, amp);

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
    FX_EFFECTS[0].process(e.state[0], in, wet, FX_CHUNK);
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

    test_depth_zero_is_the_input();
    test_output_is_bounded();
    test_long_run_stays_clean();
    test_depth_step_has_no_click();
    test_swap_crossfades();
    test_bypass_keeps_history();
    test_rate_limits();

    if (g_failures != 0) {
        fprintf(stderr, "fx-selftest: %d check(s) failed\n", g_failures);
        return 1;
    }
    printf("fx-selftest: all checks passed\n");
    return 0;
}
