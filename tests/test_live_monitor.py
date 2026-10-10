import os
import re
import socket
import subprocess
import time
import unittest
from pathlib import Path

import tempfile

import live_monitor
from patch_browser import audio_engine
from apc_faders import CC_MAX
from live_monitor import LiveMonitor, LiveMonitorSender
from loop_mix import LoopMix
REPO_ROOT = Path(__file__).resolve().parent.parent

from sl_loop_states import (
    SL_STATE_OFF,
    SL_STATE_OVERDUBBING,
    SL_STATE_PLAYING,
    SL_STATE_RECORDING,
    SL_STATE_WAIT_START,
    SL_STATE_WAIT_STOP,
)


class WhichTrackAmIPlayingInto(unittest.TestCase):
    def test_nothing_capturing(self):
        self.assertIsNone(LiveMonitor().capturing_loop())

    def test_recording_claims_the_monitor(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING)
        self.assertEqual(mon.capturing_loop(), 3)

    def test_overdub_is_a_capture_too(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_OVERDUBBING)
        self.assertEqual(mon.capturing_loop(), 3)

    def test_waiting_to_start_is_not_yet_a_capture(self):
        """The take has not opened — changing level early belongs to no take."""
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_WAIT_START)
        self.assertIsNone(mon.capturing_loop())

    def test_a_take_waiting_for_the_boundary_still_holds_it(self):
        """The tail: the button is up, the engine has not closed the take."""
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING)
        mon.note_state(3, SL_STATE_WAIT_STOP)
        self.assertEqual(mon.capturing_loop(), 3)

    def test_playing_releases_it(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING)
        mon.note_state(3, SL_STATE_PLAYING)
        self.assertIsNone(mon.capturing_loop())

    def test_the_most_recent_capture_wins(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING)
        mon.note_state(5, SL_STATE_OVERDUBBING)
        self.assertEqual(mon.capturing_loop(), 5)

    def test_releasing_one_of_two_falls_back_to_the_other(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING)
        mon.note_state(5, SL_STATE_OVERDUBBING)
        mon.note_state(5, SL_STATE_OFF)
        self.assertEqual(mon.capturing_loop(), 3)

    def test_repeated_state_updates_do_not_stack(self):
        mon = LiveMonitor()
        for _ in range(5):
            mon.note_state(3, SL_STATE_RECORDING)
        mon.note_state(3, SL_STATE_PLAYING)
        self.assertIsNone(mon.capturing_loop())


class WhatLevelYouHearYourselfAt(unittest.TestCase):
    def test_idle_is_the_live_level(self):
        mon = LiveMonitor(live_gain=CC_MAX)
        self.assertEqual(mon.target_amp(LoopMix().wet_for), 1.0)

    def test_capturing_matches_what_the_track_will_play_back_at(self):
        """The monitor target *is* the loop's composed wet — not a copy of it."""
        mix = LoopMix()
        mix.messages_for(0, 100)  # anchor
        mix.messages_for(0, 60)   # pull that column down
        loop = mix.view.loops_for_column(0)[0]
        mon = LiveMonitor()
        mon.note_state(loop, SL_STATE_RECORDING)
        self.assertEqual(mon.target_amp(mix.wet_for), mix.wet_for(loop))
        self.assertLess(mon.target_amp(mix.wet_for), 1.0)

    def test_the_master_reaches_the_monitor_through_the_loop_level(self):
        mix = LoopMix()
        mon = LiveMonitor()
        mon.note_state(0, SL_STATE_RECORDING)
        before = mon.target_amp(mix.wet_for)
        mix.messages_for("master", 64)
        self.assertLess(mon.target_amp(mix.wet_for), before)

    def test_the_master_does_not_touch_the_idle_live_level(self):
        """Idle monitoring is your live level, so master stays a loops-vs-live balance."""
        mix = LoopMix()
        mon = LiveMonitor()
        mix.messages_for("master", 64)
        self.assertEqual(mon.target_amp(mix.wet_for), mon.live_amp())

    def test_the_level_returns_when_the_take_closes(self):
        mix = LoopMix()
        mix.messages_for(0, 100)
        mix.messages_for(0, 40)
        loop = mix.view.loops_for_column(0)[0]
        mon = LiveMonitor()
        mon.note_state(loop, SL_STATE_RECORDING)
        quiet = mon.target_amp(mix.wet_for)
        mon.note_state(loop, SL_STATE_PLAYING)
        self.assertEqual(mon.target_amp(mix.wet_for), mon.live_amp())
        self.assertNotEqual(quiet, mon.target_amp(mix.wet_for))

    def test_a_silent_column_monitors_silent(self):
        mix = LoopMix()
        mix.messages_for(0, 100)
        mix.messages_for(0, 0)
        loop = mix.view.loops_for_column(0)[0]
        mon = LiveMonitor()
        mon.note_state(loop, SL_STATE_RECORDING)
        self.assertEqual(mon.target_amp(mix.wet_for), 0.0)

    def test_the_target_never_boosts(self):
        mon = LiveMonitor()
        mon.note_state(0, SL_STATE_RECORDING)
        self.assertEqual(mon.target_amp(lambda _loop: 4.0), 1.0)


class Sending(unittest.TestCase):
    def _sender(self):
        sent = []
        return LiveMonitorSender(send=sent.append), sent

    def test_a_level_goes_out_once(self):
        sender, sent = self._sender()
        self.assertTrue(sender.send(0.5))
        self.assertEqual(sent, [b"gain 0.500000"])

    def test_an_unmoved_level_is_not_resent(self):
        sender, sent = self._sender()
        sender.send(0.5)
        self.assertFalse(sender.send(0.5))
        self.assertEqual(len(sent), 1)

    def test_a_moved_level_is_sent(self):
        sender, sent = self._sender()
        sender.send(0.5)
        self.assertTrue(sender.send(0.25))
        self.assertEqual(len(sent), 2)

    def test_out_of_range_is_clamped_not_refused(self):
        sender, sent = self._sender()
        sender.send(9.0)
        self.assertEqual(sent, [b"gain 1.000000"])

    def test_a_dead_socket_is_not_an_exception(self):
        sender = LiveMonitorSender(port=1)
        sender.open()
        try:
            sender.send(0.5)  # nothing is listening; must not raise
        finally:
            sender.close()


class AskingRatherThanAssuming(unittest.TestCase):
    """A held capture is verified by asking the engine, never by a timer.

    The first version expired captures on a time-to-live, written on the belief
    that SooperLooper streamed `state` ten times a second. It does not: it
    delivers on change. Measured against the real engine in `tests/engine/` on
    2026-09-20, five seconds inside a held RECORDING take produced one datagram.
    A TTL therefore expired live takes — +16 dB into the monitor, 2.1 s in.
    """

    def test_a_silent_engine_does_not_end_a_take(self):
        """Minutes of silence is what a long take sounds like on this protocol."""
        mix = LoopMix()
        mix.messages_for(0, 100)
        mix.messages_for(0, 40)
        loop = mix.view.loops_for_column(0)[0]
        mon = LiveMonitor()
        mon.note_state(loop, SL_STATE_RECORDING, now=0.0)
        quiet = mon.target_amp(mix.wet_for, now=0.0)
        self.assertLess(quiet, 1.0)
        # No further news for a full minute. The take is still the take.
        self.assertEqual(mon.capturing_loop(now=60.0), loop)
        self.assertEqual(mon.target_amp(mix.wet_for, now=60.0), quiet)

    def test_a_quiet_capture_is_asked_about_once(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING, now=0.0)
        self.assertIsNone(mon.needs_verification(now=live_monitor.VERIFY_AFTER_S - 0.01))
        self.assertEqual(mon.needs_verification(now=live_monitor.VERIFY_AFTER_S), 3)
        # Asked once — the caller must not be told to ask again while waiting.
        self.assertIsNone(mon.needs_verification(now=live_monitor.VERIFY_AFTER_S + 0.1))

    def test_an_answer_keeps_the_take_and_re_arms_the_question(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING, now=0.0)
        mon.needs_verification(now=live_monitor.VERIFY_AFTER_S)
        # The engine answers: still recording.
        answered_at = live_monitor.VERIFY_AFTER_S + 0.05
        mon.note_state(3, SL_STATE_RECORDING, now=answered_at)
        # Well past the window the unanswered question would have expired in.
        waited = answered_at + live_monitor.VERIFY_TIMEOUT_S + 0.5
        self.assertEqual(mon.capturing_loop(now=waited), 3)
        # And the clock restarts from the answer, so it is asked about again.
        self.assertEqual(
            mon.needs_verification(now=answered_at + live_monitor.VERIFY_AFTER_S), 3
        )

    def test_an_answer_that_the_take_ended_releases_the_monitor(self):
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING, now=0.0)
        mon.needs_verification(now=live_monitor.VERIFY_AFTER_S)
        mon.note_state(3, SL_STATE_PLAYING, now=live_monitor.VERIFY_AFTER_S + 0.05)
        self.assertIsNone(mon.capturing_loop(now=live_monitor.VERIFY_AFTER_S + 0.05))

    def _ignore_every_question(self, mon, loop):
        """Play a silent engine: let every question time out. Returns the clock."""
        now = live_monitor.VERIFY_AFTER_S
        asked = 0
        while mon.needs_verification(now=now) == loop:
            asked += 1
            now += live_monitor.VERIFY_TIMEOUT_S + 0.01
        return now, asked

    def test_one_lost_question_does_not_end_a_take(self):
        """The question is a datagram to a process under realtime load."""
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING, now=0.0)
        mon.needs_verification(now=live_monitor.VERIFY_AFTER_S)
        missed = live_monitor.VERIFY_AFTER_S + live_monitor.VERIFY_TIMEOUT_S + 0.01
        self.assertEqual(mon.capturing_loop(now=missed), 3)
        # ...and it is asked again rather than abandoned.
        self.assertEqual(mon.needs_verification(now=missed), 3)

    def test_an_engine_that_never_answers_releases_the_monitor(self):
        """An engine that cannot answer cannot tell us the take ended either."""
        mon = LiveMonitor()
        mon.note_state(3, SL_STATE_RECORDING, now=0.0)
        gave_up, asked = self._ignore_every_question(mon, 3)
        self.assertEqual(asked, live_monitor.VERIFY_ATTEMPTS)
        self.assertIsNone(mon.capturing_loop(now=gave_up))

    def test_giving_up_falls_back_to_the_live_level(self):
        """The safe direction: you hear yourself, rather than silence."""
        mix = LoopMix()
        mix.messages_for(0, 100)
        mix.messages_for(0, 0)  # this column is silent
        loop = mix.view.loops_for_column(0)[0]
        mon = LiveMonitor()
        mon.note_state(loop, SL_STATE_RECORDING, now=0.0)
        self.assertEqual(mon.target_amp(mix.wet_for, now=0.0), 0.0)
        gave_up, _asked = self._ignore_every_question(mon, loop)
        self.assertEqual(mon.target_amp(mix.wet_for, now=gave_up), mon.live_amp())

    def test_no_timer_expires_a_capture_anywhere_in_this_module(self):
        """The TTL is gone, not renamed. A regression here is inaudible until a take.

        `note_state` is the only thing that may drop a capture on the engine's
        word, and `needs_verification` the only thing that may drop one on
        silence — and only after asking.
        """
        source = (REPO_ROOT / "scripts/sooperlooper/live_monitor.py").read_text()
        self.assertNotIn("CAPTURE_TTL_S", source)
        # The name being gone is the weak half of this test; the behaviour is
        # the point, and it holds however the expiry is spelled.
        mon = LiveMonitor()
        mon.note_state(1, SL_STATE_RECORDING, now=0.0)
        # An hour of silence, and nobody ever asked. Still recording.
        self.assertEqual(mon.capturing_loop(now=3600.0), 1)


class ConcurrentUpdates(unittest.TestCase):
    """State arrives on a thread per datagram (ThreadingOSCUDPServer)."""

    def test_state_updates_from_many_threads_do_not_raise(self):
        import threading

        mon = LiveMonitor()
        errors = []

        def hammer(loop):
            try:
                for _ in range(400):
                    mon.note_state(loop, SL_STATE_RECORDING)
                    mon.capturing_loop()
                    mon.note_state(loop, SL_STATE_PLAYING)
            except Exception as exc:  # noqa: BLE001 — the point is that none escape
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_the_sender_dedup_is_atomic(self):
        """Two threads must not both get past the tolerance check for one value."""
        import threading

        sent = []
        sender = LiveMonitorSender(send=sent.append)
        barrier = threading.Barrier(4)

        def push():
            barrier.wait()
            sender.send(0.25)

        threads = [threading.Thread(target=push) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(sent), 1)


class SayingNothingIsWorking(unittest.TestCase):
    """A refused datagram must not read the same as a delivered one."""

    def _unreachable_sender(self, said):
        """A sender pointed at a port with nothing bound behind it.

        Uses a real socket against a real closed port rather than a fake: the
        first version of the send path used an unconnected `sendto`, which
        silently succeeds forever when nobody is listening — so a mocked error
        would have proved the reporting worked while the reporting could never
        fire on the appliance.
        """
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # now certainly closed, and unlikely to be reused at once
        sender = LiveMonitorSender(port=port, log=said.append)
        self.assertTrue(sender.open())
        return sender

    def _send_until_refused(self, sender, limit=50):
        """ICMP comes back on a later send, so push until it lands."""
        for n in range(limit):
            sender.send((n % 90) / 100.0 + 0.005)
            if sender.refused:
                return True
            time.sleep(0.01)
        return False

    def test_an_unreachable_gain_stage_is_reported(self):
        said = []
        sender = self._unreachable_sender(said)
        try:
            landed = self._send_until_refused(sender)
        finally:
            sender.close()
        self.assertTrue(landed, "sending into a closed port never reported a refusal")
        self.assertTrue(said, "a refused level update said nothing")
        self.assertIn("unreachable", said[0])

    def test_refusals_are_counted_not_repeated_every_time(self):
        said = []
        sender = self._unreachable_sender(said)
        try:
            self._send_until_refused(sender)
            for n in range(20):
                sender.send((n + 1) / 200.0)
        finally:
            sender.close()
        self.assertGreater(sender.refused_total, 1)
        self.assertEqual(len(said), 1, "one complaint, not one per datagram")

    def test_a_delivered_level_clears_the_error(self):
        sender = LiveMonitorSender(send=lambda _payload: None)
        sender.refused = 3
        sender.error = "stale"
        sender.send(0.5)
        self.assertEqual(sender.refused, 0)

    def test_recovery_is_announced_once_the_gain_stage_is_back(self):
        said = []
        sender = self._unreachable_sender(said)
        try:
            self._send_until_refused(sender)
            # Stand a listener up on the port it was complaining about.
            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            listener.bind(("127.0.0.1", sender.port))
            try:
                for n in range(10):
                    sender.send((n + 1) / 300.0)
            finally:
                listener.close()
        finally:
            sender.close()
        self.assertTrue(
            any("reachable again" in line for line in said),
            f"recovery was never announced: {said}",
        )


class TheSensorTheWatchdogRepairsFrom(unittest.TestCase):
    """`live-monitor.state` decides whether Surge gets put back on playback.

    Read carefully in both directions: a false True leaves the instrument
    silent, and a false False reconnects the direct path underneath a healthy
    insert, which is two copies of the live signal.
    """

    def _state_file(self, body: str) -> Path:
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "live-monitor.state"
        path.write_text(body)
        return path

    def _watcher(self, path):
        return audio_engine.LiveMonitorWatcher(path=path)

    def test_an_insert_carrying_audio_is_healthy(self):
        path = self._state_file("carrying=1\ndetached=1\nupdated=1000\n")
        self.assertIs(self._watcher(path).poll(now=0.0), True)

    def test_an_insert_that_has_not_detached_is_healthy(self):
        """It has not taken anything away, so the direct path is still there."""
        path = self._state_file("carrying=0\ndetached=0\nupdated=1000\n")
        self.assertIs(self._watcher(path).poll(now=0.0), True)

    def test_a_file_that_stopped_moving_while_detached_is_the_silent_instrument(self):
        """The process that owed us a restore stopped writing. Repair."""
        path = self._state_file("carrying=1\ndetached=1\nupdated=1000\n")
        watcher = self._watcher(path)
        self.assertIs(watcher.poll(now=0.0), True)
        still = audio_engine.LIVE_MONITOR_STATE_MAX_AGE_S + 1.0
        self.assertIs(watcher.poll(now=still), False)

    def test_a_file_still_being_written_is_never_stale(self):
        path = self._state_file("carrying=1\ndetached=1\nupdated=1000\n")
        watcher = self._watcher(path)
        now = 0.0
        for tick in range(1, 60):  # two minutes of a healthy insert
            now = tick * 2.0
            path.write_text(f"carrying=1\ndetached=1\nupdated={1000 + tick * 2}\n")
            self.assertIs(watcher.poll(now=now), True, f"went stale at t={now}")

    def test_a_clock_step_does_not_condemn_a_healthy_insert(self):
        """The Pi has no RTC: NTP steps the clock forward at every boot.

        The first version compared `updated=` against wall time, so a file
        written a second before a step looked an hour old — answering False,
        firing the repair, and reconnecting the direct path underneath a live
        insert. That is two copies of the live signal, which is the one fault
        this feature must never produce.
        """
        path = self._state_file("carrying=1\ndetached=1\nupdated=1000\n")
        watcher = self._watcher(path)
        self.assertIs(watcher.poll(now=0.0), True)
        # NTP jumps wall time an hour forward; the insert keeps writing.
        path.write_text("carrying=1\ndetached=1\nupdated=3601000\n")
        self.assertIs(watcher.poll(now=2.0), True)
        path.write_text("carrying=1\ndetached=1\nupdated=3601002\n")
        self.assertIs(watcher.poll(now=4.0), True)

    def test_a_file_that_stopped_moving_but_never_detached_is_not_repaired(self):
        """Nothing was taken away, so there is nothing to put back."""
        path = self._state_file("carrying=0\ndetached=0\nupdated=1000\n")
        watcher = self._watcher(path)
        watcher.poll(now=0.0)
        self.assertIsNone(watcher.poll(now=audio_engine.LIVE_MONITOR_STATE_MAX_AGE_S + 1))

    def test_no_file_says_nothing(self):
        missing = Path(self.enterContext(tempfile.TemporaryDirectory())) / "nope.state"
        self.assertIsNone(self._watcher(missing).poll(now=0.0))

    def test_the_repair_script_leaves_an_answer_the_watchdog_can_read(self):
        """The repair and its own success check must not contradict each other.

        `wait_for_live_path()` reads this file to decide whether the repair
        took. An earlier version of the script deleted it, so every genuine
        repair reported "repair did not take" — and nothing executed that path,
        so nothing noticed.
        """
        run_dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = run_dir / "live-monitor.state"
        state.write_text("carrying=1\ndetached=1\nupdated=1000\n")
        env = dict(os.environ, MPE_RUN_DIR=str(run_dir), PATH=str(run_dir) + os.pathsep + os.environ["PATH"])
        fake_connect = run_dir / "jack_connect"
        fake_connect.write_text("#!/bin/sh\nexit 0\n")
        fake_connect.chmod(0o755)
        subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/restore-direct-monitor-path.sh")],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertTrue(state.exists(), "the success check reads this file")
        watcher = audio_engine.LiveMonitorWatcher(path=state)
        self.assertIs(watcher.poll(now=0.0), True)

    def test_the_client_publishes_every_key_this_reads(self):
        """Both halves of the contract, from the two files that hold them."""
        source = (REPO_ROOT / "native/mpe-live-monitor/mpe-live-monitor.c").read_text()
        for key in ("carrying=", "detached=", "updated="):
            self.assertIn(f'"{key}', source, f"the client never writes {key}")
        self.assertIn('STATE_NAME "live-monitor.state"', source)
        self.assertEqual(audio_engine.LIVE_MONITOR_STATE_FILE.name, "live-monitor.state")


class Wiring(unittest.TestCase):
    """The knobs the appliance is configured through, pinned by name."""

    def test_the_live_level_defaults_to_unity(self):
        """An enabled monitor with nothing configured must be inaudible."""
        self.assertEqual(LiveMonitor().live_amp(), 1.0)

    def test_the_control_port_matches_the_client(self):
        """Both sides of one UDP protocol. Read the C, do not restate it."""
        source = (REPO_ROOT / "native/mpe-live-monitor/mpe-live-monitor.c").read_text()
        match = re.search(r"#define\s+CONTROL_PORT_DEFAULT\s+(\d+)", source)
        self.assertIsNotNone(match, "CONTROL_PORT_DEFAULT not found in the C client")
        self.assertEqual(int(match.group(1)), live_monitor.DEFAULT_PORT)

    def test_the_surge_client_name_matches_the_client(self):
        """Three files name Surge's JACK client; they must agree."""
        c_source = (REPO_ROOT / "native/mpe-live-monitor/mpe-live-monitor.c").read_text()
        match = re.search(r'#define\s+SURGE_CLIENT_DEFAULT\s+"([^"]+)"', c_source)
        self.assertIsNotNone(match)
        name = match.group(1)
        for path in (
            "scripts/sooperlooper/wire-jack-graph.sh",
            "scripts/restore-direct-monitor-path.sh",
        ):
            text = (REPO_ROOT / path).read_text()
            self.assertIn(
                f'MPE_SL_SURGE_CLIENT:-{name}', text,
                f"{path} disagrees with the C client about Surge's JACK client name",
            )

        # The fourth site, and the one that decides whether the watchdog thinks
        # you can hear yourself play. It kept its own env var for compatibility,
        # so what matters is that it falls back to the shared one.
        meter = (REPO_ROOT / "native/mpe-peak-meter/mpe-peak-meter.c").read_text()
        self.assertIn('getenv("MPE_SL_SURGE_CLIENT")', meter)
        self.assertIn(f'#define SURGE_CLIENT_DEFAULT "{name}"', meter)

    def test_python_and_shell_agree_on_what_switches_it_on(self):
        """The two sides read one variable and must reach one answer.

        Every value below is run through the real shell gate, not a model of
        it: the laws diverged once already, and a restated law cannot catch
        that.

        Through `--check`, which answers and exits. Running the script proper
        would reach its `exec` and start the gain stage — on a machine with
        jackd that detaches Surge from playback, and the test's own timeout
        then SIGKILLs the one process that could put it back. A test must not
        be able to silence the instrument it is testing.
        """
        script = REPO_ROOT / "scripts/start-mpe-live-monitor.sh"
        for value in ("1", "0", "on", "ON", "On", "true", "TRUE", "True",
                      "yes", "YES", "off", "OFF", "no", "false", "False", "2", ""):
            with self.subTest(value=value):
                env = dict(os.environ, MPE_LIVE_MONITOR=value)
                proc = subprocess.run(
                    ["bash", str(script), "--check"], env=env,
                    capture_output=True, text=True, timeout=30,
                )
                shell_says_on = proc.returncode == 0
                self.assertEqual(
                    live_monitor.enabled(value), shell_says_on,
                    f"MPE_LIVE_MONITOR={value!r}: python={live_monitor.enabled(value)}, "
                    f"shell_on={shell_says_on}",
                )

    def test_the_gate_check_never_reaches_the_binary(self):
        """--check must not start the gain stage. Proven by watching for it.

        Asserted by running the script against a stand-in binary that leaves a
        mark when executed, rather than by reading the source: the first version
        of this test checked that one string appeared before another in the
        file, which stays true if you delete the `exit` between them — and the
        hazard it guards against is a test suite that silences the instrument.
        """
        repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        bin_dir = repo / "native" / "mpe-live-monitor"
        bin_dir.mkdir(parents=True)
        (repo / "scripts").mkdir()
        mark = repo / "it-ran"
        stand_in = bin_dir / "mpe-live-monitor"
        stand_in.write_text(f'#!/bin/sh\ntouch "{mark}"\n')
        stand_in.chmod(0o755)

        env = dict(
            os.environ, MPE_LIVE_MONITOR="1", MPE_MODULE_REPO=str(repo),
            MPE_RUN_DIR=str(repo),
        )
        script = REPO_ROOT / "scripts/start-mpe-live-monitor.sh"
        check = subprocess.run(
            ["bash", str(script), "--check"], env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertFalse(
            mark.exists(),
            "--check started the gain stage; on a machine with jackd that "
            "detaches Surge from playback",
        )

        # And the same script, without --check, does reach it — otherwise the
        # check above would pass for the wrong reason.
        subprocess.run(
            ["bash", str(script)], env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertTrue(mark.exists(), "the script never started the gain stage at all")


if __name__ == "__main__":
    unittest.main()
