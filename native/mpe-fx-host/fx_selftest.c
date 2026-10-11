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
    g_two[1] = (fx_effect_desc){ "flip", 0.5f, 1.0f, 1, flip_init, flip_process, NULL };
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
static int g_verbose;

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
static const fx_effect_desc LEAKY = { "leaky", 1.0f, 1.0f, sizeof(leaky_state),
                                      leaky_init, leaky_process, leaky_feed };

/* --- signals ------------------------------------------------------------- */

enum { SIG_NOISE, SIG_SINE };
typedef struct {
    int kind;
    float amp;
    float hz;
    double phase;
} signal_gen;

static void sig_fill(signal_gen *g, float *l, float *r, uint32_t n)
{
    for (uint32_t i = 0; i < n; i++) {
        if (g->kind == SIG_NOISE) {
            l[i] = g->amp * noise();
            r[i] = g->amp * noise();
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

typedef struct {
    float rms_ratio;    /* worst out/in */
    float peak_ratio;   /* worst out/in */
    float at_depth;
    int at_signal;
} level_result;

#define N_LEVEL_SIGNALS 7
static const signal_gen LEVEL_SIGNALS[N_LEVEL_SIGNALS] = {
    { SIG_NOISE, 0.5f, 0.0f, 0.0 },
    { SIG_NOISE, 0.05f, 0.0f, 0.0 },
    { SIG_SINE, 0.5f, 220.0f, 0.0 },
    { SIG_SINE, 0.5f, 80.0f, 0.0 },
    { SIG_SINE, 0.3f, 3000.0f, 0.0 },
    { SIG_SINE, 0.02f, 220.0f, 0.0 },
    { SIG_SINE, 0.9f, 440.0f, 0.0 },
};

static level_result level_sweep(const fx_effect_desc *table, int k)
{
    level_result res = { 0.0f, 0.0f, 0.0f, -1 };
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    for (int sgi = 0; sgi < N_LEVEL_SIGNALS; sgi++) {
        for (int step = 0; step <= 20; step++) {
            float depth = (float)step / 20.0f;
            fx_engine e;
            CHECK(fx_engine_init(&e, table, N_FX, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
            signal_gen g = LEVEL_SIGNALS[sgi];
            double sum_in = 0.0, sum_out = 0.0;
            float peak_in = 0.0f, peak_out = 0.0f;
            const int settle = (int)(0.4f * RATE / PERIOD);
            const int measure = (int)(1.2f * RATE / PERIOD);
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
            float rr = (float)sqrt(sum_out / sum_in);
            float pr = peak_out / peak_in;
            if (rr > res.rms_ratio) {
                res.rms_ratio = rr;
                res.at_depth = depth;
                res.at_signal = sgi;
            }
            res.peak_ratio = fmaxf(res.peak_ratio, pr);
        }
    }
    return res;
}

static void test_pack_level_bound(int k)
{
    level_result r = level_sweep(FX_EFFECTS, k);
    if (g_verbose) {
        printf("  %-8s level: worst RMS out/in %+.3f dB (depth %.2f, signal %d), "
               "worst peak out/in %+.3f dB\n", FX_LABEL[k], 20.0 * log10(r.rms_ratio),
               (double)r.at_depth, r.at_signal, 20.0 * log10(r.peak_ratio));
    }
    CHECK(r.rms_ratio <= RMS_MARGIN, "%s: output RMS %.4fx input (depth %.2f, signal %d)",
          FX_LABEL[k], (double)r.rms_ratio, (double)r.at_depth, r.at_signal);

    /* Control. The tremolo's guarantee is the peak one, so its broken twin is
     * an LFO that overshoots unity; the phaser's is the feedback that keeps
     * every frequency at or under unity; the drive's is the makeup matching
     * RMS, so 0.5 dB on top of it must show. */
    level_result c;
    if (k == 1) {
        fx_effect_desc hot = hot_version(k);
        c = level_sweep(table_with(k, &hot), k);
        CHECK(c.rms_ratio > RMS_MARGIN, "drive: control (+0.5 dB) passed: %.4f",
              (double)c.rms_ratio);
    } else if (k == 2) {
        c = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_PHASER_HOT_FEEDBACK]), k);
        CHECK(c.rms_ratio > RMS_MARGIN, "phaser: control (hot feedback) passed: %.4f",
              (double)c.rms_ratio);
    } else {
        c = level_sweep(table_with(k, &FX_TEST_MUTANTS[FX_MUT_TREMOLO_OVERSHOOT]), k);
        CHECK(c.peak_ratio > 1.0f + 1e-6f, "tremolo: control (overshoot) passed: %.4f",
              (double)c.peak_ratio);
    }
    if (g_verbose) {
        printf("  %-8s level control: RMS %+.3f dB, peak %+.3f dB\n", FX_LABEL[k],
               20.0 * log10(c.rms_ratio), 20.0 * log10(c.peak_ratio));
    }
    if (k == 3) {   /* only the tremolo can promise the peak */
        CHECK(r.peak_ratio <= 1.0f + 1e-6f, "tremolo: output peak %.6fx input",
              (double)r.peak_ratio);
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

static size_t clamp_violations(int k, float *recovered)
{
    fx_engine e;
    CHECK(fx_engine_init(&e, FX_EFFECTS, FX_EFFECT_COUNT, RATE, DEPTH_MS, XFADE_MS) == 0, "init");
    float l[PERIOD], r[PERIOD], ol[PERIOD], or_[PERIOD];
    size_t bad = 0;
    for (int p = 0; p < 2000; p++) {
        for (uint32_t i = 0; i < PERIOD; i++) {
            l[i] = 4.0f * noise();
            r[i] = (i % 97 == 0) ? NAN : (i % 89 == 0 ? INFINITY : 4.0f * noise());
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
    float rec = 0.0f;
    size_t bad = clamp_violations(k, &rec);
    CHECK(bad == 0, "%s: %zu samples non-finite or beyond ±1.0", FX_LABEL[k], bad);
    CHECK(rec > 0.3f, "%s: output %.3f of normal after NaN/Inf input — a memory stayed poisoned",
          FX_LABEL[k], (double)rec);
    fx_test_no_clamp = 1;
    size_t c = clamp_violations(k, &rec);
    fx_test_no_clamp = 0;
    CHECK(c > 0, "%s: control (clamp off) passed too", FX_LABEL[k]);
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

/* Measured: -26.0 dB oversampled, -8.9 dB without. What remains is not the
 * half-band filters (they reject ~70 dB) but harmonics 17-21 of a near-square
 * 10 kHz wave, above 96 kHz, folding at the 4x rate itself (190 kHz -> 2 kHz,
 * 170 -> 22, 200 -> 8). Only a higher factor removes those; for content
 * below ~5 kHz they sit far lower. The limit sits midway. */
#define ALIAS_LIMIT_DB -20.0f

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

static void test_pack(void)
{
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
