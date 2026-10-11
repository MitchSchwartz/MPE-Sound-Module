/*
 * fx_dsp — the effect engine inside mpe-fx-host, with no JACK in it.
 *
 * Kept apart from the JACK client so the properties that protect the listener
 * can be run on a laptop with no audio server: `fx_selftest.c` drives this file
 * directly (make selftest, and tests/test_fx_host.py).
 *
 * Shape:
 *
 *   - An effect is a table entry (`fx_effect_desc`): how big its state is, how
 *     to initialise it, how to produce its wet signal, and how to keep its
 *     history warm while it is not being heard. Adding an effect is a new
 *     entry, not a new process.
 *   - Every entry's state is allocated once, in `fx_engine_init`, before the
 *     client is activated. Nothing here allocates afterwards.
 *   - The engine owns everything the listener can hear go wrong: depth
 *     smoothing, the crossfade on an effect swap, bypass at zero, and the
 *     output clamp. Effects only ever produce a wet signal.
 *
 * Level law. Depth d in [0, 1] mixes toward the wet signal, never on top of
 * it:   out = dry + d * wet_max * (wet - dry)
 * So at d = 0 the output IS the dry signal, and at any d it is a weighted
 * average of dry and wet with non-negative weights summing to 1. An effect
 * whose wet signal never exceeds its recent input (the chorus: a linearly
 * interpolated read of its own delay line, no feedback) therefore cannot make
 * the output louder than the input was. The clamp to ±1.0 is the backstop for
 * a future effect that can.
 */

#ifndef MPE_FX_DSP_H
#define MPE_FX_DSP_H

#include <stddef.h>
#include <stdint.h>

#define FX_CHANNELS 2

/* Highest JACK rate the pre-allocated delay lines are sized for. The client
 * refuses to start above it rather than reading past a buffer. */
#define FX_MAX_RATE 192000

/* The engine works in chunks of at most this many frames, so its scratch
 * buffers are fixed-size whatever period jackd runs at. */
#define FX_CHUNK 64

/* Room for the table. Raising it costs one pointer per slot. */
#define FX_MAX_EFFECTS 8

typedef struct {
    const char *name;
    /* Fraction of wet at depth 1.0. Chorus is 0.5: fully wet is vibrato, and
     * the chorus sound is the mix. */
    float wet_max;
    size_t state_bytes;
    /* Called at startup and on a rate change. Never allocates; `state` is
     * already `state_bytes` of zeroed memory. Returns 0 on success. */
    int (*init)(void *state, float rate);
    /* Produce `n` (<= FX_CHUNK) frames of wet signal from `in`. Must read
     * in[ch][i] before anything is written to wet: the engine may hand the
     * same memory to its output. */
    void (*process)(void *state, const float *const in[FX_CHANNELS],
                    float *const wet[FX_CHANNELS], uint32_t n);
    /* Keep history current without producing output — the bypass path. The
     * chorus writes its delay line and skips the modulated reads and LFO, so
     * coming back in reads real audio, not whatever was there last time. NULL
     * for an effect with no history. */
    void (*feed)(void *state, const float *const in[FX_CHANNELS], uint32_t n);
} fx_effect_desc;

typedef struct {
    const fx_effect_desc *table;
    int count;
    void *state[FX_MAX_EFFECTS];
    float rate;

    int active;
    int incoming;       /* -1 unless a crossfade is running */
    float xfade_pos;    /* 0 -> 1 across the crossfade */
    float xfade_step;   /* per frame; 1.0 makes a swap instant */
    double xfade_ms;

    float depth;        /* smoothed */
    float depth_alpha;  /* one-pole coefficient; 0 means "jump" */
    double depth_ms;
} fx_engine;

/* The effects this build ships. Index = the effect id on the wire. */
extern const fx_effect_desc FX_EFFECTS[];
extern const int FX_EFFECT_COUNT;

/* Allocates every table entry's state. Startup only. Returns 0 on success. */
int fx_engine_init(fx_engine *e, const fx_effect_desc *table, int count, float rate,
                   double depth_ms, double xfade_ms);
void fx_engine_free(fx_engine *e);

/* Re-derive rate-dependent coefficients and re-init every effect. No
 * allocation, so it may run in the RT callback when jackd changes rate. */
int fx_engine_set_rate(fx_engine *e, float rate);

/* One period. `in` entries may be NULL (read as silence); `out` entries may be
 * NULL (skipped) and may alias `in`. `effect` and `depth` are the targets the
 * control thread last stored; out-of-range values are ignored / clamped. */
void fx_engine_process(fx_engine *e, const float *const in[FX_CHANNELS],
                       float *const out[FX_CHANNELS], uint32_t nframes,
                       int effect, float depth);

/* `fx <effect id> <depth>`, one line of ASCII per datagram. Returns 1 and
 * fills the outputs on a well-formed message, 0 otherwise. Range checks on the
 * id are the caller's (it knows the table); depth is returned unclamped but
 * finite. Shared by the client and the self-test so the Python side's encoder
 * can be checked against the real parser. */
int fx_parse_control(const char *text, int *effect, float *depth);

#endif
