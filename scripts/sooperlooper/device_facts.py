"""What we know about the hardware, and how we know it.

WHY THIS FILE EXISTS

On 2026-08-29 Mitch reported that every button on the APC has an LED. The
codebase asserted the opposite in five separate docstrings — "Shift has no
LED", "the side buttons are single-colour" — and had done since 618a19e. That
claim was never measured. It came from a vendor protocol document, was copied
forward five times, and each copy read as established fact to the next person
who touched it. I then used it to tell Mitch that what he was asking for was
physically impossible. Twice.

He had told me otherwise several sessions earlier. That statement lived in a
conversation and nowhere else, so it was gone by the next context window, while
the wrong fact was in the repo being read aloud by five files.

That is the actual defect. Not the LED code — the fact that a claim about
physical hardware had no home, no provenance, and no way to be wrong.

THE RULES

1. A fact about the device is recorded HERE, once. Other modules cite its id in
   a comment. They do not restate it — five restatements is how this happened.

2. Every fact carries HOW IT WAS ESTABLISHED, in tiers:

       MEASURED   observed on this appliance, with a date
       OWNER      Mitch stated it about his own hardware
       VENDOR     read from a manufacturer document
       INFERRED   reasoned from another fact

3. MEASURED and OWNER outrank VENDOR and INFERRED. Always. When they conflict,
   the lower tier is WRONG until re-measured — it does not get to stand because
   it is written more confidently.

4. A VENDOR or INFERRED fact may never be used to tell Mitch that something is
   impossible. "The document says green-only" is not "your device cannot do
   this." If it matters enough to refuse a request over, it matters enough to
   measure. This rule exists because breaking it is exactly what happened.

5. A contradiction is an EVENT. When an observation disagrees with a fact here,
   the old claim is superseded in place with both dates visible — never quietly
   overwritten. The history of being wrong is the useful part.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MEASURED = "measured"
OWNER = "owner"
VENDOR = "vendor"
INFERRED = "inferred"

#: Tiers that may be used to refuse a request as physically impossible.
AUTHORITATIVE = frozenset({MEASURED, OWNER})


@dataclass(frozen=True)
class Fact:
    id: str
    claim: str
    tier: str
    established: str          # ISO date
    source: str
    supersedes: str = ""      # what this replaced, and why it was wrong

    @property
    def authoritative(self) -> bool:
        return self.tier in AUTHORITATIVE

    def refuse_with(self) -> None:
        """Raise if this fact is about to be used to say 'impossible'."""
        if not self.authoritative:
            raise NotMeasured(
                f"{self.id} is {self.tier}, established {self.established} "
                f"({self.source}). A {self.tier} fact cannot be used to call "
                f"something impossible — measure it first. See rule 4."
            )


class NotMeasured(Exception):
    """A non-authoritative fact was used to refuse a request."""


FACTS: dict[str, Fact] = {}


def record(fact: Fact) -> Fact:
    FACTS[fact.id] = fact
    return fact


# --- the panel --------------------------------------------------------------

record(Fact(
    id="apc.buttons.all_have_leds",
    claim="Every button on the APC mini has an LED, Shift included.",
    tier=OWNER,
    established="2026-08-29",
    source="Mitch, directly: 'all buttons have led ... I even specified that' "
           "— restating something he had already told me in an earlier session",
    supersedes="apc.shift.no_led (VENDOR, 618a19e) — asserted in five "
               "docstrings, never measured, and used twice on 2026-08-29 to "
               "tell Mitch a request was impossible",
))

record(Fact(
    id="apc.scene.led_observed",
    claim="On channel 0 (0x90), velocities 0/1/2/3/5/13/21/127 on the scene "
          "buttons give: 0 = off, 2 = blinking green, everything else = solid "
          "green. The grid's RGB palette indices (13 yellow, 21 green) are not "
          "honoured — they are just green.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe round 1: all eight scene buttons lit at once with different "
           "velocities, read off by Mitch top to bottom",
))

record(Fact(
    id="apc.track.led_observed",
    claim="Same paint on the track row gives the same pattern in RED.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe round 1, read off left to right",
))

record(Fact(
    id="apc.buttons.channel_response",
    claim="Button LEDs respond on channel 0 (0x90) and on NOTHING ELSE. All "
          "sixteen channels were painted at once, one per button: only the "
          "button on 0x90 lit. The grid's channel scheme (0x96 brightness, "
          "0x9D blink) does not carry over — those channels are simply dark. "
          "The channel axis is therefore EXHAUSTED, not sampled.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe rounds 2 and 3. Round 3 put channels 0x90-0x97 on the scene "
           "column and 0x98-0x9F on the track row, velocity 1 throughout; "
           "Mitch: 'scene 1 green, all others off'",
))

record(Fact(
    id="apc.buttons.single_colour",
    claim="CLOSED, as a bounded negative: no addressing scheme we have tried "
          "produces any colour other than green (scene) and red (track). The "
          "buttons have exactly three states each — off, on, blink (velocity "
          "2). All colour-carrying UI must therefore live on the 8x8 grid.\n\n"
          "The space that was tried, so the next person knows what is left:\n"
          "  * CHANNEL — exhausted. All 16 painted at once; only 0x90 lights.\n"
          "  * VELOCITY — swept 0..127 in steps of 8, plus 1/2/3/5/13/21/127. "
          "Only off / on / blink. No palette regions, no brightness change.\n"
          "  * SYSEX RGB — the documented mk2 direct-colour message. Rejected "
          "by the buttons.\n\n"
          "This is stated as 'nothing we tried works', NOT as 'the hardware "
          "cannot'. Those differ, and the difference is Mitch's point.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe rounds 1-5 on the appliance, read off the device by Mitch",
))

record(Fact(
    id="apc.probe.positive_control",
    claim="The round-5 SysEx probe carried a POSITIVE CONTROL: the identical "
          "message, from the same code, aimed at grid pads 0-7, which are "
          "known RGB. The grid turned blue and the buttons stayed dark. That "
          "is what makes round 5 evidence about the hardware rather than "
          "evidence about whether I wrote the message correctly.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe round 5; Mitch: 'first row all blue, all others off'. Every "
           "earlier 'it cannot do that' on this panel was a negative result "
           "with NO control, which is how a vendor PDF passed as measurement.",
))

record(Fact(
    id="apc.shift.led",
    claim="OPEN, and unresolved AGAINST expectation. Shift (mk2 0x7A) did not "
          "light on any of the 16 channels, at velocity 1 or 127, and nothing "
          "appeared on the unused notes 0x78-0x7F or 0x62 either. Mitch states "
          "every button has an LED and he owns the device, so this is NOT "
          "closed as 'no LED' — the live hypothesis is that Shift's lamp is "
          "firmware-owned and not addressable over MIDI at all. Do not build "
          "anything that depends on lighting it, and do not tell Mitch it has "
          "no LED.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe rounds 1-3, negative throughout",
))

record(Fact(
    id="apc.grid.mk2_encoding",
    claim="mk2 grid pads are RGB: MIDI channel selects brightness/behaviour "
          "(0x96 solid 100%, 0x9D blink), velocity indexes a 128-colour "
          "palette. 0x90 is 10% brightness and reads as unlit in daylight.",
    tier=MEASURED,
    established="2026-08-28",
    source="observed on the appliance after 4efec97 — 'pads barely light up, "
           "and I'm seeing blue' was the symptom that produced this",
))

record(Fact(
    id="apc.scene_column.bottom_is_0x59",
    claim="mk1 right-hand column runs 0x52 at the TOP to 0x59 at the BOTTOM.",
    tier=MEASURED,
    established="2026-08-27",
    source="direct observation of which physical button sends 0x59, after "
           "three wrong answers reached by reasoning (see apc_panel.py)",
))

record(Fact(
    id="apc.buttons.note_sets",
    claim="Which notes address which physical buttons — ON THE MK2, which is "
          "the only unit the probe ran against. The eight right-hand SCENE "
          "buttons are 0x70-0x77, top to bottom. The bottom TRACK row is "
          "0x64-0x6B, left to right. Shift is 0x7A.\n\n"
          "Says NOTHING about mk1. The probe's mk1 arm has never executed; no "
          "mk1 has been attached since it was written. For mk1 the scene "
          "column rests on apc.scene_column.bottom_is_0x59 and Shift on the "
          "SP6/SP8 aseqdump captures — and the mk1 TRACK ROW rests on nothing "
          "at any tier, with apc_panel and apc_transport holding contradictory "
          "claims about button 8 (0x6B vs 0x37).\n\n"
          "Recorded separately because apc.scene.led_observed and "
          "apc.track.led_observed say 'the scene buttons' and 'the track row' "
          "without naming a note or a variant. On a two-variant surface the "
          "control identity IS part of the provenance: the only way to learn "
          "what 'the track row' meant was to read a hardcoded range in the "
          "probe's source, which was itself an uncited note literal.",
    tier=MEASURED,
    established="2026-08-29",
    source="probe rounds 1-3 (scripts/probe-apc-buttons.py) sent to exactly "
           "these notes on the attached mk2 and Mitch read the lit buttons off "
           "the panel — 'scene 1 green, all others off', and the scene column "
           "top to bottom. Note set re-read from the probe source 2026-08-30.",
))

record(Fact(
    id="apc.bank_arrows.notes",
    claim="OPEN. What the four bank arrows send is NOT established on either "
          "variant.\n\n"
          "mk1: recalled as 0x40-0x43. Unverified — no capture, no probe. It "
          "collides with nothing, so it is carried as a live claim and "
          "labelled VENDOR wherever it is used.\n\n"
          "mk2: recalled as 0x70-0x73, and that recall is REFUTED. Those four "
          "notes are scene buttons 1-4 (apc.buttons.note_sets, MEASURED "
          "2026-08-29), so they cannot also be the arrows. The consequence ran "
          "in production: the bench's scene branch claimed the note and "
          "`continue`d forty-five lines before handle_arrow was reached, so "
          "banking was dead and tracks 9-15 of 15 were unreachable from the "
          "surface, while every session start printed a banner advertising "
          "the feature. The mk2 arrow notes are therefore recorded as UNKNOWN "
          "rather than replaced with another guess.\n\n"
          "This fact is VENDOR on purpose. Rule 4: it may not be used to tell "
          "Mitch the arrows cannot work. It says we do not know their notes.",
    tier=VENDOR,
    established="2026-08-30",
    source="recall, origin unknown, flagged UNVERIFIED in apc_transport.py "
           "since it was written and in scripts/sooperlooper/README.md. The "
           "mk2 refutation is the collision with apc.buttons.note_sets, run "
           "against the live resolvers 2026-08-30.",
    supersedes="apc_transport.ARROW_NOTES_MK2 = (0x70,0x71,0x72,0x73), which "
               "was never a fact here at all — it was a tuple with a warning "
               "comment, which is how an unmeasured guess stayed load-bearing "
               "for a shipped feature without ever passing rule 4",
))

record(Fact(
    id="apc.faders.ccs",
    claim="OPEN. The nine faders are believed to send CC 48-55 (left to "
          "right) and CC 56 (master), identically on both variants. Never "
          "confirmed against hardware on either.\n\n"
          "Recorded because the failure is silent: a wrong CC makes "
          "fader_for_cc return None, handle_cc returns without a word, and "
          "the result is indistinguishable from a fader nobody touched. Same "
          "shape as the arrows, and the same instrument closes it.",
    tier=VENDOR,
    established="2026-08-30",
    source="recall, flagged as unconfirmed in apc_faders.py since it was "
           "written and in scripts/sooperlooper/README.md",
))


#: How the two OPEN questions above get closed. Both need the same five
#: minutes at the device and neither can be closed from here:
#:
#:     mpe looper stop-session
#:     python3 scripts/sooperlooper-apc-bench.py --dump-midi
#:     press Up, Down, Left, Right; then move each fader left to right
#:
#: then record the notes and CCs here at MEASURED with the date, and put them
#: in control_registry.CONTROLS. Until then both stay VENDOR, which is what
#: stops either being used to call anything impossible.
RESOLUTION_PATH_UNMEASURED_CONTROLS = ("apc.bank_arrows.notes", "apc.faders.ccs")


def fact(fact_id: str) -> Fact:
    return FACTS[fact_id]


def unmeasured() -> list[Fact]:
    """Everything still resting on a document or an inference.

    This list is the work queue for the capability probe, and it is meant to be
    read before promising Mitch anything about the panel.
    """
    return sorted(
        (f for f in FACTS.values() if not f.authoritative),
        key=lambda f: f.id,
    )
