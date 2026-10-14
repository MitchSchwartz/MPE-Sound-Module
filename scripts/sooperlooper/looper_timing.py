"""WHEN a player action takes effect. One table, one function.

**If a timing decision lives anywhere else, that is the bug.**

WHY THIS FILE EXISTS

Until now the question "does this action wait for the bar?" was answered in
five places that could not see each other:

  * `sl_grid_sync.set_grid_active` set the engine's quantize controls from
    whether a grid existed.
  * `slot_runtime._execute_slot_ops` asked whether the SESSION was sounding,
    and launched at once when it was not.
  * `loop_model.plan_gesture` read a `quantized` flag that
    `sooperlooper-apc-bench` set ONCE at startup from `MPE_SL_SYNC_MODE` and
    never updated -- the *mode*, not the *state*. `looper-transport-clock
    -spec.md` named that exact confusion as "the recurring bug shape" and the
    code kept it anyway.
  * `track_gesture.stop_all_loops` lifted the controls by hand for its burst.
  * `slot_runtime.expire_deferred` gave up after five seconds and launched
    unquantized.

Nothing was wrong with any one of them. The defect was that they were five.
When the rule "a silent session needs no count-in" was settled on 2026-08-30 it
was applied to LAUNCH, in the second of those five, and never reached the
first. So recording into a stopped session still counts in to a boundary
nobody can hear -- up to a full cycle of playing thrown away. That is
divergence D0 below, and it is the whole reason this file exists: not because
the rule was wrong, but because there was no single place to put it.

THE SHAPE, NOT THE ROWS

`when()` is a total function over `(Action, Session)`. Every action the surface
has is a row in `RULES`; a missing row raises at import rather than defaulting
to something plausible. There is no "if" chain and therefore no order to get
wrong, which is the same reason `binding_table.py` is a mapping and not a
sequence of branches.

Adding a gesture means adding a row here. It cannot be given timing anywhere
else, because nothing else is allowed to ask the question.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- what an action can be waiting for --------------------------------------

#: No wait. The action reaches the engine as fast as OSC carries it.
NOW = "now"

#: The playing loop's own wrap, counted by SooperLooper in the audio thread.
#: Sample-accurate. Preferred whenever the track is actually sounding.
TRACK_WRAP = "track wrap"

#: Our own timer against the grid's phase, used when nothing is playing for the
#: engine to wrap. Not sample-accurate, and it does not need to be: the only
#: thing it has to agree with is the audio that is about to start.
#: How long the BENCH will hold a deferred action before firing it anyway.
#:
#: Only `BY_BENCH` rows can strand: the engine's own quantize either fires or
#: the engine is gone, but our wait depends on `loop_pos` reports continuing to
#: arrive. If they stop, the wrap never comes and the pad is dead with no
#: error — worse than a late launch.
#:
#: **The magnitude is D5 and is unmeasured.** 5.0 was chosen, not derived; at
#: 40 BPM one 4-bar cycle is 24 s, so this fires long before the boundary it
#: was waiting for and the "fallback" becomes the normal path at slow tempi.
#: It lives here rather than at the call site because it is a timing decision,
#: and timing decisions are not something a call site may make for itself.
BENCH_WAIT_CAP_S: float = 5.0

#: A bar line computed from the tempo, with no audio to wrap. WHOSE timer that
#: is depends on the rule's enforcer, which is why `Moment` carries it:
#: SooperLooper's own cycle for a `BY_ENGINE` row, ours for a `BY_BENCH` one.
#: This docstring said "our own timer" flatly, which is false for RECORD_START.
GRID_BOUNDARY = "grid boundary"

LANDINGS: tuple[str, ...] = (NOW, TRACK_WRAP, GRID_BOUNDARY)


# --- who does the waiting ---------------------------------------------------
#
# Two completely different mechanisms, and confusing them is how a "deferred"
# command became indistinguishable from an "ignored" one for six weeks.

#: We send the command straight away and SooperLooper holds it until its own
#: boundary, because `quantize` / `sync` / `mute_quantized` are set. Our only
#: job is to have set those controls correctly -- see `engine_controls`.
BY_ENGINE = "engine"

#: We hold the message ourselves and send it later, because something has to
#: happen at the boundary that the engine cannot do for us -- swapping the
#: buffer with `load_loop` before the `trigger`, most of all.
BY_BENCH = "bench"

ENFORCERS: tuple[str, ...] = (BY_ENGINE, BY_BENCH)


# --- what decides whether an action waits at all ----------------------------

#: Never waits, whatever else is true. A transport or destructive action.
NEVER = "never"

#: Waits only while something is already sounding. THE LAW: quantization
#: exists to put a new sound in time with a sound already happening, so with
#: nothing sounding there is nothing to be late for.
#:
#: Mitch, 2026-08-30: "when I've stopped all and I start a clip, we've reset
#: the phase to zero ... it should also mean that start happens immediately."
#:
#: **Only the bench can enforce this gate**, and that is checked below. The
#: engine's quantize controls have no idea whether anything is sounding -- all
#: SooperLooper knows is its own cycle -- so an action we hand to the engine
#: CANNOT obey the law however much we would like it to.
WHILE_SOUNDING = "while sounding"

#: Waits whenever a grid exists, even in total silence. This is what the
#: engine's `quantize` / `mute_quantized` controls actually do, so it is the
#: only gate an engine-enforced action can have. See D0 for the cost.
WHILE_GRID = "while grid"

GATES: tuple[str, ...] = (NEVER, WHILE_SOUNDING, WHILE_GRID)


# --- the actions ------------------------------------------------------------

RECORD_START = "record start"
RECORD_CLOSE = "record close"
CLIP_STOP = "clip stop"
CLIP_LAUNCH = "clip launch"
SLOT_SWITCH = "slot switch"
# SCENE_LAUNCH / SCENE_STOP were here until 2026-09-07. They claimed "many
# launches sharing one boundary"; `slot_surface.scene_press` dispatches one
# `when()` per track, each with its own `track_sounding`, so a scene where A
# sounds and B does not lands A on the wrap and B on the grid boundary. The
# rows described behaviour the code does not have and nothing ever asked for
# them. A row nobody queries is prose, and prose belongs in the audit.
OVERDUB = "overdub"
CLIP_CLEAR = "clip clear"
STOP_ALL = "stop all"
CLEAR_ALL = "clear all"
FADER_MOVE = "fader move"

# Derived from RULES below, never typed out a second time. Written by hand it
# was a copy of the dict's keys sitting in the same file, so `_assert_total`
# compared the author with themselves and could not fail. What actually needs
# checking is that every row is REACHED — that lives in
# `tests/test_looper_timing.py::ReachabilityTests`, which reads the call sites.


@dataclass(frozen=True)
class Session:
    """What the instrument is doing, at the instant of the press.

    Three facts, and every one of them is read from the ENGINE rather than
    from our own bookkeeping. That is deliberate: a bench flag written when a
    command was *sent* disagrees with the engine for as long as the engine
    takes to answer, and every timing bug this looper has had lived in that
    window.
    """

    #: A grid has been established. Not "grid mode is configured" -- the
    #: distinction that `MPE_SL_SYNC_MODE` blurred for six weeks.
    #:
    #: `None` means the caller cannot tell. `slot_runtime` genuinely cannot: it
    #: is handed a `grid_boundary()` callable and "has no business knowing what
    #: a GridState is", so it knows whether a BOUNDARY is computable, which is
    #: a different fact. It used to pass that different fact under this name.
    #: Nothing broke only because the two rules it asks are gated on sounding
    #: and never read this field -- an accident, not a design.
    grid: bool | None
    #: ANY loop, on any track, is sounding. Not the pressed track. Asking about
    #: the pressed track made a stopped clip launch instantly while other
    #: tracks played, landing off the beat of the music it was joining.
    #:
    #: `None` means the caller genuinely cannot tell -- a per-loop gesture has
    #: no view of its peers. It is not a synonym for False. `when()` refuses a
    #: rule that needs this while it is None, so answering D0 by flipping the
    #: table cannot silently start reading a guess as an answer.
    sounding: bool | None
    #: The pressed track itself is sounding, so the engine has a wrap of its
    #: own to land on.
    track_sounding: bool = False
    #: This take is the one that will DEFINE the grid. It is always free: there
    #: is no bar to count in to yet, and quantizing it would snap it to a cycle
    #: inherited from the previous session.
    defining: bool = False


@dataclass(frozen=True)
class Moment:
    """When the action lands, and why. `why` is for the log, not decoration.

    Every deferred action in this instrument has at some point looked exactly
    like a dropped press. The reason it is waiting has to be sayable.
    """

    lands_on: str
    why: str
    #: Who holds the wait -- the engine's quantize controls, or us. A caller
    #: that must lift a control before acting needs this; without it, reading
    #: `lands_on` alone told you a boundary was coming but not whose.
    enforced_by: str | None = None

    @property
    def immediate(self) -> bool:
        return self.lands_on == NOW

    def __str__(self) -> str:
        return self.why


@dataclass(frozen=True)
class Rule:
    """One action's timing, and the reasoning that put it there."""

    gate: str
    #: Where it lands once it does wait. Ignored when the gate is NEVER.
    lands_on: str = TRACK_WRAP
    #: Who holds it until then. Ignored when the gate is NEVER.
    enforced_by: str = BY_ENGINE
    why: str = ""
    #: Set when this row knowingly departs from `WHILE_SOUNDING`, naming the
    #: divergence in `Documents/`-free terms: the reason lives here.
    divergence: str = ""

    def __post_init__(self) -> None:
        if self.gate not in GATES:
            raise ValueError(f"unknown gate {self.gate!r}")
        if self.lands_on not in LANDINGS:
            raise ValueError(f"unknown landing {self.lands_on!r}")
        if self.enforced_by not in ENFORCERS:
            raise ValueError(f"unknown enforcer {self.enforced_by!r}")
        if not self.why.strip():
            raise ValueError("a rule with no reason is a rule nobody wrote")
        if self.gate == WHILE_SOUNDING and self.enforced_by == BY_ENGINE:
            # Not a style rule. SooperLooper's quantize controls are told only
            # whether a grid exists; nothing in the OSC surface lets them ask
            # "is anything sounding right now?". So a rule that claims to obey
            # the law while handing the wait to the engine is claiming
            # something that cannot happen, and it would be believed.
            raise ValueError(
                f"gate {WHILE_SOUNDING!r} cannot be enforced by the engine: "
                "the engine does not know whether anything is sounding. Hold "
                "the command in the bench, or gate on the grid and say so."
            )


# --- the rules --------------------------------------------------------------
#
# Read this table as the answer to "what does the looper do?". It is the
# whole of §3 of the old prose contract, and it is executable.

RULES: dict[str, Rule] = {

    RECORD_START: Rule(
        gate=WHILE_GRID,
        lands_on=GRID_BOUNDARY,
        enforced_by=BY_ENGINE,
        why="a take joins the music, so it starts on a boundary",
        # ------------------------------------------------------------------
        # D0. THE ROW THAT CANNOT OBEY THE LAW AS IT STANDS.
        #
        # Launching a clip gates on WHILE_SOUNDING and so fires at once into a
        # silent session. Recording cannot, and the reason is structural
        # rather than an oversight: the wait is done by SooperLooper's
        # `quantize` control, and that control is told only whether a grid
        # exists. It has no way to ask whether anything is sounding.
        #
        # So after Stop All -- grid kept, nothing sounding -- a press arms the
        # pad and the engine records NOTHING until a boundary the player
        # cannot hear.
        #
        # MEASURED on the appliance 2026-09-07, press -> first recorded sample:
        #   no grid          0.06 s, 0.07 s
        #   3.62 s cycle     0.56 s, 0.57 s
        #   2.18 s cycle     2.27 s      <- a full cycle of playing, gone
        #
        # Reported as "the second column only records the after loop". The
        # column was never the variable.
        #
        # The fix therefore has two halves, and the invariant above will not
        # let one ship without the other:
        #   gate=WHILE_SOUNDING, enforced_by=BY_BENCH
        # and the bench then lifts `quantize` for a record into silence, the
        # way `stop_all_loops` already lifts it for its burst. Not made here:
        # it changes playing feel, which is Mitch's call and not mine.
        # ------------------------------------------------------------------
        divergence="D0: gated on the grid, not on whether anything is "
                   "sounding, because the engine holds the wait and the "
                   "engine cannot know. Silent sessions still count in.",
    ),

    RECORD_CLOSE: Rule(
        gate=WHILE_GRID,
        lands_on=TRACK_WRAP,
        enforced_by=BY_ENGINE,
        why="the take lands exactly one cycle long",
        # Not a divergence. A take being closed is itself sounding, by
        # definition -- it is recording -- so WHILE_GRID and WHILE_SOUNDING
        # cannot disagree here. It is written WHILE_GRID because the grid is
        # what the length is snapped to.
    ),

    CLIP_STOP: Rule(
        gate=WHILE_GRID,
        lands_on=TRACK_WRAP,
        enforced_by=BY_ENGINE,
        why="a per-clip stop is a musical edit, so it lands on the beat",
        # Gated on the grid rather than on the law, because `mute_quantized`
        # is what does the waiting and it only knows about the grid. No
        # divergence follows: you only ever stop a clip that is sounding, so
        # the two gates cannot disagree here. Record is the case where they
        # can, and do.
    ),

    CLIP_LAUNCH: Rule(
        gate=WHILE_SOUNDING,
        lands_on=TRACK_WRAP,
        enforced_by=BY_BENCH,
        why="a clip joins what is already playing",
        divergence=(
            "D5: the bench gives up after BENCH_WAIT_CAP_S and fires anyway. "
            "The cap is unmeasured, and below ~75 BPM a 4-bar cycle is longer "
            "than the cap, so the fallback becomes the normal path."
        ),
        # The law, in the one place it was ever applied. With nothing
        # sounding this returns NOW and the clip becomes the phase reference:
        # you cannot be late for something that has not started, and waiting
        # would sit in silence for a whole cycle before the first note.
    ),

    SLOT_SWITCH: Rule(
        gate=WHILE_SOUNDING,
        lands_on=TRACK_WRAP,
        enforced_by=BY_BENCH,
        why="the outgoing clip stops and the incoming starts on one boundary",
    ),

    OVERDUB: Rule(
        gate=NEVER,
        why="SooperLooper toggles overdub at once even under quantize",
        # MEASURED 2026-09-07, tests/engine. Not our choice: holding the
        # overdub ourselves would put our own timing in the audio path, which
        # is the one thing the control layer must never do.
        divergence="D4: the engine imposes this, we do not choose it.",
    ),

    CLIP_CLEAR: Rule(
        gate=NEVER,
        why="you asked for it gone; watching it play on is worse than a seam",
        divergence="D2: destructive, so immediate on purpose.",
    ),

    STOP_ALL: Rule(
        gate=NEVER,
        why="a transport action. You want silence now, not at the end of the bar",
        # MEASURED 2026-09-07: 20-21 ms to silence at all five bar phases,
        # against a 2003 ms bar. The delay a player feels is NOT this -- it is
        # that Shift+StopAll acts on the chord's RELEASE, because the same
        # chord held three seconds means CLEAR_ALL. That is a binding_table
        # question, not a timing one, and it is deliberately not modelled here.
        divergence="D1: immediate, but dispatched on release. See binding_table.",
    ),

    CLEAR_ALL: Rule(
        gate=NEVER,
        why="destructive and total; quantizing it would be theatre",
        divergence="D3: destructive, so immediate on purpose.",
    ),

    FADER_MOVE: Rule(
        gate=NEVER,
        why="a level is not an event; there is no boundary for it to land on",
    ),
}


# --- totality ---------------------------------------------------------------

def _assert_well_formed() -> None:
    """Every row is a real Rule under a real name.

    This used to diff `ACTIONS` against `RULES` — two literals in this file,
    written by the same hand, so it compared the author with themselves and
    could not fail. `ACTIONS` is now derived from `RULES`, which makes that
    check a tautology; the question worth asking is whether a row is ever
    REACHED, and that needs the call sites, not this file. See
    `tests/test_looper_timing.py::ReachabilityTests`.
    """
    for action, rule in RULES.items():
        if not isinstance(action, str) or not action.strip():
            raise ValueError(f"unnamed timing action: {action!r}")
        if not isinstance(rule, Rule):
            raise ValueError(
                f"{action!r} maps to {type(rule).__name__}, not a Rule"
            )


_assert_well_formed()

#: Every action that has a rule. Derived — never typed out a second time.
ACTIONS: tuple[str, ...] = tuple(RULES)


# --- the one question -------------------------------------------------------

def when(action: str, session: Session) -> Moment:
    """When does `action` take effect, given what the instrument is doing?

    The only way to find out. Nothing else in this repository is permitted to
    decide, and `tests/test_looper_timing.py` checks that nothing does.
    """
    try:
        rule = RULES[action]
    except KeyError:
        raise ValueError(
            f"no timing rule for {action!r}. Add a row to RULES; do not "
            f"decide it at the call site."
        ) from None

    if rule.gate == NEVER:
        return Moment(NOW, rule.why, rule.enforced_by)

    # The defining take is always free. There is no grid yet by definition, so
    # this is belt and braces against a caller that says otherwise -- and it
    # has been needed: a stale grid from the previous session once stretched a
    # short first take to an imaginary bar.
    if session.defining:
        return Moment(NOW, "defining the grid — free-form, no count-in",
                      rule.enforced_by)

    if rule.gate == WHILE_GRID:
        if session.grid is None:
            raise ValueError(
                f"{action!r} is gated on {WHILE_GRID!r}, and this caller does "
                "not know whether a grid is established. A computable BOUNDARY "
                "is a different fact -- do not pass one under this name."
            )
        if not session.grid:
            return Moment(NOW, "no grid yet — nothing to wait for",
                          rule.enforced_by)

    if rule.gate == WHILE_SOUNDING:
        if session.sounding is None:
            raise ValueError(
                f"{action!r} is gated on {WHILE_SOUNDING!r}, and this caller "
                "does not know whether anything is sounding. Give the call "
                "site a real answer -- a per-loop gesture has to be handed "
                "one, it cannot see its peers. Guessing False here would make "
                "every press immediate."
            )
        if not session.sounding:
            return Moment(NOW, "nothing is sounding — nothing to be late for",
                          rule.enforced_by)

    # It waits. Land on the track's own wrap when the engine has one to give,
    # because that is sample-accurate and our timer is not.
    if rule.lands_on == TRACK_WRAP and not session.track_sounding:
        return Moment(GRID_BOUNDARY, f"{rule.why} (nothing playing to sync to)",
                      rule.enforced_by)
    return Moment(rule.lands_on, rule.why, rule.enforced_by)


def engine_controls(*, grid: bool) -> dict[str, float]:
    """The engine-side half of the same rules, as controls to send per loop.

    These four ARE quantization as far as SooperLooper is concerned, and they
    are derived here rather than written out in `sl_grid_sync` so that the
    engine's half and the bench's half of one rule cannot drift apart. They
    did: `set_grid_active` keyed off "a grid exists" while `slot_runtime`
    keyed off "something is sounding", and neither knew the other existed.

      quantize        1 == QUANT_CYCLE. Makes `record` wait, and snaps the
                      take's length to exactly one cycle.
      sync            counts the take in to the boundary.
      mute_quantized  makes `mute_on` (stop) and `trigger` (launch) wait.
      round           ALWAYS 0. Rounding on top of an already quantized stop
                      adds another whole cycle to the take.

    A caller that wants a burst to be immediate -- Stop All -- clears these,
    acts, and restores them. It must not invent its own values, so this is
    also what the restore uses.
    """
    quantizing = [
        a for a, r in RULES.items()
        if r.gate != NEVER and r.enforced_by == BY_ENGINE
    ]
    if not quantizing:                      # pragma: no cover - guard
        raise ValueError("no engine-enforced rule; the controls would be dead")
    on = 1.0 if grid else 0.0
    return {"quantize": on, "sync": on, "mute_quantized": on, "round": 0.0}



def send_engine_controls(
    send, *, grid: bool, prefix: str = "/sl/-1/set"
) -> None:
    """Send the WHOLE control set. The only sanctioned way to write them.

    Three call sites used to lift their own subset for an immediate burst:
    Stop All sent all four, `looper_songs.stop_playback` sent `mute_quantized`
    alone, and `apply_freeform` sent three and never `mute_quantized`. Each is
    defensible read on its own, and together they meant "make this immediate"
    had three different meanings — the same shape as the bug where two files
    restored `mute_quantized` from different premises.

    Indexing one key out of `engine_controls()` is the thing this replaces. If
    a caller genuinely needs a subset, that is a rule, and rules live in
    `RULES` — not in a subscript at the call site.
    """
    for control, value in engine_controls(grid=grid).items():
        send(prefix, [control, value])


def divergences() -> dict[str, str]:
    """Rows that knowingly depart from the law, for the boot banner and tests.

    Kept as a function over `RULES` rather than a second list, so a divergence
    cannot outlive the rule that caused it.
    """
    return {a: r.divergence for a, r in RULES.items() if r.divergence}
