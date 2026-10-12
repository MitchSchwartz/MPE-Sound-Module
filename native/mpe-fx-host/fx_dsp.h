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
 *     smoothing, the crossfade when an effect is enabled or disabled, bypass,
 *     the level curve, and the output clamp. Effects only ever produce a wet
 *     signal.
 *
 * Chain. The enabled effects run in series in one fixed order, FX_CHAIN_ORDER
 * (Drive -> Phaser -> Chorus -> Tremolo), whatever order they were enabled in.
 * An enable mask (bit 1 << id) says which; one depth applies to all of them.
 * Each stage k turns its input x into
 *
 *   y_k = g_k(d) * ( x + w_k(d) * (wet_k(x) - x) )
 *   x'  = x + c_k * (y_k - x)
 *
 * where c_k is the stage's enable ramp: it runs 0 -> 1 when the bit is set and
 * back when it is cleared, over the crossfade time, so a toggle is a fade, not
 * a step. A stage at c_k = 0 is skipped (its `feed` keeps its history current
 * with the signal at its place in the chain) and contributes x' = x exactly.
 *
 * Bypass. Depth 0, or no stage enabled or still fading: the output IS the
 * input, bit for bit, and every effect is fed the input.
 *
 * Level law. Depth d in [0, 1] mixes toward the wet signal, never on top of
 * it:   w(d) = wet_max * min(1, d / mix_full_at)
 * so at d = 0 a stage is its input, and at any d its mix is a weighted average
 * of dry and wet with non-negative weights summing to 1. An effect whose wet
 * signal is never louder than its input therefore cannot make the mix louder
 * either. The enable crossfade is another such average, so a chain of stages
 * that are each never louder is never louder: fx_selftest.c measures that for
 * every effect and every mask, at every knob position. The clamp to ±1.0 is
 * the backstop, not the mechanism.
 *
 * For most effects the knob IS the mix (mix_full_at = 1). The drive is the
 * exception: its knob is the drive amount, handed to `process` per frame, and
 * the mix only fades the effect in across the first few percent so leaving
 * bypass is a fade, not a step.
 *
 * Level curve. A mix that never gets louder usually gets quieter: half dry and
 * half a decorrelated copy is -3 dB, a phaser's notches take energy out. For an
 * effect with level_scale > 0 the engine applies a STATIC gain per knob
 * position (round 4: the round-3 dynamic makeup and its limiter distorted, see
 * fx_dsp.c):
 *
 *   g(d) = ( 1 - 2 w (1 - w) (1 - level_corr) ) ^ (-level_scale / 2)
 *
 * The bracket is the mean square of a w-weighted mix of two signals of equal
 * level whose correlation is level_corr, so scale 1 would restore exactly that
 * loss; the scale holds it short of parity. Both are calibrated on the keys
 * signal (fx_selftest.c) and tabulated per effect at init; per frame it is a
 * table lookup of the smoothed depth, so the gain moves only with the knob. It
 * never changes with the signal: no detector, no limiter, no pumping.
 * g(0) = 1 exactly.
 *
 * Cost of the engine per enabled stage, per stereo frame: mix 6, level lookup
 * 5, apply 2, enable crossfade 6 => ~20 ops on top of the effect's own.
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
    /* The static level curve (see "Level curve" above). level_scale 0 turns
     * it off: g(d) = 1. */
    float level_corr;
    float level_scale;
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

/* Points of the per-effect level table over depth 0..1. */
#define FX_LEVEL_POINTS 64

typedef struct {
    const fx_effect_desc *table;
    int count;
    void *state[FX_MAX_EFFECTS];
    float rate;

    /* The chain: table indices in run order (FX_CHAIN_ORDER, restricted to
     * this table; any index it does not name runs last, in id order). */
    int order[FX_MAX_EFFECTS];

    float on[FX_MAX_EFFECTS];   /* enable ramp per effect, 0..1 */
    float xfade_step;           /* per frame; 1.0 makes a toggle instant */
    double xfade_ms;

    float depth;        /* smoothed */
    float depth_alpha;  /* one-pole coefficient; 0 means "jump" */
    double depth_ms;

    float level[FX_MAX_EFFECTS][FX_LEVEL_POINTS + 1];
} fx_engine;

/* The effects this build ships. Index = the effect id on the wire. */
extern const fx_effect_desc FX_EFFECTS[];
extern const int FX_EFFECT_COUNT;
/* The order they run in, as ids, first to last. */
extern const int FX_CHAIN_ORDER[];
extern const int FX_CHAIN_LEN;

/* Allocates every table entry's state. Startup only. Returns 0 on success. */
int fx_engine_init(fx_engine *e, const fx_effect_desc *table, int count, float rate,
                   double depth_ms, double xfade_ms);
void fx_engine_free(fx_engine *e);

/* Re-derive rate-dependent coefficients and re-init every effect. No
 * allocation, so it may run in the RT callback when jackd changes rate. */
int fx_engine_set_rate(fx_engine *e, float rate);

/* One period. `in` entries may be NULL (read as silence); `out` entries may be
 * NULL (skipped) and may alias `in`. `mask` (bit 1 << id per enabled effect)
 * and `depth` are the targets the control thread last stored; bits beyond the
 * table are ignored and depth is clamped. */
void fx_engine_process(fx_engine *e, const float *const in[FX_CHANNELS],
                       float *const out[FX_CHANNELS], uint32_t nframes,
                       uint32_t mask, float depth);

/* One line of ASCII per datagram:
 *   `chain <mask> <depth>`   the enabled effects and the one depth
 *   `fx <effect id> <depth>` the first protocol: means chain <1 << id> <depth>
 * Returns 1 and fills the outputs on a well-formed message, 0 otherwise. Range
 * checks against the table are the caller's (it knows the table; the parser
 * only refuses bits beyond FX_MAX_EFFECTS); depth is returned unclamped but
 * finite. Shared by the client and the self-test so the Python side's encoder
 * can be checked against the real parser. */
int fx_parse_control(const char *text, uint32_t *mask, float *depth);

#ifdef FX_SELFTEST
/* Deliberately broken builds of the shipped effects, compiled only into the
 * self-test (make selftest defines FX_SELFTEST). Each check that guards a
 * property runs against the matching mutant and must fail it. Indices: */
enum {
    FX_MUT_DRIVE_NO_OVERSAMPLING,   /* the shaper runs at the base rate */
    FX_MUT_DRIVE_NO_CEILING,        /* makeup without the peak ceiling or the limiter */
    FX_MUT_DRIVE_NO_MAKEUP,         /* no level compensation at all: too quiet */
    FX_MUT_DRIVE_OLD_SHAPER,        /* the round-2 cubic shaper: harsher */
    FX_MUT_DRIVE_ROUND3_BIAS,       /* bias 0.1: fewer even harmonics */
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
/* Broken level curves, self-test only: none at all (the lower bound must
 * catch it), 0.5 dB hot (the upper bound must), and a gain that wobbles with
 * time, as the round-3 makeup did with the phaser's sweep (the static-gain
 * check must). */
extern int fx_test_no_level;
extern int fx_test_level_hot;
extern int fx_test_level_wobble;
/* The chain run in reverse order (the order check must catch it). */
extern int fx_test_reverse_order;
#endif

#endif
