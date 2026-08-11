"""Tests for counting ALSA xrun reports off arecord/aplay stderr."""

from __future__ import annotations

import io
import unittest

from patch_browser.looper_alsa_stderr import (
    MAX_ECHOED_LINES,
    AlsaStderrMonitor,
    XrunWindow,
    format_xrun_report,
    parse_xrun_line,
    session_xrun_total,
    start_alsa_stderr_monitors,
)


class ParseXrunLineTests(unittest.TestCase):
    def test_parses_aplay_underrun_length(self) -> None:
        self.assertAlmostEqual(
            parse_xrun_line("underrun!!! (at least 21.333 ms long)"), 21.333
        )

    def test_parses_arecord_overrun_length(self) -> None:
        self.assertAlmostEqual(parse_xrun_line("overrun!!! (at least 5.000 ms long)"), 5.0)

    def test_xrun_without_stated_length_counts_as_zero_ms(self) -> None:
        self.assertEqual(parse_xrun_line("underrun!!!"), 0.0)

    def test_non_xrun_line_returns_none(self) -> None:
        self.assertIsNone(
            parse_xrun_line("Recording raw data 'stdin' : Signed 16 bit Little Endian")
        )

    def test_blank_line_returns_none(self) -> None:
        self.assertIsNone(parse_xrun_line(""))


class MonitorAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.echoed: list[str] = []
        self.monitor = AlsaStderrMonitor("aplay", None, echo=self.echoed.append)

    def test_counts_worst_and_total(self) -> None:
        self.monitor.feed("underrun!!! (at least 10.000 ms long)")
        self.monitor.feed("underrun!!! (at least 42.500 ms long)")
        self.monitor.feed("underrun!!! (at least 2.500 ms long)")
        window = self.monitor.take_window()
        self.assertEqual(window.count, 3)
        self.assertAlmostEqual(window.worst_ms, 42.5)
        self.assertAlmostEqual(window.total_ms, 55.0)
        self.assertFalse(window.is_clean)

    def test_take_window_resets_but_session_total_accumulates(self) -> None:
        self.monitor.feed("underrun!!! (at least 8.000 ms long)")
        self.assertEqual(self.monitor.take_window().count, 1)

        second = self.monitor.take_window()
        self.assertTrue(second.is_clean)
        self.assertEqual(second.worst_ms, 0.0)

        self.monitor.feed("underrun!!! (at least 3.000 ms long)")
        self.monitor.take_window()
        self.assertEqual(self.monitor.session_xruns, 2)

    def test_non_xrun_lines_are_echoed_not_counted(self) -> None:
        self.monitor.feed("Playing raw data 'stdin' : Signed 16 bit Little Endian")
        self.assertTrue(self.monitor.take_window().is_clean)
        self.assertEqual(self.echoed, ["[aplay] Playing raw data 'stdin' : Signed 16 bit Little Endian"])

    def test_echo_is_capped_so_a_broken_device_cannot_flood(self) -> None:
        for index in range(MAX_ECHOED_LINES + 25):
            self.monitor.feed(f"some ALSA chatter {index}")
        self.assertEqual(len(self.echoed), MAX_ECHOED_LINES)

    def test_xruns_are_never_capped(self) -> None:
        for _ in range(MAX_ECHOED_LINES + 25):
            self.monitor.feed("underrun!!! (at least 1.000 ms long)")
        self.assertEqual(self.monitor.take_window().count, MAX_ECHOED_LINES + 25)
        self.assertEqual(self.echoed, [])


class MonitorThreadTests(unittest.TestCase):
    def test_pump_drains_stream_to_eof(self) -> None:
        stream = io.BytesIO(
            b"Playing raw data 'stdin'\n"
            b"underrun!!! (at least 21.333 ms long)\n"
            b"underrun!!! (at least 1.000 ms long)\n"
        )
        monitor = AlsaStderrMonitor("aplay", stream, echo=lambda _: None)
        monitor.start()
        monitor._thread.join(timeout=5)
        self.assertFalse(monitor._thread.is_alive())
        self.assertEqual(monitor.session_xruns, 2)

    def test_missing_stream_is_a_no_op(self) -> None:
        monitor = AlsaStderrMonitor("aplay", None)
        monitor.start()
        self.assertTrue(monitor.take_window().is_clean)


class ReportFormattingTests(unittest.TestCase):
    def test_clean_window_reads_as_none(self) -> None:
        self.assertEqual(str(XrunWindow()), "none")

    def test_report_labels_each_stream(self) -> None:
        rec = AlsaStderrMonitor("arecord", None)
        play = AlsaStderrMonitor("aplay", None)
        play.feed("underrun!!! (at least 21.300 ms long)")
        report = format_xrun_report([rec, play])
        self.assertEqual(report, "arecord=none aplay=1(worst=21.3ms total=21.3ms)")

    def test_report_consumes_the_window(self) -> None:
        play = AlsaStderrMonitor("aplay", None)
        play.feed("underrun!!!")
        format_xrun_report([play])
        self.assertEqual(format_xrun_report([play]), "aplay=none")

    def test_session_total_spans_streams(self) -> None:
        rec = AlsaStderrMonitor("arecord", None)
        play = AlsaStderrMonitor("aplay", None)
        rec.feed("overrun!!!")
        play.feed("underrun!!!")
        play.feed("underrun!!!")
        self.assertEqual(session_xrun_total([rec, play]), 3)


class StartMonitorsTests(unittest.TestCase):
    def test_starts_one_monitor_per_process(self) -> None:
        class FakeProc:
            def __init__(self, payload: bytes) -> None:
                self.stderr = io.BytesIO(payload)

        rec = FakeProc(b"overrun!!! (at least 4.000 ms long)\n")
        play = FakeProc(b"")
        monitors = start_alsa_stderr_monitors(
            ("arecord", rec), ("aplay", play), echo=lambda _: None
        )
        self.assertEqual([m.label for m in monitors], ["arecord", "aplay"])
        for monitor in monitors:
            monitor._thread.join(timeout=5)
        self.assertEqual(session_xrun_total(monitors), 1)


if __name__ == "__main__":
    unittest.main()
