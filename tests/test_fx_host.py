"""mpe-fx-host: the DSP self-test, the wire format, the gate, the unit.

Its wiring is tested where every client's is: tests/test_audio_routing.py runs
mpe-fx-host.c against a fake JACK and checks it against the routing authority.

The DSP checks live in C (`native/mpe-fx-host/fx_selftest.c`), built here by
`make selftest-bin` whenever a C compiler is present and run with one worker
per core. They are SKIPPED, loudly, and the skip reason says which case:

- no C compiler: nothing is verified on this machine;
- FX_SELFTEST_SKIP=1 (CI's unittest job): CI's fx-selftest job runs them,
  only when the FX code changes;
- the self-test's inputs (its C sources, its Makefile, the compiler, its
  environment switches) hash to a run that already passed on this machine.
  The pass is recorded under ~/.cache/mpe-fx-selftest/, written only on a
  pass. FX_SELFTEST_FORCE=1 runs it anyway.

A skipped self-test is not a passed one. The wire-format tests below use the
built binary's parser and need only the compile, so they run every time.

FX_SELFTEST_SERIAL=1 also runs the self-test serially (one worker) beside the
parallel run and requires the two to print the same bytes, numbers included.
CI's fx-selftest job sets it.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from patch_browser import fx_host_protocol as proto

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE = REPO_ROOT / "native" / "mpe-fx-host"
C_CLIENT = NATIVE / "mpe-fx-host.c"
C_DSP = NATIVE / "fx_dsp.c"
UNIT = REPO_ROOT / "config" / "mpe-fx-host.service"
START = REPO_ROOT / "scripts" / "start-mpe-fx-host.sh"

#: What `make selftest-bin` compiles, and so all the self-test's result can
#: depend on besides the compiler. Not the client (mpe-fx-host.c) and not the
#: Python protocol: the self-test never reads either. Its own switches are in
#: the key too (_SELFTEST_ENV).
SELFTEST_INPUTS = ("fx_dsp.c", "fx_dsp.h", "fx_gain_state.c", "fx_gain_state.h", "fx_selftest.c",
                   "Makefile")
_SELFTEST_ENV = ("CC", "CFLAGS", "LDFLAGS", "FX_SELFTEST_FULL")


def _compiler() -> str | None:
    return shutil.which(os.environ.get("CC", "cc")) or shutil.which("gcc")


def _flag(name: str) -> bool:
    return os.environ.get(name, "") == "1"


def selftest_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "mpe-fx-selftest"


def selftest_key(native: Path = NATIVE) -> str:
    """A content hash of everything the self-test's result depends on."""
    h = hashlib.sha256()
    for name in SELFTEST_INPUTS:
        h.update(name.encode() + b"\0" + (native / name).read_bytes() + b"\0")
    cc = os.environ.get("CC", "cc")
    try:
        version = subprocess.run([cc, "--version"], capture_output=True, text=True,
                                 timeout=30).stdout
    except OSError:
        version = "no compiler"
    h.update(version.encode() + b"\0")
    for name in _SELFTEST_ENV:
        h.update(f"{name}={os.environ.get(name, '')}".encode() + b"\0")
    return h.hexdigest()


def _passed_marker(key: str) -> Path:
    return selftest_cache_dir() / f"passed-{key}"


_BUILD_DIR: tempfile.TemporaryDirectory | None = None
_BUILD: tuple[Path, subprocess.CompletedProcess] | None = None
_RUNS: tuple[subprocess.CompletedProcess, subprocess.CompletedProcess | None] | None = None


def _selftest_binary() -> tuple[Path, subprocess.CompletedProcess]:
    """Build (once per run, into a temp dir, never the tree); do not run."""
    global _BUILD_DIR, _BUILD
    if _BUILD is None:
        _BUILD_DIR = tempfile.TemporaryDirectory()
        binary = Path(_BUILD_DIR.name) / "fx-selftest"
        _BUILD = binary, subprocess.run(
            ["make", "-s", "-C", str(NATIVE), "selftest-bin", f"SELFTEST_BIN={binary}"],
            capture_output=True, text=True, timeout=300,
        )
    return _BUILD


def _selftest_env(jobs: str | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("FX_SELFTEST_JOBS", "FX_SELFTEST_TIMES")}
    if jobs is not None:
        env["FX_SELFTEST_JOBS"] = jobs
    return env


def _selftest_runs() -> tuple[subprocess.CompletedProcess, subprocess.CompletedProcess | None]:
    """Run every check, `-v`, one worker per core; with FX_SELFTEST_SERIAL=1,
    the serial run (one worker) at the same time. Records the pass."""
    global _RUNS
    if _RUNS is not None:
        return _RUNS
    binary, build = _selftest_binary()
    if build.returncode != 0:
        _RUNS = build, None
        return _RUNS
    key = selftest_key()
    started = time.monotonic()
    serial = None
    if _flag("FX_SELFTEST_SERIAL"):
        serial = subprocess.Popen([str(binary), "-v"], env=_selftest_env("1"), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    parallel = subprocess.run([str(binary), "-v"], env=_selftest_env(None), capture_output=True,
                              text=True, timeout=900)
    serial_run = None
    if serial is not None:
        out, err = serial.communicate(timeout=1800)
        serial_run = subprocess.CompletedProcess(serial.args, serial.returncode, out, err)
    passed = parallel.returncode == 0 and "fx-selftest: all checks passed" in parallel.stdout
    if serial_run is not None:
        passed = passed and (serial_run.stdout, serial_run.stderr) == (parallel.stdout, parallel.stderr)
    if passed:
        marker = _passed_marker(key)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"passed {time.strftime('%Y-%m-%dT%H:%M:%S')} "
                          f"in {time.monotonic() - started:.1f} s\n")
    _RUNS = parallel, serial_run
    return _RUNS


def tearDownModule() -> None:
    if _BUILD_DIR is not None:
        _BUILD_DIR.cleanup()


def _skip(case: unittest.TestCase, msg: str) -> None:
    print(f"\nSKIP: {msg}", file=sys.stderr)
    case.skipTest(msg)


def _need_compiler(case: unittest.TestCase) -> None:
    if _compiler() is None or shutil.which("make") is None:
        _skip(case, "NO C COMPILER — mpe-fx-host DSP self-test NOT RUN. Depth-0 identity, "
                    "output bound, click-free depth/swap are unverified on this machine.")


def _need_selftest_run(case: unittest.TestCase) -> None:
    """Skip, visibly and with the reason, when the DSP checks are not to run."""
    _need_compiler(case)
    if _flag("FX_SELFTEST_SKIP"):
        _skip(case, "FX_SELFTEST_SKIP=1: mpe-fx-host DSP self-test NOT RUN here (CI runs it in "
                    "its fx-selftest job when the FX code changes).")
    if _flag("FX_SELFTEST_FORCE") or _RUNS is not None:
        return
    key = selftest_key()
    if _passed_marker(key).exists():
        _skip(case, f"mpe-fx-host DSP self-test NOT RUN: its inputs are unchanged since a run "
                    f"that passed on this machine ({_passed_marker(key)}). "
                    f"FX_SELFTEST_FORCE=1 runs it.")


class DspSelfTest(unittest.TestCase):
    def test_every_listener_protecting_property_holds(self):
        """For every effect and the chain: depth 0 or an empty mask is the
        input; output RMS never exceeds the input's times the chain's makeup
        (+1 dB x the loudest stage's depth), for every effect alone and every
        mask (and peak, where the effect can promise it), nor ±1; on the keys
        signal it never falls more than 1 dB below that (phaser 1.3 dB, the
        tremolo: below its computed level); the makeup is applied once per
        chain (+0.7..+1.1 dB at full depth over the same chain without it) and
        moves no faster than the depth smoother and the enable ramp; the chorus and phaser are their
        #149 mix times a static gain; the chain runs drive, phaser, chorus,
        tremolo; no NaN or denormal blowup over 30 s (feedback: 115 s); depth
        steps, toggles and re-entry after bypass are click-free; the drive's
        oversampling holds aliasing down — each against a deliberately broken
        run that must fail the same check."""
        _need_selftest_run(self)
        run, _serial = _selftest_runs()
        self.assertEqual(run.returncode, 0, f"{run.stdout}\n{run.stderr}")
        self.assertIn("fx-selftest: all checks passed", run.stdout)

    def test_serial_and_parallel_print_the_same(self):
        """Every check in its own worker, one at a time or one per core: the
        same verbose output, byte for byte, numbers included."""
        _need_selftest_run(self)
        if not _flag("FX_SELFTEST_SERIAL"):
            _skip(self, "FX_SELFTEST_SERIAL is not 1: the serial run (about 2.5 min) is NOT "
                        "compared with the parallel one here; CI's fx-selftest job compares them.")
        parallel, serial = _selftest_runs()
        self.assertIsNotNone(serial, parallel.stderr)
        self.assertEqual(serial.returncode, parallel.returncode, serial.stderr)
        self.assertIn("fx-selftest: all checks passed", serial.stdout)
        self.assertEqual(serial.stdout, parallel.stdout)
        self.assertEqual(serial.stderr, parallel.stderr)


class Runner(unittest.TestCase):
    """The self-test's runner is its failure channel: each check runs in a
    worker, and a failure reaches the exit code only through it. Its own
    controls, through the built binary, every run (they take under a second)."""

    def _check(self, mode: str, jobs: str = "4") -> subprocess.CompletedProcess:
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        return subprocess.run([str(binary), "--runner-check", mode], env=_selftest_env(jobs),
                              capture_output=True, text=True, timeout=60)

    def test_output_is_in_case_order_and_the_fold_sees_every_part(self):
        """Later cases finish first; with one worker or several, the output is
        in case order and the fold sums every part (1 + ... + 12)."""
        want = "".join(f"case {i}\n" for i in range(12)) + "fold 78\nfx-selftest: all checks passed\n"
        for jobs in ("1", "4"):
            with self.subTest(jobs=jobs):
                run = self._check("order", jobs)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stdout, want)

    def test_a_failed_check_in_a_worker_fails_the_run(self):
        run = self._check("fail")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stdout, "ran 0\nran 1\nran 2\n")
        self.assertEqual(run.stderr.count("deliberate failure (runner control)"), 2)
        self.assertIn("fx-selftest: 2 check(s) failed", run.stderr)

    def test_a_worker_that_dies_fails_the_run(self):
        run = self._check("crash")
        self.assertEqual(run.returncode, 1)
        self.assertIn("FAIL crash: worker killed by signal", run.stderr)
        self.assertIn("ran 2", run.stdout)
        self.assertIn("fx-selftest: 1 check(s) failed", run.stderr)

    def test_jobs_must_be_a_positive_whole_number(self):
        for bad in ("0", "-2", "x", "1.5"):
            with self.subTest(jobs=bad):
                run = self._check("order", bad)
                self.assertEqual(run.returncode, 2)
                self.assertIn("FX_SELFTEST_JOBS", run.stderr)


class SelfTestCache(unittest.TestCase):
    """The skip is keyed on content, so any change to what the self-test
    builds from makes it run again."""

    def _copy(self) -> Path:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in SELFTEST_INPUTS:
            shutil.copy(NATIVE / name, tmp / name)
        return tmp

    def test_every_input_changes_the_key(self):
        tmp = self._copy()
        base = selftest_key(tmp)
        self.assertEqual(selftest_key(tmp), base)
        for name in SELFTEST_INPUTS:
            with self.subTest(file=name):
                original = (tmp / name).read_bytes()
                (tmp / name).write_bytes(original + b"\n")
                self.assertNotEqual(selftest_key(tmp), base)
                (tmp / name).write_bytes(original)
        self.assertEqual(selftest_key(tmp), base)

    def test_the_switches_change_the_key(self):
        tmp = self._copy()
        base = selftest_key(tmp)
        for name in _SELFTEST_ENV:
            with self.subTest(var=name):
                old = os.environ.get(name)
                os.environ[name] = "1" if name == "FX_SELFTEST_FULL" else (old or "") + " x"
                try:
                    self.assertNotEqual(selftest_key(tmp), base)
                finally:
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old

    def test_the_inputs_are_what_the_makefile_compiles(self):
        """A source the self-test build gains must join the key."""
        rule = re.search(r"^selftest-bin:.*?\n\t(.*?)\n", (NATIVE / "Makefile").read_text(), re.S | re.M)
        self.assertIsNotNone(rule, "no selftest-bin rule in the Makefile")
        compiled = set(re.findall(r"\b([\w-]+\.c)\b", rule.group(1)))
        self.assertEqual(compiled, {"fx_selftest.c", "fx_dsp.c", "fx_gain_state.c"})
        self.assertLessEqual(compiled, set(SELFTEST_INPUTS))
        for source in compiled:
            for header in re.findall(r'#include "([^"]+)"', (NATIVE / source).read_text()):
                with self.subTest(source=source, header=header):
                    self.assertIn(header, SELFTEST_INPUTS)


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
        for bad_id in (7, -1, 99, True, 0.0, "0"):
            with self.subTest(effect=bad_id), self.assertRaises(ValueError):
                proto.encode(bad_id, 0.5)

    def test_levels_encoding(self):
        self.assertEqual(
            proto.encode_chain_levels(5, 0.5, (1.0, 0.25, 0, 1, 0.5, 0.75, 0.1)),
            b"chainl 5 0.500000 1.000000 0.250000 0.000000 1.000000 0.500000 0.750000 0.100000",
        )
        # Clamped like the depth.
        self.assertEqual(
            proto.encode_chain_levels(0, 2.0, (-1.0, 3.0, 0.5, 0.5, 0.5, 0.5, 0.5)),
            b"chainl 0 1.000000 0.000000 1.000000 0.500000 0.500000 0.500000 0.500000 0.500000",
        )
        self.assertEqual(proto.LEVELS_DEFAULT, (1.0,) * len(proto.EFFECT_IDS))

    def test_levels_encoding_refuses_what_it_cannot_say_honestly(self):
        good = (1.0,) * len(proto.EFFECT_IDS)
        for bad in ((1.0,) * 5, (1.0,) * 6, (1.0,) * 8, (1.0, 1.0, float("nan"), 1.0, 1.0, 1.0, 1.0),
                    (1.0, float("inf"), 1.0, 1.0, 1.0, 1.0, 1.0), (1.0, True, 1.0, 1.0, 1.0, 1.0, 1.0),
                    ("1", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0), None, {0: 1.0}):
            with self.subTest(levels=bad), self.assertRaises(ValueError):
                proto.encode_chain_levels(5, 0.5, bad)
        with self.assertRaises(ValueError):
            proto.encode_chain_levels(proto.MASK_ALL + 1, 0.5, good)
        with self.assertRaises(ValueError):
            proto.encode_chain_levels(5, float("nan"), good)

    def test_levels_from_is_append_only(self):
        n = len(proto.EFFECT_IDS)
        self.assertIsNone(proto.levels_from(None))
        self.assertIsNone(proto.levels_from({"0": 1.0}))
        self.assertEqual(proto.levels_from([]), (1.0,) * n)
        self.assertEqual(proto.levels_from([0.5]), (0.5,) + (1.0,) * (n - 1))
        self.assertEqual(proto.levels_from([0.1] * (n + 3)), (0.1,) * n)
        self.assertEqual(proto.levels_from(["x", None, True, 2.0, -2.0]), (1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0))
        # A five-effect build's list (before 2026-10-13): the first five as
        # stored, the rotary and the auto-wah at 1.0.
        self.assertEqual(proto.levels_from([0.4, 0.0, 0.55, 0.3, 0.9]), (0.4, 0.0, 0.55, 0.3, 0.9, 1.0, 1.0))

    def test_with_level(self):
        ones = proto.LEVELS_DEFAULT
        self.assertEqual(proto.with_level(ones, proto.EFFECT_PHASER, 0.4), (1.0, 1.0, 0.4, 1.0, 1.0, 1.0, 1.0))
        self.assertEqual(proto.with_level(ones, proto.EFFECT_PHASER, 4), (1.0,) * 7)
        self.assertEqual(proto.with_level(ones, proto.EFFECT_AUTO_WAH, 0.3), (1.0,) * 6 + (0.3,))
        for bad_id in (7, -1, True):
            with self.subTest(effect=bad_id), self.assertRaises(ValueError):
                proto.with_level(ones, bad_id, 0.5)
        with self.assertRaises(ValueError):
            proto.with_level(ones, 0, float("nan"))

    def test_python_effect_ids_match_the_c_table(self):
        """EFFECT_IDS is the table's indices, read from the C, not restated:
        same count, same order, each Python name the C entry's name. An id is
        a number on the wire, so a reorder on either side must fail here."""
        source = C_DSP.read_text()
        table = re.search(r"const fx_effect_desc FX_EFFECTS\[\] = \{(.*?)\n\};", source, re.S)
        self.assertIsNotNone(table, "FX_EFFECTS table not found in fx_dsp.c")
        names = re.findall(r'\{\s*"([a-z0-9_-]+)"', table.group(1))
        self.assertEqual(names, ["chorus", "drive", "phaser", "tremolo", "flanger", "rotary", "auto-wah"])
        self.assertEqual(proto.EFFECT_IDS, tuple(range(len(names))))
        self.assertEqual(sorted(proto.EFFECT_NAMES), list(proto.EFFECT_IDS))
        for effect_id in proto.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                # The C table's name is the DSP's own; the screen says "Tube drive".
                display = {"drive": "tube drive"}.get(names[effect_id], names[effect_id])
                self.assertEqual(proto.EFFECT_NAMES[effect_id].lower(), display)
        # Pinned numbers: ids are on the wire and in every stored patch, so
        # the first five keep theirs and the two added 2026-10-13 append.
        self.assertEqual(
            (proto.EFFECT_CHORUS, proto.EFFECT_TUBE_DRIVE, proto.EFFECT_PHASER,
             proto.EFFECT_TREMOLO, proto.EFFECT_FLANGER, proto.EFFECT_ROTARY, proto.EFFECT_AUTO_WAH),
            (0, 1, 2, 3, 4, 5, 6),
        )

    def test_every_shipped_id_encodes(self):
        for effect_id in proto.EFFECT_IDS:
            with self.subTest(effect=effect_id):
                self.assertEqual(proto.encode(effect_id, 0.25), f"fx {effect_id} 0.250000".encode())

    def test_chain_encoding(self):
        self.assertEqual(proto.encode_chain(0b0101, 0.5), b"chain 5 0.500000")
        self.assertEqual(proto.encode_chain(proto.MASK_NONE, 0.5), b"chain 0 0.500000")
        self.assertEqual(proto.encode_chain(proto.MASK_ALL, 1), b"chain 127 1.000000")
        self.assertEqual(proto.encode_chain(0b11111, 1), b"chain 31 1.000000")
        self.assertEqual(proto.encode_chain(1 << proto.EFFECT_FLANGER, 0.5), b"chain 16 0.500000")
        self.assertEqual(proto.encode_chain(1, -0.3), b"chain 1 0.000000")
        self.assertEqual(proto.encode_chain(1, 7.0), b"chain 1 1.000000")

    def test_chain_refuses_what_it_cannot_say_honestly(self):
        for bad_depth in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(depth=bad_depth), self.assertRaises(ValueError):
                proto.encode_chain(1, bad_depth)
        for bad_mask in (128, 0b10000001, -1, True, 1.0, "1", None):
            with self.subTest(mask=bad_mask), self.assertRaises(ValueError):
                proto.encode_chain(bad_mask, 0.5)

    def test_mask_helpers(self):
        D, P, C, T, F = (proto.EFFECT_TUBE_DRIVE, proto.EFFECT_PHASER, proto.EFFECT_CHORUS,
                         proto.EFFECT_TREMOLO, proto.EFFECT_FLANGER)
        R, W = proto.EFFECT_ROTARY, proto.EFFECT_AUTO_WAH
        self.assertEqual(proto.CHAIN_ORDER, (D, W, P, F, C, R, T))
        self.assertEqual(sorted(proto.CHAIN_ORDER), list(proto.EFFECT_IDS))
        self.assertEqual(proto.MASK_ALL, 0b1111111)
        self.assertEqual(proto.EFFECT_NAMES[F], "Flanger")
        self.assertEqual((proto.EFFECT_NAMES[R], proto.EFFECT_NAMES[W]), ("Rotary", "Auto-wah"))
        # The flanger runs between the phaser and the chorus.
        self.assertEqual(proto.effects_in(proto.mask_of([T, C, F, P])), (P, F, C, T))
        # The auto-wah right after the drive, the rotary just before the tremolo.
        self.assertEqual(proto.effects_in(proto.mask_of([T, R, C, W, D])), (D, W, C, R, T))
        # The old five keep their relative order.
        old = tuple(i for i in proto.CHAIN_ORDER if i < 5)
        self.assertEqual(old, (D, P, F, C, T))
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
        for bad in (7, -1, True, "0"):
            with self.subTest(effect=bad):
                with self.assertRaises(ValueError):
                    proto.mask_of([bad])
                with self.assertRaises(ValueError):
                    proto.toggled(0, bad)
        with self.assertRaises(ValueError):
            proto.effects_in(128)

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
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        ones = " ".join(["1.000000"] * proto.MAX_LEVELS)

        cases: list[tuple[str, str]] = []
        for mask in range(proto.MASK_ALL + 1):
            for depth in (0.0, 0.000001, 0.25, 0.5, 0.999999, 1.0):
                # `chain` is every level 1.0.
                cases.append((proto.encode_chain(mask, depth).decode("ascii"), f"ok {mask} {depth:.6f} {ones}"))
        for mask in range(proto.MASK_ALL + 1):
            for depth in (0.0, 0.5, 1.0):
                for levels in LEVEL_GRID:
                    want = " ".join(f"{v:.6f}" for v in levels)
                    pad = " ".join(["1.000000"] * (proto.MAX_LEVELS - len(levels)))
                    cases.append(
                        (
                            proto.encode_chain_levels(mask, depth, levels).decode("ascii"),
                            f"ok {mask} {depth:.6f} {want} {pad}",
                        )
                    )
        # The first protocol's single-effect datagram is the one-bit mask.
        for effect_id in proto.EFFECT_IDS:
            cases.append((proto.encode(effect_id, 0.5).decode("ascii"), f"ok {proto.mask_of([effect_id])} 0.500000 {ones}"))
        got = _parse_lines(binary, [wire for wire, _ in cases])
        for (wire, want), answer in zip(cases, got, strict=True):
            with self.subTest(wire=wire):
                self.assertEqual(answer, want)

    def test_levels_are_append_only_on_the_wire(self):
        """A sender with fewer levels than the client has effects leaves the
        rest at 1.0; one with more (a future sixth effect) still parses, up to
        the client's FX_MAX_EFFECTS slots; past that it is refused."""
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        pad = lambda n: " ".join(["1.000000"] * n)  # noqa: E731
        cases = [
            ("chainl 5 0.5", f"ok 5 0.500000 {pad(8)}"),
            ("chainl 5 0.5 0.25", f"ok 5 0.500000 0.250000 {pad(7)}"),
            ("chainl 5 0.5 0.1 0.2 0.3 0.4 0.5 0.6", f"ok 5 0.500000 0.100000 0.200000 0.300000 0.400000 0.500000 0.600000 {pad(2)}"),
            ("chainl 5 0.5 " + " ".join(["0.5"] * 8), "ok 5 0.500000 " + " ".join(["0.500000"] * 8)),
            ("chainl 5 0.5 " + " ".join(["0.5"] * 9), "reject"),
            # Out-of-range levels parse as sent; the client clamps them.
            ("chainl 5 0.5 2 -1", f"ok 5 0.500000 2.000000 -1.000000 {pad(6)}"),
            ("chainl 5 0.5 0.25\n", f"ok 5 0.500000 0.250000 {pad(7)}"),
        ]
        got = _parse_lines(binary, [c for c, _ in cases])
        for (wire, want), answer in zip(cases, got, strict=True):
            with self.subTest(wire=wire):
                self.assertEqual(answer, want)

    def test_junk_changes_nothing(self):
        """Things a stray loopback sender might send must change nothing. (A
        mask naming an effect the build lacks, e.g. 32, parses; the client
        refuses it against its table.)"""
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        junk = ["fx", "fx 0", "fx 0 nan", "fx 0 inf", "fx -1 0.5",
                "fx 0x1 0.5", "fx 0 0.5 extra", "fx0 0.5", "fx 1e3 0.5", "fx 8 0.5", "",
                "chain", "chain 5", "chain -1 0.5", "chain 0x5 0.5", "chain 5 nan",
                "chain 5 0.5 extra", "chain5 0.5", "chain 256 0.5", "chain 5.0 0.5",
                "chains 5 0.5",
                # The levels form: no depth, a bad level, glued numbers, levels on `chain`.
                "chainl", "chainl 5", "chainl5 0.5 1", "chainl 5 0.5 nan", "chainl 5 0.5 inf",
                "chainl 5 0.5 1 x", "chainl 5 0.5 0.50.5", "chainl 5 0.5,1", "chainl 256 0.5 1",
                "chainl -1 0.5 1", "chain 5 0.5 1", "chainlx 5 0.5 1", "chainl 5 0.5 1e40"]
        got = _parse_lines(binary, junk)
        for text, answer in zip(junk, got, strict=True):
            with self.subTest(junk=text):
                self.assertEqual(answer, "reject")

    def test_bass_encoding(self):
        self.assertEqual(proto.encode_bass(120), b"bass 120.000000")
        self.assertEqual(proto.encode_bass(proto.BASS_OFF), b"bass 0.000000")
        # Above 0, clamped to the range this side offers.
        self.assertEqual(proto.encode_bass(20.0), b"bass 80.000000")
        self.assertEqual(proto.encode_bass(5000.0), b"bass 300.000000")
        for bad in (float("nan"), float("inf"), -1.0, True, "120", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                proto.encode_bass(bad)

    def test_bass_value(self):
        for good in (0, 0.0, 80, 120.0, 300):
            self.assertEqual(proto.bass_value(good), float(good))
        for bad in (79.9, 300.5, -1, float("nan"), True, "120", None, [120]):
            with self.subTest(bad=bad):
                self.assertIsNone(proto.bass_value(bad))

    def test_bass_steps(self):
        steps = proto.BASS_STEPS_HZ
        self.assertEqual(steps[0], proto.BASS_OFF)
        self.assertEqual((steps[1], steps[-1]), (proto.BASS_MIN_HZ, proto.BASS_MAX_HZ))
        self.assertEqual(list(steps), sorted(set(steps)))
        self.assertIn(proto.BASS_DEFAULT_HZ, steps)
        self.assertTrue(all(proto.bass_value(hz) == hz for hz in steps))

    def test_the_bass_range_is_inside_the_clients(self):
        """What this side sends, the client takes as sent (no clamp of its own
        moves it)."""
        text = (NATIVE / "fx_dsp.h").read_text()
        lo = float(re.search(r"#define\s+FX_BASS_MIN_HZ\s+([\d.]+)f?", text).group(1))
        hi = float(re.search(r"#define\s+FX_BASS_MAX_HZ\s+([\d.]+)f?", text).group(1))
        self.assertLessEqual(lo, proto.BASS_MIN_HZ)
        self.assertGreaterEqual(hi, proto.BASS_MAX_HZ)

    def test_bass_round_trips_through_the_real_parser_and_old_messages_keep_their_meaning(self):
        """`bass` is a datagram of its own: the client's parser reads it, and
        every older message reads exactly as it did beside it."""
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        ones = " ".join(["1.000000"] * proto.MAX_LEVELS)
        cases = [(proto.encode_bass(hz).decode("ascii"), f"bass {hz:.6f}") for hz in proto.BASS_STEPS_HZ]
        cases += [
            ("bass 1000", "bass 1000.000000"),   # out of the UI's range: parsed, the engine clamps
            ("  bass\t75.5 ", "bass 75.500000"),
            (proto.encode_chain(5, 0.5).decode("ascii"), f"ok 5 0.500000 {ones}"),
            (proto.encode(1, 0.25).decode("ascii"), f"ok 2 0.250000 {ones}"),
            ("chainl 5 0.5 0.25", "ok 5 0.500000 0.250000 " + " ".join(["1.000000"] * 7)),
        ]
        got = _parse_lines(binary, [c for c, _ in cases])
        for (wire, want), answer in zip(cases, got, strict=True):
            with self.subTest(wire=wire):
                self.assertEqual(answer, want)

    def test_bass_junk_changes_nothing(self):
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        junk = ["bass", "bass ", "bass -1", "bass nan", "bass inf", "bass 120 x", "bass 120 80",
                "bassx 120", "bass120", "bass 0x10", "bass 1e40", "chainl 5 0.5 1 bass 120"]
        got = _parse_lines(binary, junk)
        for text, answer in zip(junk, got, strict=True):
            with self.subTest(junk=text):
                self.assertEqual(answer, "reject")

    def test_the_client_reads_bass_and_starts_off(self):
        """The client stores what `bass` says, and nothing else does: with no
        `bass` it stays Off (0), the chain as it was before the bypass."""
        text = C_CLIENT.read_text()
        self.assertRegex(text, r"static _Atomic float g_target_bass = 0\.0f;")
        self.assertIn("fx_parse_bass(buf, &bass)", text)
        self.assertIn("fx_engine_set_bass(&g_engine", text)

    def test_gain_encoding(self):
        self.assertEqual(proto.encode_gain(1.0), b"gain 1.00000000")
        self.assertEqual(proto.encode_gain(1), b"gain 1.00000000")
        self.assertEqual(proto.encode_gain(0.0), b"gain 0.00000000")
        self.assertEqual(proto.encode_gain(10 ** (12 / 20)), b"gain 3.98107171")
        self.assertEqual(proto.encode_gain(10 ** (-90 / 20)), b"gain 0.00003162")
        # Above the client's ceiling, held to it, as the client would.
        top = f"gain {10 ** (proto.GAIN_MAX_DB / 20):.8f}".encode()
        self.assertEqual(proto.encode_gain(1e6), top)
        for bad in (float("nan"), float("inf"), -0.5, True, "1.0", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                proto.encode_gain(bad)

    def test_the_gain_ceiling_matches_the_c(self):
        found = re.search(r"#define\s+FX_GAIN_MAX_DB\s+([\d.]+)", (NATIVE / "fx_dsp.h").read_text())
        self.assertIsNotNone(found, "FX_GAIN_MAX_DB not found in fx_dsp.h")
        self.assertEqual(float(found.group(1)), proto.GAIN_MAX_DB)
        self.assertEqual(proto.GAIN_MAX_DB, 48.0)

    def test_gain_round_trips_through_the_real_parser(self):
        """Every value the encoder writes, read back by the client's parser,
        and every older message reads as it did beside it."""
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        ones = " ".join(["1.000000"] * proto.MAX_LEVELS)
        cases = []
        for db in (-120.0, -90.0, -60.0, -30.0, -12.0, -0.1, 0.0, 0.1, 6.0, 12.0, 30.0, 47.9,
                   48.0, 60.0):
            wire = proto.encode_gain(10 ** (db / 20)).decode("ascii")
            # The client holds a float: the value sent, to float precision.
            sent = float(wire.split()[1])
            cases.append((wire, f"gain {struct.unpack('f', struct.pack('f', sent))[0]:.8f}"))
        cases += [
            ("gain 0", "gain 0.00000000"),
            ("  gain\t2.5 ", "gain 2.50000000"),
            ("gain 1000", "gain 1000.00000000"),   # parsed as sent; the engine clamps
            (proto.encode_bass(120).decode("ascii"), "bass 120.000000"),
            (proto.encode_chain(5, 0.5).decode("ascii"), f"ok 5 0.500000 {ones}"),
        ]
        got = _parse_lines(binary, [c for c, _ in cases])
        for (wire, want), answer in zip(cases, got, strict=True):
            with self.subTest(wire=wire):
                self.assertEqual(answer, want)

    def test_gain_junk_changes_nothing(self):
        _need_compiler(self)
        binary, build = _selftest_binary()
        self.assertEqual(build.returncode, 0, build.stderr)
        junk = ["gain", "gain ", "gain -1", "gain -0.5", "gain nan", "gain NaN", "gain inf",
                "gain 1 x", "gain 1 2", "gainx 1", "gain1", "gain 0x10", "gain 1e3", "gain 1e40",
                "gain +1", "gain 1,5", "gains 1", "chainl 5 0.5 1 gain 2"]
        got = _parse_lines(binary, junk)
        for text, answer in zip(junk, got, strict=True):
            with self.subTest(junk=text):
                self.assertEqual(answer, "reject")

    def test_the_client_reads_gain_and_starts_at_unity(self):
        """The client stores what `gain` says and hands it to the engine every
        period; with no `gain` it stays at unity, the insert as it was."""
        text = C_CLIENT.read_text()
        self.assertRegex(text, r"static _Atomic float g_target_gain = 1\.0f;")
        self.assertIn("fx_parse_gain(buf, &gain)", text)
        self.assertIn("fx_engine_set_gain(&g_engine", text)
        self.assertIn("atomic_is_lock_free(&g_target_gain)", text)

    def test_the_level_slots_match_the_c(self):
        found = re.search(r"#define\s+FX_MAX_EFFECTS\s+(\d+)", (NATIVE / "fx_dsp.h").read_text())
        self.assertIsNotNone(found)
        self.assertEqual(int(found.group(1)), proto.MAX_LEVELS)

    def test_the_longest_datagram_fits_the_client_buffer(self):
        """The client ignores a datagram that fills its receive buffer (it may
        have been cut short, and a cut number still parses). The longest the
        encoder can write, and one with every level slot at that width, fit."""
        found = re.search(r"char buf\[(\d+)\];", C_CLIENT.read_text())
        self.assertIsNotNone(found, "no receive buffer in mpe-fx-host.c")
        size = int(found.group(1))
        longest = proto.encode_chain_levels(proto.MASK_ALL, 1.0, [1.0] * len(proto.EFFECT_IDS))
        self.assertLess(len(longest), size - 1)
        every_slot = b"chainl 255 1.000000 " + b" ".join([b"1.000000"] * proto.MAX_LEVELS)
        self.assertLess(len(every_slot), size - 1)


#: Level rows for the round trip: by effect id, the edges and the in-betweens.
LEVEL_GRID = (
    (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.25, 0.5, 0.75, 1.0, 0.0, 0.5, 0.25),
    (0.000001, 0.999999, 0.333333, 0.5, 0.123456, 0.654321, 0.000002),
)


def _parse_lines(binary: Path, lines: list[str]) -> list[str]:
    """Each line through the client's parser (`fx-selftest --parse-lines`)."""
    for line in lines:
        assert "\n" not in line.rstrip("\n"), line
    proc = subprocess.run(
        [str(binary), "--parse-lines"], input="".join(l.rstrip("\n") + "\n" for l in lines),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


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


class GainStateRestart(unittest.TestCase):
    """A restarted client starts at the last stage gain, not at unity for up to
    the sender's resend interval. The file's round trip, its refusals and the
    first-frame level and the cap at unity are fx_selftest's "stage gain state
    file", "stage gain restart" and "stage gain restore cap" checks; these read the client's C, since there is no jackd here."""

    def _function(self, source: str, signature: str) -> str:
        start = source.index(signature)
        end = source.index("\n}\n", start)
        return source[start:end]

    def test_main_restores_before_activating(self):
        main = self._function(C_CLIENT.read_text(), "int main(")
        # The restore, not the plain read: a restart keeps a cut, never a
        # boost (fx_selftest "stage gain restore cap").
        self.assertNotIn("fx_gain_state_read(", main)
        read = main.index("fx_gain_state_restore(g_gain_state_path, &restored)")
        self.assertLess(read, main.index("jack_activate(g_client)"))
        self.assertLess(main.index("fx_engine_init("), read)
        restore = main[read:main.index("jack_activate(g_client)")]
        self.assertIn("fx_engine_start_gain(&g_engine, restored)", restore)
        self.assertIn("atomic_store_explicit(&g_target_gain, restored", restore)

    def test_the_control_thread_writes_and_the_callback_does_not(self):
        source = C_CLIENT.read_text()
        control = self._function(source, "static void *control_thread(")
        self.assertIn("fx_gain_state_write(g_gain_state_path, gain)", control)
        self.assertIn("if (gain != written)", control, "a resend of the same value writes nothing")
        process = self._function(source, "static int process(")
        self.assertNotIn("fx_gain_state", process)

    def test_the_path_matches_the_python_and_the_unit(self):
        header = (NATIVE / "fx_gain_state.h").read_text()
        match = re.search(r'#define FX_GAIN_STATE_DEFAULT "([^"]+)"', header)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), proto.GAIN_STATE_DEFAULT)
        self.assertIn('getenv("MPE_FX_GAIN_STATE")', C_CLIENT.read_text())
        # /run/mpe is the unit's RuntimeDirectory, kept across its restarts.
        self.assertTrue(proto.GAIN_STATE_DEFAULT.startswith("/run/mpe/"))
        lines = {line.strip() for line in UNIT.read_text().splitlines()}
        self.assertIn("RuntimeDirectory=mpe", lines)
        self.assertIn("RuntimeDirectoryPreserve=yes", lines)

    def test_python_reads_what_the_client_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state"
            self.assertIsNone(proto.read_gain_state(str(path)))
            for text, want in (("gain 0.10000000\n", 0.1), ("gain 1.00000000", 1.0)):
                path.write_text(text)
                self.assertEqual(proto.read_gain_state(str(path)), want)
            for bad in ("", "gain -1\n", "gain 1e3\n", "gain nan\n", "gain 1\ngain 2\n", "x 1\n",
                        "gain " + "0" * 70):
                path.write_text(bad)
                self.assertIsNone(proto.read_gain_state(str(path)), bad)
            old = os.environ.get("MPE_FX_GAIN_STATE")
            os.environ["MPE_FX_GAIN_STATE"] = str(path)
            try:
                self.assertEqual(proto.gain_state_path(), str(path))
            finally:
                if old is None:
                    os.environ.pop("MPE_FX_GAIN_STATE", None)
                else:
                    os.environ["MPE_FX_GAIN_STATE"] = old


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
