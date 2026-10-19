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
 * Stage gain. Before anything else, the input is multiplied by one linear
 * gain, the per-patch normalization (patch_browser/patch_normalization.py
 * decides it; this applies what it is told):
 *
 *   x = s * in,   s one-pole smoothed (FX_GAIN_MS) toward the commanded gain
 *
 * Here, at the input, and not after the chain: the effects and every loop
 * input see the normalized level, as they did when Surge's own volume did the
 * normalizing, and the drive is driven by it. Unity (the startup state) is
 * the input bit for bit, so an insert nobody configures stays transparent.
 * The commanded gain is clamped to FX_GAIN_MAX_DB; NaN, inf and negative
 * values are ignored. Everything else on this page (bypass, the level curves,
 * the makeup, the bounds the self-test holds) is about the stage's OUTPUT:
 * the effects add at most the makeup over what the stage hands them. The
 * stage itself applies whatever gain it is told, up to FX_GAIN_MAX_DB, and
 * the clamp to ±1.0 stays the backstop. Per stereo frame: nothing at a
 * settled unity, 2 multiplies settled elsewhere, 4 more while it moves.
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
    /* The bass bypass's level correction (fx_dsp.h, "Bass bypass"): the
     * gain its high band needs, as a loss in dB at its full mix, with the
     * split at each of FX_BASS_GRID_HZ. NULL: none. Last, so a table entry that names
     * no correction gets none. */
    const float *bass_loss;
} fx_effect_desc;

/* Points of the per-effect level table over depth 0..1. 80, not 64, so that
 * the drive's and the rotary's fade-ins (mix_full_at 0.05 and 0.1) end on a
 * point: the level curve has a corner there, and at 64 points the linear
 * interpolation cut across it, +0.3 dB at the rotary's 0.1. */
#define FX_LEVEL_POINTS 80

/* The chain's makeup at full depth, in dB (see "Makeup" above). The one place
 * it is set: the self-test derives its bounds from it. */
#define FX_MAKEUP_DB 1.0

/* The stage gain (see "Stage gain" above). The most it will apply: a
 * commanded gain above it is held to it. The real limit is the sender's
 * ceiling (the calibrated peak); this only bounds a wrong sender.
 * patch_browser/fx_host_protocol.py mirrors it (GAIN_MAX_DB), pinned by
 * tests/test_fx_host.py. */
#define FX_GAIN_MAX_DB 48.0
/* Its smoothing time constant: a fader drag or a 30 dB step on a patch change
 * is a ramp, not a zipper or a click. */
#define FX_GAIN_MS 25.0
/* Below this distance the gain lands on its target rather than approaching
 * forever, so a return to unity is unity, bit for bit. */
#define FX_GAIN_SNAP 1e-6f

/* Bass bypass (#173). Below a crossover the signal skips the chain: a
 * Linkwitz-Riley 4th-order split (two cascaded Butterworth sections per band,
 * TPT state-variable filters, so a moving corner stays stable) sends the low
 * band (dry, per channel or summed to mono: FX_BASS_LOW_MONO) straight to the
 * output, and the high band through the chain. LR4's two bands sum to an allpass: flat in level, so with the chain
 * transparent the split changes the phase of the lows and nothing else.
 *
 *   out = G(m) * ( b * lo + chain( x + b * (hi - x) ) )
 *
 * b runs 0 -> 1 over the enable crossfade time when the split comes in (the
 * chain goes live with the crossover set, or the crossover is set while it is
 * live) and back when it goes, so neither is a step. The crossover glides
 * (one-pole, FX_BASS_GLIDE_MS, coefficients once per FX_CHUNK) rather than
 * jumping, and b only moves with the corner parked at FX_BASS_PARK_HZ (in:
 * fade, then glide up; out: glide down, then fade: ~0.25 s). b = 0 is the chain exactly as before: Off, and the whole bypass
 * (depth 0 or no effect on) still return the input bit for bit, because the
 * split only runs while the chain is live, or while b fades out after it.
 * The makeup applies to the sum, so the level on keys stays G(m) x the input.
 *
 * Per stereo frame while the split runs: five SVFs (~11 ops each with the
 * flush), the mono sum and the fade, ~70 ops. While bypassed with an effect
 * ticked the filters keep running (no output), so the knob coming up from 0
 * finds them warm; with no effect ticked, nothing runs. */

/* Level. LR4's bands sum flat only while they are coherent. An effect
 * decorrelates the high band from the dry lows (delays, sweeps, a rotor), and
 * in the octave around the crossover the two then add in power: down to -3 dB
 * there. On the keys that octave is most of the signal once the crossover
 * reaches 200-300 Hz: measured at 300 Hz, full depth, the rotary -1.9 dB, the
 * phaser and the flanger -1.0. So each effect carries its loss over the
 * crossover (`bass_loss`, fitted on the keys like the level curve, fx_dsp.c)
 * and the stage gives it back on the high band only:
 *
 *   h_k = 1 + (H_k(fc) - 1) * b * c_k * w_k / wet_max_k,   H_k = 10 ^ (-loss_k(fc) / 20)
 *
 * so it scales with the stage's mix and fades with the split, and the lows
 * stay at their dry level. Applied once, after the chain, as the largest
 * stage's (the same rule as the makeup): the loss comes from the high band
 * no longer matching the lows, and two decorrelating stages lose barely more
 * than one, so a correction per stage overshot a chain by 1 dB. A correction
 * on the high band alone has to be larger than the loss it gives back (the
 * lows are part of the level), so the tables are fitted, not measured:
 * fx-selftest --fit-bass K. H_k is looked up once per chunk (a pow per stage
 * while the crossover glides). */
#define FX_BASS_GRID 8
extern const float FX_BASS_GRID_HZ[FX_BASS_GRID];

/* 1: the drive is inside the bypass: the split comes before it, so the lows
 * stay clean as well as dry. 0: the drive runs on the whole signal
 * and the split comes after it (the lows are driven, then kept dry of
 * the modulation effects). The one place this is decided. */
#define FX_BASS_DRIVE_INSIDE 1
/* The crossover range the client accepts; 0 (or below) is Off. The UI offers
 * Off and 80-300 Hz (patch_browser/fx_host_protocol.py). */
#define FX_BASS_MIN_HZ 20.0f
#define FX_BASS_MAX_HZ 1000.0f
/* How fast a moved crossover glides to its new value. 100 ms, was 50: a
 * corner moving under a tone near it modulates the band sum while it moves
 * (the bands sum to an allpass only at rest); gliding up from the park past
 * an 84 Hz tone, 50 ms dipped it 1.6 dB, 100 ms 0.9 (fx_selftest.c, the fade
 * check). */
#define FX_BASS_GLIDE_MS 100.0
/* Where the corner parks while the split fades in or out (review of #185):
 * mid-fade the dry lows and the band-split lows are mixed, and near the
 * corner LR4's allpass has them half a turn apart, so at the corner they
 * cancelled (the low band dipped up to -13 dB for ~30 ms). The split now
 * fades in with the corner here, below the keyboard, then glides up to the
 * target; going out it glides down here first, then fades. Parked counts as
 * within FX_BASS_PARKED of it. */
#define FX_BASS_PARK_HZ 10.0f
#define FX_BASS_PARKED (1.2f * FX_BASS_PARK_HZ)
/* 1: the low band is summed to mono (both channels get (L + R) / 2 below the
 * corner). 0: each channel keeps its own low band, dry. Mono loses the bass
 * of a stereo patch: -3 dB where L and R are uncorrelated, and it cancels
 * where they are opposed (review of #185); per channel keeps every low as it
 * was. Pending Mitch's choice; 0 until then. */
#ifndef FX_BASS_LOW_MONO
#define FX_BASS_LOW_MONO 0
#endif

typedef struct {
    float ic1, ic2;
} fx_svf;

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

    /* The bass bypass (see above). */
    float bass_target;  /* Hz, 0 = Off: fx_engine_set_bass */
    float bass_hz;      /* the crossover in force, gliding toward the target */
    float bass_mix;     /* b, 0..1 */
    float bass_glide;   /* one-pole coefficient per FX_CHUNK */
    float bass_a1, bass_a2, bass_a3;   /* the SVF coefficients at bass_hz */
    int bass_warm;      /* the filters ran last chunk */
    int bass_split_at;  /* the stage (index into order) the split comes before */
    float bass_corr[FX_MAX_EFFECTS];   /* H_k at bass_hz */
    fx_svf bass_first[FX_CHANNELS];    /* per channel: LP and HP section 1 */
    fx_svf bass_lo;                    /* mono: LP section 2 */
    fx_svf bass_lo_r;                  /* unused but by a self-test mutant */
    fx_svf bass_hi[FX_CHANNELS];       /* per channel: HP section 2 */

    /* The stage gain (see "Stage gain" above), linear. */
    float gain_target;  /* fx_engine_set_gain; 1.0 at init */
    float gain;         /* in force, ramping toward the target */
    float gain_alpha;   /* one-pole coefficient per frame; 0 means "jump" */
    float gain_max;     /* FX_GAIN_MAX_DB, linear */
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

/* The crossover the next period plays toward: Hz, or 0 (or anything not a
 * positive number) for Off; clamped to FX_BASS_MIN_HZ..FX_BASS_MAX_HZ. RT-safe:
 * it only stores. */
void fx_engine_set_bass(fx_engine *e, float hz);

/* The stage gain the next period ramps toward, linear (1.0 is unity). NaN,
 * inf and negative values are ignored (the target stays as it was); anything
 * above FX_GAIN_MAX_DB is held to it. RT-safe: it only stores. */
void fx_engine_set_gain(fx_engine *e, float linear);

/* One line of ASCII per datagram:
 *   `chainl <mask> <depth> <l0> <l1> ...`  the enabled effects, the shared
 *                            depth, and a level per effect in effect-id order
 *   `chain <mask> <depth>`   the same with every level 1.0
 *   `fx <effect id> <depth>` the first protocol: means chain <1 << id> <depth>
 *   `bass <hz>`              the bass bypass's crossover, 0 for Off: parsed
 *                            by fx_parse_bass below, not here
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

/* `bass <hz>`: the bass bypass's crossover, a separate datagram so that every
 * older message keeps its meaning and an older client ignores this one (it
 * reads as malformed there, which changes nothing). 0 is Off. Returns 1 and
 * fills `hz` (finite, >= 0, unclamped) on a well-formed message, 0 otherwise. */
int fx_parse_bass(const char *text, float *hz);

/* `gain <linear>`: the stage gain (see "Stage gain" above), a datagram of its
 * own for the reason `bass` is one. A plain decimal: no sign, exponent, hex,
 * NaN or inf. Returns 1 and fills `linear` (finite, >= 0, unclamped: the
 * engine clamps) on a well-formed message, 0 otherwise. */
int fx_parse_gain(const char *text, float *linear);

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
    FX_MUT_ROTARY_NO_DIRECTIVITY,   /* the horn as bright facing away as facing the microphone */
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
/* The drive's and the auto-wah's top thirds off (2026-10-15): both play their
 * round-before law at every depth, so the shaper and band-pass checks keep
 * measuring the voicing they were calibrated on, and the top-third checks
 * have a control. */
extern int fx_test_no_top;
extern int fx_test_level_hot;
extern int fx_test_level_cold;
extern int fx_test_level_wobble;
/* The chain run in reverse order (the order check must catch it). */
extern int fx_test_reverse_order;
/* The bass bypass broken: the split run even at depth 0 (the bit-identity
 * checks must catch it; the flat-sum check uses it on purpose, to split and
 * recombine with the chain bypassed), a Linkwitz-Riley 2 split in place of
 * LR4 (one section per band: its sum has a notch, the flat-sum check must
 * catch it), the fade and the glide instant (the click check must), the lows
 * left in stereo (the mono check must), and the drive moved outside the
 * split whatever FX_BASS_DRIVE_INSIDE says (read at init). */
extern int fx_test_bass_split_at_depth0;
extern int fx_test_bass_lr2;
extern int fx_test_bass_unsmoothed;
extern int fx_test_bass_stereo;      /* the other FX_BASS_LOW_MONO option */
extern int fx_test_bass_low_left;    /* mutant: both channels' lows from L only */
extern int fx_test_bass_no_park;     /* mutant: fade at the target corner (pre-review) */
extern int fx_test_bass_drive_outside;
/* The level correction off (the bass level check's control), and a table to
 * use in place of an effect's own (fx-selftest --fit-bass). */
extern int fx_test_bass_no_corr;
extern const float *fx_test_bass_fit[FX_MAX_EFFECTS];
/* The makeup broken: off (the lower bound must catch it), and following the
 * targets instead of the smoothed depths and enable ramps (the click checks
 * must). */
extern int fx_test_no_makeup;
extern int fx_test_makeup_unsmoothed;
/* The stage gain broken: not applied at all, a step instead of the ramp,
 * after the chain instead of before it, its FX_GAIN_MAX_DB clamp gone,
 * applied twice (an addition, not a deletion: the level check must catch a
 * stage that does too much), and the ramp with no landing, so it stalls a
 * float's width short of its target (the unity check must catch it). */
extern int fx_test_gain_off;
extern int fx_test_gain_no_ramp;
extern int fx_test_gain_post_chain;
extern int fx_test_gain_unclamped;
extern int fx_test_gain_twice;
extern int fx_test_gain_no_snap;
/* A rotary state's rotor speed in Hz (rotor 0 the horn, 1 the drum), for the
 * inertia check: the state is private to fx_dsp.c. */
float fx_test_rotary_speed(const void *state, int rotor);
/* The LFO shapes, for the tremolo's shape report: the sine (shipped) and the
 * rounded triangle (before 2026-10-13), -1..1 over phase 0..1. */
float fx_test_lfo_sine(float phase);
float fx_test_lfo_rounded(float phase);
#endif

#endif
