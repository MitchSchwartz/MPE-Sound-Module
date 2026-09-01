"""The card-identity predicate — the boot gate that decides whether to wait for a DAC.

REGRESSION THIS PINS (2026-08-30 → 2026-09-01, appliance silent).

`snd-dummy` became the Pi 5 idle sink and is loaded at sysinit by
config/modules-load.d/mpe-idle-sink.conf — long before USB enumeration finishes.
Five separate hand-maintained "which cards are virtual" lists existed across four
files; only two learned about Dummy. One of the three that did not was
`mpe_physical_playback_card_present`, which gates the bounded DAC-enumeration wait
in jackd-prestart.sh.

So on every cold boot the gate saw Dummy, called it a real card, skipped the wait,
detection ran before the DAC had enumerated, tier 3 matched Dummy, and jackd bound
the one device on the appliance that is inaudible by construction. systemd green,
`mpe jack status` green, xruns 0, no sound.

Each behavioural test here is paired with a NEGATIVE CONTROL that restores the old
predicate and asserts the test FAILS — per AGENTS.md Rule -1, an assertion that
cannot fail is not evidence.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_ENGINE_SH = REPO_ROOT / "scripts" / "lib" / "audio-engine.sh"

# The Pi 5 as it actually boots: idle sink up, USB DAC not yet enumerated.
CARDS_IDLE_SINK_ONLY = """\
 8 [Dummy          ]: Dummy - Dummy
                      Dummy 1
"""

# Same moment on a unit with a display attached — still nothing audible.
CARDS_HDMI_AND_IDLE_SINK = """\
 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 8 [Dummy          ]: Dummy - Dummy
                      Dummy 1
"""

# A second later: the DAC has enumerated.
CARDS_DAC_AND_IDLE_SINK = """\
 1 [Play3          ]: USB-Audio - Sound Blaster Play! 3
                      Creative Technology Sound Blaster Play! 3 at usb-xhci-hcd
 8 [Dummy          ]: Dummy - Dummy
                      Dummy 1
"""

# The Pi 4 idle sink is a REAL, audible output and must not be called virtual.
CARDS_PI4_HEADPHONES = """\
 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
"""

# The old predicate, before the fix. Used only by the negative controls.
_OLD_PREDICATE = r"""
mpe_physical_playback_card_present() {
    local cards_file="${MPE_ASOUND_CARDS:-/proc/asound/cards}"
    [ -r "$cards_file" ] || return 1
    grep -E '^[[:space:]]*[0-9]+[[:space:]]*\[' "$cards_file" 2>/dev/null \
        | grep -viE 'Loopback|vc4hdmi|UAC2' \
        | grep -q .
}
"""


def _run_bash(snippet: str, cards: str | None = None, *, old_predicate: bool = False):
    """Run a snippet with audio-engine.sh sourced. Returns CompletedProcess."""
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["MPE_MODULE_REPO"] = str(REPO_ROOT)
        env["MPE_RUN_DIR"] = tmp
        env["MPE_JACK_STATE_FILE"] = f"{tmp}/jack.state"
        env["MPE_ENGINE_STATE_FILE"] = f"{tmp}/engine.state"
        if cards is not None:
            cards_path = Path(tmp) / "cards"
            cards_path.write_text(cards, encoding="utf-8")
            env["MPE_ASOUND_CARDS"] = str(cards_path)
        override = _OLD_PREDICATE if old_predicate else ""
        script = f'source "{AUDIO_ENGINE_SH}"\n{override}\n{snippet}\n'
        return subprocess.run(
            ["bash", "-c", script],
            env=env, capture_output=True, text=True, timeout=30,
        )


def _card_present(cards: str, *, old_predicate: bool = False) -> bool:
    proc = _run_bash(
        'if mpe_physical_playback_card_present; then echo YES; else echo NO; fi',
        cards, old_predicate=old_predicate,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().endswith("YES")


class CardIsVirtualTests(unittest.TestCase):
    def test_virtual_cards_are_virtual(self):
        for card in ("Dummy", "Loopback", "vc4hdmi0", "vc4hdmi1", "UAC2Gadget", "UAC2"):
            with self.subTest(card=card):
                proc = _run_bash(f'mpe_card_is_virtual "{card}" && echo VIRTUAL || echo REAL')
                self.assertIn("VIRTUAL", proc.stdout, f"{card} must be treated as virtual")

    def test_real_cards_are_not_virtual(self):
        # Headphones is the Pi 4 idle sink — inaudible only if nothing is plugged
        # into it, which is a cable question, not a card-identity question.
        for card in ("Play3", "USB", "Scarlett4i4", "Headphones", "bcm2835"):
            with self.subTest(card=card):
                proc = _run_bash(f'mpe_card_is_virtual "{card}" && echo VIRTUAL || echo REAL')
                self.assertIn("REAL", proc.stdout, f"{card} must NOT be treated as virtual")


class PhysicalPlaybackCardPresentTests(unittest.TestCase):
    def test_idle_sink_alone_is_not_a_physical_card(self):
        """THE REGRESSION. Dummy alone must not satisfy the DAC-enumeration gate."""
        self.assertFalse(
            _card_present(CARDS_IDLE_SINK_ONLY),
            "snd-dummy alone was reported as a physical card — jackd will bind the "
            "idle sink and the appliance will be silent with every reading green",
        )

    def test_hdmi_plus_idle_sink_is_not_a_physical_card(self):
        self.assertFalse(_card_present(CARDS_HDMI_AND_IDLE_SINK))

    def test_real_dac_alongside_idle_sink_is_present(self):
        """Positive control: the fix must not make the gate blind to a real DAC."""
        self.assertTrue(_card_present(CARDS_DAC_AND_IDLE_SINK))

    def test_pi4_headphone_jack_is_present(self):
        """Pi 4 behaviour must be unchanged by the Pi 5 fix."""
        self.assertTrue(_card_present(CARDS_PI4_HEADPHONES))

    # ---- negative controls -------------------------------------------------

    def test_negative_control_old_predicate_fails_the_regression_test(self):
        """With the pre-fix predicate restored, the regression test must FAIL.

        Without this, the assertion above could be passing for some unrelated
        reason and we would never know.
        """
        self.assertTrue(
            _card_present(CARDS_IDLE_SINK_ONLY, old_predicate=True),
            "negative control is broken: the OLD predicate should wrongly report "
            "Dummy as physical. If this fails, the test above proves nothing.",
        )

    def test_negative_control_old_predicate_still_passes_positive_case(self):
        """The old predicate was only wrong about virtual cards, not real ones."""
        self.assertTrue(_card_present(CARDS_DAC_AND_IDLE_SINK, old_predicate=True))


class GraphRestartDenylistTests(unittest.TestCase):
    def test_virtual_cards_do_not_restart_the_production_graph(self):
        for card in ("Dummy", "Loopback", "vc4hdmi0", "UAC2Gadget"):
            with self.subTest(card=card):
                proc = _run_bash(
                    f'mpe_should_skip_graph_restart_for_card "{card}" && echo SKIP || echo RESTART'
                )
                self.assertIn("SKIP", proc.stdout)

    def test_real_dac_does_restart_the_production_graph(self):
        proc = _run_bash('mpe_should_skip_graph_restart_for_card "Play3" && echo SKIP || echo RESTART')
        self.assertIn("RESTART", proc.stdout)


class StuckFailedSweepTests(unittest.TestCase):
    """The sweep must not read the idle sink as 'hardware came back'.

    It decides whether to restart the graph partly on `mpe_physical_playback_card_present`,
    so before the fix an appliance with only snd-dummy looked like a unit whose DAC
    had returned.
    """

    def test_sweep_does_not_see_hardware_when_only_idle_sink_is_present(self):
        proc = _run_bash(
            'card=0; if mpe_physical_playback_card_present; then card=1; fi; echo "card=$card"',
            CARDS_IDLE_SINK_ONLY,
        )
        self.assertIn("card=0", proc.stdout)

    def test_sweep_decision_is_idle_when_no_real_card(self):
        # decision(now, since, swept, threshold, state, card, jack_ready, jackd_active)
        proc = _run_bash(
            'mpe_engine_stuck_failed_decision 1000 1 0 30 failed 0 1 1',
            CARDS_IDLE_SINK_ONLY,
        )
        self.assertIn("idle", proc.stdout)

    def test_sweep_decision_sweeps_when_a_real_card_is_back(self):
        """Positive control: a genuine DAC return must still trigger the sweep."""
        proc = _run_bash(
            'mpe_engine_stuck_failed_decision 1000 900 0 30 failed 1 1 1',
            CARDS_DAC_AND_IDLE_SINK,
        )
        self.assertIn("sweep", proc.stdout)


class JackStateAudibilityTests(unittest.TestCase):
    """jack.state must record WHAT is bound, not just hw:N.

    ALSA reuses card indices, so `device=hw:8` is not an identity. Without
    card/tier/audible, jack.state and engine.state read identically whether the
    graph is driving the player's DAC or the inaudible idle sink.
    """

    def _state_after_write(self, card: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "jack.state"
            env = os.environ.copy()
            env.update({
                "MPE_MODULE_REPO": str(REPO_ROOT),
                "MPE_RUN_DIR": tmp,
                "MPE_JACK_STATE_FILE": str(state),
            })
            proc = subprocess.run(
                ["bash", "-c",
                 f'source "{AUDIO_ENGINE_SH}"; '
                 f'mpe_jack_state_write "hw:8" 128 2 48000 "{card}" 3'],
                env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return dict(
                line.split("=", 1)
                for line in state.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )

    def test_idle_sink_is_recorded_as_inaudible(self):
        state = self._state_after_write("Dummy")
        self.assertEqual(state["card"], "Dummy")
        self.assertEqual(state["audible"], "no")

    def test_real_dac_is_recorded_as_audible(self):
        state = self._state_after_write("Play3")
        self.assertEqual(state["card"], "Play3")
        self.assertEqual(state["audible"], "yes")

    def test_unknown_card_is_not_assumed_audible(self):
        """A missing card id must never be optimistically reported as audible."""
        state = self._state_after_write("")
        self.assertEqual(state["audible"], "unknown")

    def test_engine_reason_names_the_idle_sink(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "jack.state"
            env = os.environ.copy()
            env.update({
                "MPE_MODULE_REPO": str(REPO_ROOT),
                "MPE_RUN_DIR": tmp,
                "MPE_JACK_STATE_FILE": str(state),
            })
            proc = subprocess.run(
                ["bash", "-c",
                 f'source "{AUDIO_ENGINE_SH}"; '
                 f'mpe_jack_state_write "hw:8" 128 2 48000 Dummy 3; '
                 f'printf "reason=[%s]" "$(mpe_engine_sink_reason)"'],
                env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertIn("reason=[idle-sink]", proc.stdout)

    def test_engine_reason_is_empty_on_a_real_dac(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "jack.state"
            env = os.environ.copy()
            env.update({
                "MPE_MODULE_REPO": str(REPO_ROOT),
                "MPE_RUN_DIR": tmp,
                "MPE_JACK_STATE_FILE": str(state),
            })
            proc = subprocess.run(
                ["bash", "-c",
                 f'source "{AUDIO_ENGINE_SH}"; '
                 f'mpe_jack_state_write "hw:1" 128 2 48000 Play3 1; '
                 f'printf "reason=[%s]" "$(mpe_engine_sink_reason)"'],
                env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertIn("reason=[]", proc.stdout)


class SingleSourceOfTruthTests(unittest.TestCase):
    """There must be exactly ONE virtual-card list.

    Five disagreeing lists is what let snd-dummy through. The first version of
    this guard was itself bypassable — it only looked at single-line `grep`
    chains, so a `case`-glob copy (the shape of the canonical predicate!) or a
    backslash-wrapped grep slipped straight past. It now joins continuations and
    checks both shapes.
    """

    VIRTUAL_TOKENS = ("Loopback", "vc4hdmi", "vc4-hdmi", "UAC2", "Dummy")
    # The one legitimate home, plus the JUCE-device-string mirror which is a
    # different namespace and is deliberately named VIRTUAL_GREP so it is
    # greppable alongside the predicate.
    ALLOWED = {"audio-engine.sh", "detect-audio-device.sh"}

    def _sources(self):
        roots = [
            (REPO_ROOT / "scripts", REPO_ROOT),
            (Path("/home/mitch/Documents/GitHub/mpe-cli"), Path("/home/mitch/Documents/GitHub/mpe-cli")),
        ]
        for root, base in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.sh")):
                if path.name in self.ALLOWED:
                    continue
                yield path, base

    @staticmethod
    def _logical_lines(text: str):
        """Join backslash continuations so a wrapped grep chain is one line."""
        out, buf, start = [], "", 1
        for lineno, raw in enumerate(text.splitlines(), 1):
            if not buf:
                start = lineno
            stripped = raw.rstrip()
            if stripped.endswith("\\"):
                buf += stripped[:-1] + " "
                continue
            out.append((start, buf + stripped))
            buf = ""
        if buf:
            out.append((start, buf))
        return out

    def test_no_file_reimplements_the_virtual_card_list(self):
        offenders = []
        for path, base in self._sources():
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in self._logical_lines(text):
                if line.lstrip().startswith("#"):
                    continue
                hits = sum(tok in line for tok in self.VIRTUAL_TOKENS)
                if hits < 2:
                    continue
                # Two shapes count as a reimplementation: a grep filter, and a
                # `case` pattern list — the latter being the exact shape of
                # mpe_card_is_virtual itself, i.e. the most likely copy-paste.
                if "grep" in line or (")" in line and ("|" in line or "case" in line)):
                    offenders.append(
                        f"{path.relative_to(base)}:{lineno}: {line.strip()[:100]}"
                    )
        self.assertEqual(
            offenders, [],
            "these lines re-implement the virtual-card list instead of calling "
            "mpe_card_is_virtual():\n" + "\n".join(offenders),
        )

    # ---- negative controls: the guard must catch both bypass shapes ----

    def _guard_finds(self, snippet: str) -> bool:
        for lineno, line in self._logical_lines(snippet):
            if line.lstrip().startswith("#"):
                continue
            hits = sum(tok in line for tok in self.VIRTUAL_TOKENS)
            if hits < 2:
                continue
            if "grep" in line or (")" in line and ("|" in line or "case" in line)):
                return True
        return False

    def test_negative_control_guard_catches_a_case_glob_copy(self):
        self.assertTrue(
            self._guard_finds('    case "$id" in\n        Loopback | Dummy) return 0 ;;\n    esac\n'),
            "guard misses a case-glob copy of the predicate — the likeliest bypass",
        )

    def test_negative_control_guard_catches_a_wrapped_grep_chain(self):
        self.assertTrue(
            self._guard_finds('  printf %s "$r" | grep -viE \'Loopback\' \\\n     | grep -viE \'Dummy\'\n'),
            "guard misses a backslash-wrapped grep chain",
        )

    def test_negative_control_guard_ignores_prose(self):
        self.assertFalse(
            self._guard_finds('# Loopback and Dummy are both virtual, see the predicate\n'),
            "guard flags comments — it would be noise and get disabled",
        )


class AnchoredPredicateTests(unittest.TestCase):
    """The predicate must not silence a real DAC whose id merely starts the same."""

    def test_lookalike_real_cards_are_not_virtual(self):
        for card in ("DummyPlug", "LoopbackPro", "UAC2Audio", "UAC20", "vc4hdmiX"):
            with self.subTest(card=card):
                proc = _run_bash(f'mpe_card_is_virtual "{card}" && echo VIRTUAL || echo REAL')
                self.assertIn(
                    "REAL", proc.stdout,
                    f"{card} is a real card being classified virtual — that SILENCES a "
                    f"working rig, the exact failure this predicate exists to prevent",
                )

    def test_alsa_duplicate_id_suffixes_are_still_virtual(self):
        """ALSA appends _1, _2 when two cards share an id."""
        for card in ("Loopback_1", "Dummy_1", "vc4hdmi0", "vc4hdmi1"):
            with self.subTest(card=card):
                proc = _run_bash(f'mpe_card_is_virtual "{card}" && echo VIRTUAL || echo REAL')
                self.assertIn("VIRTUAL", proc.stdout)


if __name__ == "__main__":
    unittest.main()
