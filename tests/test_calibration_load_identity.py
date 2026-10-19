"""A calibration measures the patch it names, or nothing.

2026-10-19: same-named patches read as each other and five different patches
read about -24 LUFS in a row. Surge loads a patch in the background and refuses
a missing file without a reply, so a load that has not taken (yet, or at all)
leaves the old patch playing, and the calibration measured it. Each load now
waits for Surge to report the requested path (``/q/patch``) before anything is
sent or measured, and the identity check is the whole path, never the stem.
"""

from __future__ import annotations

import contextlib
import io
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from patch_browser.patch_loader import PatchLoader, same_patch_path  # noqa: E402
from test_calibration_integrity import load_cal_module  # noqa: E402

FACTORY = "/surge/patches_factory/Basses/Bass 1"
BLUELIGHT = "/surge/patches_3rdparty/Bluelight/Basses/Bass 1"


class FakeOscClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def send_message(self, address: str, value) -> None:
        self.messages.append((address, value))

    def sent(self, address: str) -> list:
        return [v for a, v in self.messages if a == address]


def _loader() -> PatchLoader:
    loader = PatchLoader(fx_policy=mock.Mock(enabled=False), fx_store=mock.Mock())
    loader.osc_client = FakeOscClient()
    loader.osc_enabled = True
    return loader


@contextlib.contextmanager
def _quiet_load(loader: PatchLoader):
    with (
        mock.patch("patch_browser.patch_loader.time.sleep"),
        mock.patch.object(loader, "_apply_playback_policy"),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        yield


class SamePatchPathTests(unittest.TestCase):
    def test_same_stem_in_another_folder_is_another_patch(self) -> None:
        self.assertFalse(same_patch_path(FACTORY, BLUELIGHT))

    def test_the_same_path_matches(self) -> None:
        self.assertTrue(same_patch_path(BLUELIGHT, BLUELIGHT))
        self.assertTrue(same_patch_path(BLUELIGHT + "/", BLUELIGHT))

    def test_no_reply_is_no_match(self) -> None:
        self.assertFalse(same_patch_path(None, BLUELIGHT))
        self.assertFalse(same_patch_path("", BLUELIGHT))


class LoadByPathTests(unittest.TestCase):
    def test_same_stem_patches_each_send_their_own_path(self) -> None:
        loader = _loader()
        with _quiet_load(loader):
            self.assertTrue(loader.load_patch(FACTORY + ".fxp", capture_hold=False))
            self.assertTrue(loader.load_patch(BLUELIGHT + ".fxp", capture_hold=False))
        self.assertEqual(loader.osc_client.sent("/patch/load"), [[FACTORY], [BLUELIGHT]])
        self.assertEqual(loader._loaded_patch_path, BLUELIGHT + ".fxp")

    def test_a_load_is_never_skipped_for_a_matching_name(self) -> None:
        # No "already loaded" shortcut by name: B after A, and A again, all go out.
        loader = _loader()
        with _quiet_load(loader):
            for path in (FACTORY, BLUELIGHT, FACTORY):
                loader.load_patch(path + ".fxp", capture_hold=False)
        self.assertEqual(
            loader.osc_client.sent("/patch/load"), [[FACTORY], [BLUELIGHT], [FACTORY]]
        )


class ConfirmedLoadTests(unittest.TestCase):
    def _load(self, loader: PatchLoader, replies: list, path: str = BLUELIGHT, timeout: float = 5.0):
        replies = list(replies)

        def reply(*_a, **_k):
            return replies.pop(0) if len(replies) > 1 else replies[0]

        with _quiet_load(loader), mock.patch.object(loader, "query_loaded_patch_path", side_effect=reply):
            return loader.load_patch(path + ".fxp", capture_hold=False, confirm_timeout_s=timeout)

    def test_confirmed_when_surge_reports_the_requested_path(self) -> None:
        loader = _loader()
        self.assertTrue(self._load(loader, [BLUELIGHT]))
        self.assertTrue(loader.osc_client.sent("/param/a/amp/volume"))

    def test_waits_through_the_previous_patch_until_the_new_one_lands(self) -> None:
        loader = _loader()
        self.assertTrue(self._load(loader, [FACTORY, FACTORY, BLUELIGHT]))

    def test_a_same_stem_patch_reported_is_a_failed_load(self) -> None:
        loader = _loader()
        loader._loaded_patch_path = FACTORY + ".fxp"
        self.assertFalse(self._load(loader, [FACTORY], timeout=0.0))
        # Nothing follows a load that did not take: no volume, no new identity.
        self.assertEqual(loader.osc_client.sent("/param/a/amp/volume"), [])
        self.assertEqual(loader._loaded_patch_path, FACTORY + ".fxp")

    def test_no_reply_is_a_failed_load(self) -> None:
        loader = _loader()
        self.assertFalse(self._load(loader, [None], timeout=0.0))
        self.assertEqual(loader.osc_client.sent("/param/a/amp/volume"), [])

    def test_without_confirm_surge_is_not_asked(self) -> None:
        loader = _loader()
        with _quiet_load(loader), mock.patch.object(loader, "query_loaded_patch_path") as q:
            self.assertTrue(loader.load_patch(BLUELIGHT + ".fxp", capture_hold=False))
        q.assert_not_called()


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QueryLoadedPatchPathTests(unittest.TestCase):
    """The real socket path: Surge's reply arrives among parameter echoes."""

    def _query(self, datagrams: list[bytes]) -> str | None:
        port = _free_udp_port()
        loader = PatchLoader(osc_out_port=port, fx_policy=mock.Mock(enabled=False), fx_store=mock.Mock())
        loader.osc_enabled = True

        class Surge:
            def send_message(self, address, _value):
                if address != "/q/patch":
                    return

                def answer():
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as out:
                        for data in datagrams:
                            out.sendto(data, ("127.0.0.1", port))

                threading.Thread(target=answer).start()

        loader.osc_client = Surge()
        return loader.query_loaded_patch_path(timeout_s=1.0)

    @staticmethod
    def _osc(address: str, *args) -> bytes:
        from pythonosc.osc_message_builder import OscMessageBuilder

        builder = OscMessageBuilder(address=address)
        for arg in args:
            builder.add_arg(arg)
        return builder.build().dgram

    def test_reads_the_patch_path_past_parameter_echoes(self) -> None:
        echo = self._osc("/param/a/amp/volume", 0.5, "-6.02 dB")
        got = self._query([echo, self._osc("/patch", BLUELIGHT)])
        self.assertEqual(got, BLUELIGHT)

    def test_no_patch_message_is_none(self) -> None:
        self.assertIsNone(self._query([self._osc("/param/a/amp/volume", 0.5)]))


class CalibrationLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _calibrate(self, load_ok: bool):
        store = self.cal.PatchNormalizationStore(self.tmp / "norm.json")
        loader = mock.Mock()
        loader.load_patch.return_value = load_ok
        with (
            mock.patch.object(self.cal, "measure_strike_anchor_lufs", return_value=(-20.0, -6.0)) as strike,
            mock.patch.object(self.cal, "measure_sustain_anchor_lufs", return_value=(-22.0, -6.0)),
            mock.patch.object(self.cal, "measure_light_touch_lufs", return_value=None),
            mock.patch.object(self.cal, "finalize_gain_with_closed_loop", return_value=(1.0, -18.0, -9.0)),
            mock.patch.object(self.cal.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = self.cal.calibrate_patch(
                Path(BLUELIGHT + ".fxp"), loader, store, audio_device="hw:9,0",
                mock_lufs=None, dry_run=False, midi_out=object(),
            )
        return result, loader, strike, store

    def test_the_load_asks_for_confirmation_by_full_path(self) -> None:
        result, loader, _strike, _store = self._calibrate(True)
        self.assertTrue(result.ok)
        args, kwargs = loader.load_patch.call_args
        self.assertEqual(args, (BLUELIGHT + ".fxp",))
        self.assertEqual(kwargs["confirm_timeout_s"], self.cal.PATCH_LOAD_CONFIRM_TIMEOUT_S)
        self.assertGreater(self.cal.PATCH_LOAD_CONFIRM_TIMEOUT_S, 0)

    def test_an_unconfirmed_load_is_a_failure_not_a_measurement(self) -> None:
        result, _loader, strike, store = self._calibrate(False)
        self.assertFalse(result.ok)
        self.assertTrue(result.load_failed)
        self.assertIsNone(result.lufs_strike)
        strike.assert_not_called()
        self.assertFalse((self.tmp / "norm.json").exists())
        del store

    def test_verify_asks_for_confirmation_too(self) -> None:
        store = mock.Mock()
        store.get_entry.return_value = {"gain_db": 1.0}
        loader = mock.Mock()
        loader.load_patch.return_value = False
        with contextlib.redirect_stderr(io.StringIO()):
            result = self.cal.verify_patch(
                Path(BLUELIGHT + ".fxp"), loader, store, audio_device="hw:9,0", midi_out=object()
            )
        self.assertFalse(result.ok)
        self.assertEqual(loader.load_patch.call_args.kwargs["confirm_timeout_s"],
                         self.cal.PATCH_LOAD_CONFIRM_TIMEOUT_S)

    def test_calibration_surge_answers_patch_queries(self) -> None:
        # /q/patch replies only reach a Surge started with an OSC out port.
        for start in (self.cal.start_surge_loopback, self.cal.start_surge_standalone):
            cli = self.tmp / "surge-xt-cli"
            cli.write_text("")
            with (
                mock.patch.object(self.cal, "surge_cli_path", return_value=cli),
                mock.patch.object(self.cal, "ensure_snd_aloop"),
                mock.patch.object(self.cal, "resolve_surge_loopback_interface", return_value="x"),
                mock.patch.object(self.cal, "resolve_surge_standalone_interface", return_value="x"),
                mock.patch.object(self.cal, "detect_script_path", return_value=cli),
                mock.patch.object(self.cal.Path, "home", return_value=self.tmp),
                mock.patch.object(self.cal.subprocess, "Popen") as popen,
                mock.patch.object(self.cal.time, "sleep"),
            ):
                start()
            self.assertIn(f"--osc-out-port={self.cal.OSC_OUT_PORT}", popen.call_args.args[0])

    def _run_main(self, load_results: list[bool]) -> tuple[int, mock.Mock, str]:
        roots = [self.tmp / "patches_factory", self.tmp / "patches_3rdparty"]
        for i in range(len(load_results)):
            path = roots[0] / "Basses" / f"Bass {i}.fxp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        roots[1].mkdir(exist_ok=True)
        loader = mock.Mock(osc_enabled=True)
        loader.load_patch.side_effect = list(load_results)
        # Real-looking, distinct readings so the plateau guard stays out of it.
        strikes = iter([(-14.0 - 3 * i, -6.0) for i in range(len(load_results))])
        argv = [
            "calibrate", "--force", "--no-touch-cal",
            "--output", str(self.tmp / "norm.json"),
            "--pressure-output", str(self.tmp / "pressure.json"),
            "--no-use-loopback", "--audio-device", "hw:9,0",
        ]
        err = io.StringIO()
        with (
            mock.patch.object(self.cal, "SURGE_PATCH_DIRS", roots),
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(self.cal, "should_restart_surge_for_standalone", return_value=False),
            mock.patch.object(self.cal, "PatchLoader", return_value=loader),
            mock.patch.object(self.cal, "wait_for_surge_midi_port", return_value=0),
            mock.patch.object(self.cal, "open_midi_out", return_value=object()),
            mock.patch.object(self.cal, "close_midi_out"),
            mock.patch.object(self.cal, "FAILURE_REPORT_PATH", self.tmp / "failure.json"),
            mock.patch.object(self.cal.signal, "signal"),
            mock.patch.object(self.cal, "measure_strike_anchor_lufs", side_effect=lambda *a: next(strikes)),
            mock.patch.object(self.cal, "measure_sustain_anchor_lufs", return_value=(-22.0, -6.0)),
            mock.patch.object(self.cal, "finalize_gain_with_closed_loop", return_value=(1.0, -18.0, -9.0)),
            mock.patch.object(self.cal.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = self.cal.main()
        return code, loader, err.getvalue()

    def test_loads_failing_back_to_back_halt_the_run(self) -> None:
        code, loader, err = self._run_main([False] * 5)
        self.assertEqual(code, 1)
        self.assertEqual(loader.load_patch.call_count, self.cal.LOAD_FAILURES_TO_HALT)
        self.assertIn("not confirmed", err)

    def test_a_confirmed_load_resets_the_count(self) -> None:
        code, loader, err = self._run_main([False, False, True, False, False])
        self.assertEqual(loader.load_patch.call_count, 5)
        self.assertNotIn("in a row were not confirmed", err)
        del code


class PlateauJitterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cal = load_cal_module()

    def _halted_at(self, readings) -> int | None:
        guard = self.cal.PlateauGuard()
        for i, (strike, sustain) in enumerate(readings):
            if guard.add(Path(f"/p/{i}.fxp"), strike, sustain):
                return i
        return None

    def test_a_plateau_with_0_32_db_jitter_halts(self) -> None:
        # The second reading sits 0.32 dB off the first: measured against the
        # first reading, it restarted the count and the plateau went on.
        readings = [(-24.07, -26.0), (-23.75, -25.8), (-24.04, -26.1), (-23.90, -25.9)]
        self.assertEqual(self._halted_at(readings), 3)

    def test_one_patch_off_the_plateau_spoils_the_window(self) -> None:
        readings = [(-24.0, -26.0), (-24.1, -26.0), (-22.0, -26.0), (-24.0, -26.0)]
        self.assertIsNone(self._halted_at(readings))

    def test_a_spread_wider_than_the_band_is_not_a_plateau(self) -> None:
        readings = [(-24.0, -26.0), (-24.7, -26.0), (-24.0, -26.0), (-24.7, -26.0),
                    (-24.0, -26.0), (-24.7, -26.0)]
        self.assertIsNone(self._halted_at(readings))


if __name__ == "__main__":
    unittest.main()
