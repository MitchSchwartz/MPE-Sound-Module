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
 *     the level curve, the chain's makeup, and the output clamp. Effects only ever produce a wet
 *     signal.
 *
 * Chain. The enabled effects run in series in one fixed order, FX_CHAIN_ORDER
 * (Drive -> Auto-wah -> Phaser -> Flanger -> Chorus -> Rotary -> Tremolo),
 * whatever order they were enabled in.
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
 * of dry and wet with non-negative weights summing to 1. The enable
 * crossfade is another such average. What sets the level is the level curve
 * and the makeup below; the clamp to ±1.0 is the backstop, not the mechanism.
 *
 * For most effects the knob IS the mix (mix_full_at = 1). The drive is the
 * exception: its knob is the drive amount, handed to `process` per frame, and
 * the mix only fades the effect in across the first few percent so leaving
 * bypass is a fade, not a step.
 *
 * Level curve. A mix usually loses level: half dry and half a decorrelated
 * copy is -3 dB, a phaser's notches take energy out, a tremolo's dips and an
 * auto-wah's narrow band take more. The engine applies a STATIC gain per knob
 * position (round 4: the round-3 dynamic makeup and its limiter distorted, see
 * fx_dsp.c) that brings the effect back to its input's level on the keys
 * signal (2026-10-13; until then the curves were held short of it, and the
 * tremolo and the auto-wah had none):
 *
 *   g(d)  = ms(d) ^ (-1/2)
 *   ms(d) = (1 - w)^2 + w^2 (p0 + p1 d) + 2 w (1 - w) (c0 + c1 d)
 *
 * ms is the mean square of the w-weighted mix of the input (power 1) and a
 * wet signal of power p0 + p1 d whose cross term with the input is c0 + c1 d:
 * the model of the loss, four numbers per effect (fx_level_fit), least-squares
 * fitted to each effect's measured loss on keys (fx_dsp.c, the *_LEVEL
 * fits). All zero turns the curve off: g = 1. Tabulated per effect at init;
 * per frame it is a table lookup of the smoothed depth, so the gain moves
 * only with the knob. It never changes with the signal: no detector, no
 * limiter, no pumping. g(0) = 1 exactly.
 *
 * So the engine is no longer "never louder" on every signal: it is the input's
 * level on keys, and a signal that loses less than keys through an effect
 * (the auto-wah's 3 kHz tone, the rotary's 80 Hz tone in its fade-in) comes
 * out over its input, by up to 2.5 dB. Peaks rise with the gain: the
 * tremolo's by its whole lift (+3.4 dB at full, its gain's crest). Measured
 * (fx_selftest.c, which holds these): each effect alone, keys within 0.3 dB of
 * G(m) x the input at every knob position; any chain of two or more, -0.6..
 * +1.35 dB, because the stages' losses do not multiply exactly (the phaser and
 * the flanger together stack their combs: the top of that range); any signal,
 * at most +3 dB. --measure and --unity report the other signals.
 *
 * Per-effect levels. Each effect has a level, 0..1 (default 1), and plays at
 * its own depth d_k = shared depth x level_k: one knob moves every enabled
 * effect and they keep their proportions. Everything above is per effect and
 * reads d_k: the mix w_k(d_k), the level curve g_k(d_k), and the drive's
 * amount. Each d_k is smoothed on its own (the same one-pole, DEPTH_MS), so a
 * level move ramps like a knob move. A stage whose d_k is 0, smoothed and
 * target, is skipped as a disabled one is (fed, and x' = x exactly); when every
 * enabled stage is, that is the bypass above, bit for bit. A level only picks
 * which d in [0, 1] a stage plays at, and each stage is at unity at every d.
 *
 * Makeup (2026-10-13). "Never louder" read as "adding an effect takes level
 * away", so the chain now ends in one small gain, and the level curves bring
 * every effect to unity under it, so the total is the makeup whatever is on:
 *
 *   out = G(m) * x_chain,   G(m) = 10 ^ (FX_MAKEUP_DB * m / 20)
 *   m   = max over the stages running this frame of c_k * d_k
 *
 * Once per chain, not per stage: five stages at +1 dB each would be +5 dB,
 * and the brief is "keep it small". The max, not a sum or a mean, so the
 * makeup is the loudest stage's depth: one effect at full gets the whole
 * +1 dB, and switching on a second at a lower level adds nothing. m reads
 * the smoothed d_k and the enable ramp c_k, the values the stages themselves
 * play at, so G has no smoother of its own and cannot disagree with them: a
 * knob, level or mask move ramps the makeup exactly as it ramps the mix and
 * the level curve. A max of continuous ramps is continuous, so there is no
 * step. G(0) = 1 exactly, so depth 0 is still the input bit for bit, and so
 * is a stage still ramping up from 0. On keys the output is G(m) x the
 * input (bounds above); fx_selftest.c also checks that at full depth the
 * output is +1 dB over the same chain without the makeup, and +0.5 dB at
 * half. Tabulated like the level curve; per frame it is a max per stage (2
 * ops), a lookup (5) and a multiply per channel (2).
 *
 * Cost of the engine per enabled stage, per stereo frame: depth smoothing 4
 * (none once it has settled), mix 6, level lookup 5, apply 2, enable crossfade
 * 6 => ~23 ops on top of the effect's own. Before per-effect levels the
 * smoother ran once per frame for the whole chain: + ~4 ops per enabled stage
 * after the first while a depth or level is moving, and nothing once settled.
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

/* The level model's four numbers (see "Level curve" above). */
typedef struct {
    float p0, p1, c0, c1;
} fx_level_fit;

static inline int fx_level_fit_on(const fx_level_fit *f)
{
    return f->p0 != 0.0f || f->p1 != 0.0f || f->c0 != 0.0f || f->c1 != 0.0f;
}

/* The smallest mean square the level curve divides by: a 20 dB lift at most,
 * whatever a fit does outside the range it was fitted on. */
#define FX_LEVEL_MS_MIN 0.01

typedef struct {
    const char *name;
    /* Fraction of wet at depth 1.0. Chorus is 0.5: fully wet is vibrato, and
     * the chorus sound is the mix. */
    float wet_max;
    /* Depth at which the mix reaches wet_max: 1.0 when the knob is the mix. */
    float mix_full_at;
    /* The static level curve (see "Level curve" above). All zero turns it
     * off: g(d) = 1. */
    fx_level_fit level;
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
     * recursive ones (drive, phaser, and the flanger, whose delay line holds
     * its own feedback) run in full with the output discarded; the tremolo
     * advances its LFO. NULL for an effect with no history. */
    void (*feed)(void *state, const float *const in[FX_CHANNELS], uint32_t n);
} fx_effect_desc;

/* Points of the per-effect level table over depth 0..1. 80, not 64, so that
 * the drive's and the rotary's fade-ins (mix_full_at 0.05 and 0.1) end on a
 * point: the level curve has a corner there, and at 64 points the linear
 * interpolation cut across it, +0.3 dB at the rotary's 0.1. */
#define FX_LEVEL_POINTS 80

/* The chain's makeup at full depth, in dB (see "Makeup" above). The one place
 * it is set: the self-test derives its bounds from it. */
#define FX_MAKEUP_DB 1.0

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

    /* Each effect's own depth, smoothed: the shared depth times its level
     * (see "Per-effect levels" above). */
    float depth[FX_MAX_EFFECTS];
    float depth_alpha;  /* one-pole coefficient; 0 means "jump" */
    double depth_ms;

    float level[FX_MAX_EFFECTS][FX_LEVEL_POINTS + 1];
    /* G(m) over m = 0..1, the chain's makeup (see "Makeup" above). */
    float makeup[FX_LEVEL_POINTS + 1];
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
 * NULL (skipped) and may alias `in`. `mask` (bit 1 << id per enabled effect),
 * `depth` and `levels` (one per table entry, indexed by effect id; NULL means
 * every level 1.0) are the targets the control thread last stored; bits beyond
 * the table are ignored, and depth and each level are clamped to 0..1 (NaN
 * reads as 0). */
void fx_engine_process_levels(fx_engine *e, const float *const in[FX_CHANNELS],
                              float *const out[FX_CHANNELS], uint32_t nframes,
                              uint32_t mask, float depth, const float *levels);
/* The same with every level at 1.0. */
void fx_engine_process(fx_engine *e, const float *const in[FX_CHANNELS],
                       float *const out[FX_CHANNELS], uint32_t nframes,
                       uint32_t mask, float depth);

/* One line of ASCII per datagram:
 *   `chainl <mask> <depth> <l0> <l1> ...`  the enabled effects, the shared
 *                            depth, and a level per effect in effect-id order
 *   `chain <mask> <depth>`   the same with every level 1.0
 *   `fx <effect id> <depth>` the first protocol: means chain <1 << id> <depth>
 * `chainl` carries from 0 to FX_MAX_EFFECTS levels. Append-only: an effect
 * the sender names no level for plays at level 1.0, and levels for ids this
 * build does not ship are parsed and left unused, so a sender that knows a
 * sixth effect and a client that does not still agree on the first five.
 * Returns 1 and fills the outputs (all FX_MAX_EFFECTS levels) on a well-formed
 * message, 0 otherwise. Range checks against the table are the caller's (it
 * knows the table; the parser only refuses bits beyond FX_MAX_EFFECTS); depth
 * and levels are returned unclamped but finite. Shared by the client and the
 * self-test so the Python side's encoder can be checked against the real
 * parser. */
int fx_parse_control(const char *text, uint32_t *mask, float *depth,
                     float levels[FX_MAX_EFFECTS]);

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
    FX_MUT_PHASER_HOT_FEEDBACK,     /* feedback 0.95: resonance far above unity */
    FX_MUT_PHASER_RUNAWAY,          /* feedback 1.1: the loop grows */
    FX_MUT_TREMOLO_OVERSHOOT,       /* LFO gain reaching 1.25 */
    FX_MUT_TREMOLO_SHALLOW_TOP,     /* LFO gain topping out at 0.8: too quiet */
    FX_MUT_TREMOLO_ROUNDED_TRI,     /* the pre-2026-10-13 tremolo: rounded triangle, to 0 */
    FX_MUT_TREMOLO_NO_FLOOR,        /* the sine, but still down to 0 at full depth */
    FX_MUT_FLANGER_NO_FLUSH,        /* delay line and filter memories never flushed */
    FX_MUT_FLANGER_HOT_FEEDBACK,    /* feedback 0.95: resonance far above unity */
    FX_MUT_FLANGER_RUNAWAY,         /* feedback 1.1: the loop grows */
    FX_MUT_ROTARY_NO_FLUSH,         /* crossover memories never flushed */
    FX_MUT_ROTARY_NO_DOPPLER,       /* the rotors move the level only, not the delay */
    FX_MUT_ROTARY_NO_INERTIA,       /* rotor speed jumps to the knob's */
    FX_MUT_ROTARY_LINEAR_AM,        /* gains 1 +- a c: the two sides' power swells */
    FX_MUT_ROTARY_WIDE_CENTER,      /* delays centred on 1 ms: a deeper comb in the fade-in */
    FX_MUT_WAH_NO_FLUSH,            /* filter and envelope memories never flushed */
    FX_MUT_WAH_NO_SWEEP,            /* the envelope never reaches the filter */
    FX_MUT_WAH_RUNAWAY,             /* negative damping: the resonance grows */
    FX_MUT_WAH_WHISTLE,             /* Q 12, the bandpass unnormalised: a whistle */
    FX_MUT_WAH_SLOW_ATTACK,         /* envelope attack 400 ms, not 4: the band opens late */
    FX_MUT_COUNT
};
extern const fx_effect_desc FX_TEST_MUTANTS[];
/* Non-zero turns the engine's output clamp off. Self-test only. */
extern int fx_test_no_clamp;
/* Broken level curves, self-test only: none at all, 0.5 dB hot and 0.5 dB
 * cold (the unity bounds must catch each), and a gain that wobbles with time,
 * as the round-3 makeup did with the phaser's sweep (the static-gain check
 * must). */
extern int fx_test_no_level;
extern int fx_test_level_hot;
extern int fx_test_level_cold;
extern int fx_test_level_wobble;
/* The chain run in reverse order (the order check must catch it). */
extern int fx_test_reverse_order;
/* The makeup broken: off (the lower bound must catch it), and following the
 * targets instead of the smoothed depths and enable ramps (the click checks
 * must). */
extern int fx_test_no_makeup;
extern int fx_test_makeup_unsmoothed;
/* A rotary state's rotor speed in Hz (rotor 0 the horn, 1 the drum), for the
 * inertia check: the state is private to fx_dsp.c. */
float fx_test_rotary_speed(const void *state, int rotor);
/* The LFO shapes, for the tremolo's shape report: the sine (shipped) and the
 * rounded triangle (before 2026-10-13), -1..1 over phase 0..1. */
float fx_test_lfo_sine(float phase);
float fx_test_lfo_rounded(float phase);
#endif

#endif
