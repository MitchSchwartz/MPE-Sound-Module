"""mpe-fx-host: the DSP self-test, the wire format, the gate, the unit.

Its wiring is tested where every client's is: tests/test_audio_routing.py runs
mpe-fx-host.c against a fake JACK and checks it against the routing authority.

The DSP checks live in C (`native/mpe-fx-host/fx_selftest.c`) and run here
through `make selftest` whenever a C compiler is present. Without one they are
SKIPPED, loudly — a skipped self-test is not a passed one, and the skip reason
says so.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from patch_browser import fx_host_protocol as proto

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE = REPO_ROOT / "native" / "mpe-fx-host"
C_CLIENT = NATIVE / "mpe-fx-host.c"
C_DSP = NATIVE / "fx_dsp.c"
UNIT = REPO_ROOT / "config" / "mpe-fx-host.service"
START = REPO_ROOT / "scripts" / "start-mpe-fx-host.sh"


def _compiler() -> str | None:
    return shutil.which(os.environ.get("CC", "cc")) or shutil.which("gcc")


_SELFTEST_DIR: tempfile.TemporaryDirectory | None = None
_SELFTEST_BIN: Path | None = None
_SELFTEST_RUN: subprocess.CompletedProcess | None = None


def _selftest() -> tuple[Path, subprocess.CompletedProcess]:
    """Build (once per run, into a temp dir — never the tree) and run it."""
    global _SELFTEST_DIR, _SELFTEST_BIN, _SELFTEST_RUN
    if _SELFTEST_RUN is None:
        _SELFTEST_DIR = tempfile.TemporaryDirectory()
        _SELFTEST_BIN = Path(_SELFTEST_DIR.name) / "fx-selftest"
        _SELFTEST_RUN = subprocess.run(
            ["make", "-s", "-C", str(NATIVE), "selftest", f"SELFTEST_BIN={_SELFTEST_BIN}"],
            capture_output=True, text=True, timeout=600,
        )
    return _SELFTEST_BIN, _SELFTEST_RUN


def tearDownModule() -> None:
    if _SELFTEST_DIR is not None:
        _SELFTEST_DIR.cleanup()


def _need_compiler(case: unittest.TestCase) -> None:
    if _compiler() is None or shutil.which("make") is None:
        msg = ("NO C COMPILER — mpe-fx-host DSP self-test NOT RUN. Depth-0 identity, "
               "output bound, click-free depth/swap are unverified on this machine.")
        print(f"\nSKIP: {msg}", file=sys.stderr)
        case.skipTest(msg)


class DspSelfTest(unittest.TestCase):
    def test_every_listener_protecting_property_holds(self):
        """For every effect and the chain: depth 0 or an empty mask is the
        input; output RMS never exceeds the input's, for every effect alone and
        every mask (and peak, where the effect can promise it), nor ±1; on the
        keys signal it never falls more than 1 dB below it (phaser 1.3 dB, the
        tremolo: below its computed level); the chorus and phaser are their
        #149 mix times a static gain; the chain runs drive, phaser, chorus,
        tremolo; no NaN or denormal blowup over two minutes; depth steps,
        toggles and re-entry after bypass are click-free; the drive's
        oversampling holds aliasing down — each against a deliberately broken
        run that must fail the same check."""
        _need_compiler(self)
        _, run = _selftest()
        self.assertEqual(run.returncode, 0, f"{run.stdout}\n{run.stderr}")
        self.assertIn("all checks passed", run.stdout)


class WireFormat(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(proto.encode(0, 0.5), b"fx 0 0.500000")
        self.assertEqual(proto.encode(proto.EFFECT_CHORUS, 0), b"fx 0 0.000000")
        self.assertEqual(proto.encode(0, 1), b"fx 0 1.000000")

    def test_depth_is_clamped(self):
        self.assertEqual(proto.encode(0, -0.3), b"fx 0 0.000000")
        self.assertEqual(proto.encode(0, 7.0), b"fx 0 1.000000")

    def test_refuses_what_it_cannot_say_honestly(self):
        for bad_depth in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(depth=bad_depth), self.assertRaises(ValueError):
                proto.encode(0, bad_depth)
        for bad_id in (5, -1, 99, True, 0.0, "0"):
            with self.subTest(effect=bad_id), self.assertRaises(ValueError):
                proto.encode(bad_id, 0.5)

    def test_python_effect_ids_match_the_c_table(self):
        """EFFECT_IDS is the table's indices, read from the C, not restated:
        same count, same order, each Python name the C entry's name. An id is
        a number on the wire, so a reorder on either side must fail here."""
        source = C_DSP.read_text()
        table = re.search(r"const fx_effect_desc FX_EFFECTS\[\] = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "FX_EFFECTS table not found in fx_dsp.c")
        names = re.findall(r'\{\s*"([a-z0-9_-]+)"', table.group(1))
        self.assertEqual(names, ["chorus", "drive", "phaser", "tremolo", "flanger"])
        self.assertEqual(proto.EFFECT_IDS, tuple(range(len(names))))
        self.assertEqual(sorted(proto.EFFECT_NAMES), list(proto.EFFECT_IDS))
        for effect_id in proto.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                self.assertEqual(proto.EFFECT_NAMES[effect_id].lower(), names[effect_id])
        self.assertEqual(
            (proto.EFFECT_CHORUS, proto.EFFECT_TUBE_DRIVE, proto.EFFECT_PHASER,
             proto.EFFECT_TREMOLO, proto.EFFECT_FLANGER),
            (0, 1, 2, 3, 4),
        )

    def test_every_shipped_id_encodes(self):
        for effect_id in proto.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                self.assertEqual(proto.encode(effect_id, 0.25), f"fx {effect_id} 0.250000".encode())

    def test_chain_encoding(self):
        self.assertEqual(proto.encode_chain(0b0101, 0.5), b"chain 5 0.500000")
        self.assertEqual(proto.encode_chain(proto.MASK_NONE, 0.5), b"chain 0 0.500000")
        self.assertEqual(proto.encode_chain(proto.MASK_ALL, 1), b"chain 31 1.000000")
        self.assertEqual(proto.encode_chain(1 << proto.EFFECT_FLANGER, 0.5), b"chain 16 0.500000")
        self.assertEqual(proto.encode_chain(1, -0.3), b"chain 1 0.000000")
        self.assertEqual(proto.encode_chain(1, 7.0), b"chain 1 1.000000")

    def test_chain_refuses_what_it_cannot_say_honestly(self):
        for bad_depth in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(depth=bad_depth), self.assertRaises(ValueError):
                proto.encode_chain(1, bad_depth)
        for bad_mask in (32, 0b100001, -1, True, 1.0, "1", None):
            with self.subTest(mask=bad_mask), self.assertRaises(ValueError):
                proto.encode_chain(bad_mask, 0.5)

    def test_mask_helpers(self):
        D, P, C, T, F = (proto.EFFECT_TUBE_DRIVE, proto.EFFECT_PHASER, proto.EFFECT_CHORUS,
                         proto.EFFECT_TREMOLO, proto.EFFECT_FLANGER)
        self.assertEqual(proto.CHAIN_ORDER, (D, P, F, C, T))
        self.assertEqual(sorted(proto.CHAIN_ORDER), list(proto.EFFECT_IDS))
        self.assertEqual(proto.MASK_ALL, 0b11111)
        self.assertEqual(proto.EFFECT_NAMES[F], "Flanger")
        # The flanger runs between the phaser and the chorus.
        self.assertEqual(proto.effects_in(proto.mask_of([T, C, F, P])), (P, F, C, T))
        self.assertEqual(proto.mask_of([]), proto.MASK_NONE)
        self.assertEqual(proto.mask_of([T, C, C]), (1 << T) | (1 << C))
        self.assertEqual(proto.mask_of(proto.EFFECT_IDS), proto.MASK_ALL)
        # The run order, whatever order they were enabled in.
        self.assertEqual(proto.effects_in(proto.mask_of([T, C, D])), (D, C, T))
        self.assertEqual(proto.effects_in(proto.MASK_ALL), proto.CHAIN_ORDER)
        self.assertEqual(proto.effects_in(proto.MASK_NONE), ())
        self.assertTrue(proto.is_enabled(proto.mask_of([P]), P))
        self.assertFalse(proto.is_enabled(proto.mask_of([P]), C))
        self.assertEqual(proto.toggled(proto.MASK_NONE, P), 1 << P)
        self.assertEqual(proto.toggled(1 << P, P), proto.MASK_NONE)
        for bad in (5, -1, True, "0"):
            with self.subTest(effect=bad):
                with self.assertRaises(ValueError):
                    proto.mask_of([bad])
                with self.assertRaises(ValueError):
                    proto.toggled(0, bad)
        with self.assertRaises(ValueError):
            proto.effects_in(32)

    def test_the_old_datagram_is_the_one_bit_mask(self):
        """`encode` is kept for the single-effect datagram; the client reads
        `fx <id> d` as `chain <1 << id> d` (the C side is checked below)."""
        for effect_id in proto.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                self.assertEqual(proto.effects_in(proto.mask_of([effect_id])), (effect_id,))

    def test_chain_order_matches_the_c(self):
        """CHAIN_ORDER is FX_CHAIN_ORDER in fx_dsp.c, read from the C. The
        order is what the engine runs; a drift would make the UI's label order
        lie about it."""
        found = re.search(r"const int FX_CHAIN_ORDER\[\] = \{([^}]*)\};", C_DSP.read_text())
        self.assertIsNotNone(found, "FX_CHAIN_ORDER not found in fx_dsp.c")
        c_order = tuple(int(v) for v in found.group(1).split(","))
        self.assertEqual(c_order, proto.CHAIN_ORDER)

    def test_the_control_port_matches_the_client(self):
        match = re.search(r"#define\s+CONTROL_PORT_DEFAULT\s+(\d+)", C_CLIENT.read_text())
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), proto.DEFAULT_PORT)
        self.assertIn('getenv("MPE_FX_HOST_PORT")', C_CLIENT.read_text())

    def test_resolve_port(self):
        cases = {"": 9958, "9000": 9000, "0": 9958, "70000": 9958, "x": 9958}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                old = os.environ.get("MPE_FX_HOST_PORT")
                os.environ["MPE_FX_HOST_PORT"] = raw
                try:
                    self.assertEqual(proto.resolve_port(), want)
                finally:
                    if old is None:
                        os.environ.pop("MPE_FX_HOST_PORT", None)
                    else:
                        os.environ["MPE_FX_HOST_PORT"] = old

    def test_every_encoding_round_trips_through_the_real_parser(self):
        """Both sides of one protocol, the C side executed, not read."""
        _need_compiler(self)
        binary, run = _selftest()
        self.assertTrue(binary.exists(), run.stderr)

        def parse(text: str) -> str:
            return subprocess.run(
                [str(binary), "--parse", text], capture_output=True, text=True, timeout=10,
            ).stdout.strip()

        for mask in range(proto.MASK_ALL + 1):
            for depth in (0.0, 0.000001, 0.25, 0.5, 0.999999, 1.0):
                with self.subTest(mask=mask, depth=depth):
                    wire = proto.encode_chain(mask, depth).decode("ascii")
                    self.assertEqual(parse(wire), f"ok {mask} {depth:.6f}")
        # The first protocol's single-effect datagram is the one-bit mask.
        for effect_id in proto.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                wire = proto.encode(effect_id, 0.5).decode("ascii")
                self.assertEqual(parse(wire), f"ok {proto.mask_of([effect_id])} 0.500000")
        # Things a stray loopback sender might send must change nothing. (A
        # mask naming an effect the build lacks, e.g. 32, parses; the client
        # refuses it against its table.)
        for junk in ("gain 0.5", "fx", "fx 0", "fx 0 nan", "fx 0 inf", "fx -1 0.5",
                     "fx 0x1 0.5", "fx 0 0.5 extra", "fx0 0.5", "fx 1e3 0.5", "fx 8 0.5", "",
                     "chain", "chain 5", "chain -1 0.5", "chain 0x5 0.5", "chain 5 nan",
                     "chain 5 0.5 extra", "chain5 0.5", "chain 256 0.5", "chain 5.0 0.5",
                     "chains 5 0.5"):
            with self.subTest(junk=junk):
                self.assertEqual(parse(junk), "reject")


class Gate(unittest.TestCase):
    def test_python_and_shell_agree_on_what_switches_it_on(self):
        """Every value through the real shell gate, via --check (see the
        live monitor's identical test for why never without it)."""
        for value in ("1", "0", "on", "ON", "true", "True", "yes", "YES", "off",
                      "no", "false", "2", ""):
            with self.subTest(value=value):
                env = dict(os.environ, MPE_FX_HOST=value)
                proc = subprocess.run(["bash", str(START), "--check"], env=env,
                                      capture_output=True, text=True, timeout=30)
                self.assertEqual(proto.enabled(value), proc.returncode == 0)

    def test_the_gate_check_never_reaches_the_binary(self):
        """--check must not start the insert; proven with a stand-in that
        leaves a mark, and the same script without --check must reach it."""
        repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bin_dir = repo / "native" / "mpe-fx-host"
        bin_dir.mkdir(parents=True)
        (repo / "scripts").mkdir()
        mark = repo / "it-ran"
        stand_in = bin_dir / "mpe-fx-host"
        stand_in.write_text(f'#!/bin/sh\ntouch "{mark}"\n')
        stand_in.chmod(0o755)
        env = dict(os.environ, MPE_FX_HOST="1", MPE_MODULE_REPO=str(repo),
                   MPE_RUN_DIR=str(repo))
        check = subprocess.run(["bash", str(START), "--check"], env=env,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertFalse(mark.exists(), "--check started the FX host")
        subprocess.run(["bash", str(START)], env=env, capture_output=True, text=True, timeout=30)
        self.assertTrue(mark.exists(), "the script never started the FX host at all")

    def test_off_by_default(self):
        env = {k: v for k, v in os.environ.items() if k != "MPE_FX_HOST"}
        proc = subprocess.run(["bash", str(START), "--check"], env=env,
                              capture_output=True, text=True, timeout=30)
        self.assertNotEqual(proc.returncode, 0)


class Unit(unittest.TestCase):
    """The live monitor's unit shape."""

    def _lines(self, path: Path) -> set[str]:
        return {line.strip() for line in path.read_text().splitlines()}

    def test_shape(self):
        lines = self._lines(UNIT)
        for want in (
            "After=mpe-jackd.service surge-xt-cli.service",
            "Wants=mpe-jackd.service",
            "PartOf=mpe-jackd.service",
            "Restart=on-failure",
            "KillMode=mixed",
            "TimeoutStopSec=5",
            "ExecStart=@MPE_MODULE_REPO@/scripts/start-mpe-fx-host.sh",
            # The live monitor's restore: it asks the routing authority, which
            # names Surge once our ports are gone.
            "ExecStopPost=-@MPE_MODULE_REPO@/scripts/restore-direct-monitor-path.sh",
        ):
            self.assertIn(want, lines)

    def test_same_cores_as_the_live_monitor(self):
        def affinity(path: Path) -> str:
            found = [l for l in self._lines(path) if l.startswith("CPUAffinity=")]
            self.assertEqual(len(found), 1, path)
            return found[0]
        self.assertEqual(affinity(UNIT), affinity(REPO_ROOT / "config/mpe-live-monitor.service"))

    def test_installed_but_in_neither_enable_list(self):
        text = (REPO_ROOT / "scripts/install-units.sh").read_text()
        for name in ("ENABLED", "DISABLED"):
            block = re.search(rf"^{name}=\((.*?)^\)", text, re.S | re.M)
            self.assertIsNotNone(block, name)
            entries = [l.split("#")[0].strip() for l in block.group(1).splitlines()]
            self.assertNotIn("mpe-fx-host", entries, f"mpe-fx-host listed in {name}")

    def test_client_exits_non_zero_when_jack_goes_away(self):
        """Restart=on-failure only fires on a failure exit. Read from the C
        because there is no jackd here to kill; the branch must return 1."""
        source = C_CLIENT.read_text()
        branch = re.search(r"if \(g_jack_shutdown\) \{(.*?)\n    \}", source, re.S)
        self.assertIsNotNone(branch, "no JACK-shutdown exit branch in main")
        self.assertRegex(branch.group(1), r"return 1;")


if __name__ == "__main__":
    unittest.main()
