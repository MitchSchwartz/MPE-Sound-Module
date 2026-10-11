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
                           float *const wet[FX_CHANNELS], uint32_t n)
{
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

/* The wire's effect ids. Append only: an id is a number the policy module
 * sends, and renumbering would silently change what an old sender selects. */
const fx_effect_desc FX_EFFECTS[] = {
    { "chorus", 0.5f, sizeof(chorus_state), chorus_init, chorus_process, chorus_feed },
};
const int FX_EFFECT_COUNT = (int)(sizeof(FX_EFFECTS) / sizeof(FX_EFFECTS[0]));

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
        e->table[a].process(e->state[a], dry, wet_a, n);
        if (b >= 0) {
            e->table[b].process(e->state[b], dry, wet_b, n);
        }
        feed_idle(e, dry, n, a, b);

        const float wa = e->table[a].wet_max;
        const float wb = b >= 0 ? e->table[b].wet_max : 0.0f;
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
            for (int ch = 0; ch < FX_CHANNELS; ch++) {
                float dry_s = dry_buf[ch][i];
                float mix = dry_s + depth_ramp[i] * wa * (wet_a_buf[ch][i] - dry_s);
                if (b >= 0) {
                    float mix_b = dry_s + depth_ramp[i] * wb * (wet_b_buf[ch][i] - dry_s);
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
