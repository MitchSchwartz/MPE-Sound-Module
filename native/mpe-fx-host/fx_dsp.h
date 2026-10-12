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
 * it:   out = dry + w(d) * (wet - dry),   w(d) = wet_max * min(1, d / mix_full_at)
 * So at d = 0 the output IS the dry signal, and at any d it is a weighted
 * average of dry and wet with non-negative weights summing to 1. An effect
 * whose wet signal is never louder than its input therefore cannot make the
 * output louder either. Each effect states how it keeps that promise next to
 * its code in fx_dsp.c, and fx_selftest.c measures it at every knob position.
 * The clamp to ±1.0 is the backstop, not the mechanism.
 *
 * For most effects the knob IS the mix (mix_full_at = 1). The drive is the
 * exception: its knob is the drive amount, handed to `process` per frame, and
 * the mix only fades the effect in across the first few percent so leaving
 * bypass is a fade, not a step.
 *
 * Level match. A mix that never gets louder usually gets quieter: half dry
 * and half a decorrelated copy is -3 dB, a phaser's notches take energy out,
 * a drive's tone filters do too. An effect with makeup_max > 1 gets the
 * engine's level stage on its mix:
 *
 *   g    = clamp( sqrt(ms_dry / ms_mix) * LEVEL_TARGET, 1, makeup_max ), smoothed
 *   out  = limit( g * mix,  max(peak_dry, |mix|) )
 *
 * ms_* are 50 ms mean squares over both channels, peak_dry the largest dry
 * sample of the last ~24 ms. The gain only ever lifts (g >= 1), so an effect
 * that is too loud on its own still shows as too loud: the stage cannot hide
 * it. The limiter (instant attack, 50 ms release) keeps every lifted sample at
 * or below the larger of the recent input peak and what the unlifted mix was,
 * so the makeup cannot add a peak the input or the effect did not already
 * have.
 * The tremolo has no level stage: see fx_dsp.c for why its level falls.
 *
 * Cost: the dry detectors ~15 ops per frame whenever the effect is heard, the
 * stage ~30 more for an effect that has it (the sqrt and divide once per
 * 64-frame chunk, not per frame). Laptop: ~+5 and ~+10 ns per frame.
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
    /* Depth at which the mix reaches wet_max: 1.0 when the knob is the mix. */
    float mix_full_at;
    /* Largest lift the engine's level stage may apply to this effect's mix;
     * 1.0 turns the stage off (see "Level match" above). */
    float makeup_max;
    size_t state_bytes;
    /* Called at startup and on a rate change. Never allocates; `state` is
     * already `state_bytes` of zeroed memory. Returns 0 on success. */
    int (*init)(void *state, float rate);
    /* Produce `n` (<= FX_CHUNK) frames of wet signal from `in`. `amount` is
     * the smoothed depth, one value per frame, for an effect whose knob sets
     * a parameter rather than the mix; the others ignore it. Must read
     * in[ch][i] before anything is written to wet: the engine may hand the
     * same memory to its output. */
    void (*process)(void *state, const float *const in[FX_CHANNELS],
                    float *const wet[FX_CHANNELS], const float *amount, uint32_t n);
    /* Keep history current without producing output — the bypass path. The
     * chorus writes its delay line and skips the modulated reads and LFO, so
     * coming back in reads real audio, not whatever was there last time. The
     * filters (drive, phaser) run in full with the output discarded; the
     * tremolo advances its LFO. NULL for an effect with no history. */
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

    /* Level stage (see "Level match" above). */
    float lv_detect;    /* one-pole coefficient of the mean squares */
    float lv_smooth;    /* one-pole coefficient of the gain */
    float lv_release;   /* one-pole coefficient of the limiter's recovery */
    float ms_dry;
    float lv_ms[FX_MAX_EFFECTS];
    float lv_gain[FX_MAX_EFFECTS];
    float lv_target[FX_MAX_EFFECTS];
    float lim_gain[FX_MAX_EFFECTS];
    float peak_blocks[5];
    float peak_now;
    uint32_t block_len, block_pos;
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

#ifdef FX_SELFTEST
/* Deliberately broken builds of the shipped effects, compiled only into the
 * self-test (make selftest defines FX_SELFTEST). Each check that guards a
 * property runs against the matching mutant and must fail it. Indices: */
enum {
    FX_MUT_DRIVE_NO_OVERSAMPLING,   /* the shaper runs at the base rate */
    FX_MUT_DRIVE_NO_CEILING,        /* makeup without the peak ceiling or the limiter */
    FX_MUT_DRIVE_NO_MAKEUP,         /* no level compensation at all: too quiet */
    FX_MUT_DRIVE_OLD_SHAPER,        /* the round-2 cubic shaper: harsher */
    FX_MUT_DRIVE_NO_FLUSH,          /* filter memories never flushed */
    FX_MUT_PHASER_NO_FLUSH,
    FX_MUT_PHASER_HOT_FEEDBACK,     /* positive feedback: resonant peaks > 1 */
    FX_MUT_TREMOLO_OVERSHOOT,       /* LFO gain reaching 1.25 */
    FX_MUT_TREMOLO_SHALLOW_TOP,     /* LFO gain topping out at 0.8: too quiet */
    FX_MUT_COUNT
};
extern const fx_effect_desc FX_TEST_MUTANTS[];
/* Non-zero turns the engine's output clamp off. Self-test only. */
extern int fx_test_no_clamp;
/* Broken level stages, self-test only: no lift at all (the lower bound must
 * catch it), a target 0.5 dB hot (the upper bound must), and no limiter (the
 * peak bound must). */
extern int fx_test_no_level;
extern int fx_test_level_hot;
extern int fx_test_no_limit;
#endif

#endif
