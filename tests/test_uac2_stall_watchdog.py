"""Unit tests for scripts/uac2-stall-watchdog.sh host-route state machine."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.hermetic_env import hermetic_env_skip_system, hermetic_env_with_profile

REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_SCRIPT = REPO_ROOT / "scripts" / "uac2-stall-watchdog.sh"


class Uac2HostRouteWatchdogTests(unittest.TestCase):
    def _run_watchdog(
        self,
        *,
        stream_rates: list[str],
        poll_seconds: str = "1",
        cooldown: str = "0",
    ) -> tuple[Path, Path, Path]:
        tmp_path = Path(tempfile.mkdtemp())
        asound = tmp_path / "asound"
        status = asound / "card4" / "pcm0p" / "sub0" / "status"
        status.parent.mkdir(parents=True)
        (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
        status.write_text("state: CLOSED\n", encoding="utf-8")

        streaming_flag = tmp_path / "host-streaming"
        restart_marker = tmp_path / "surge-restarted"
        recovery_marker = tmp_path / "recovery.state"
        log_file = tmp_path / "watchdog.log"

        rate_file = tmp_path / "rate"
        rate_file.write_text(stream_rates[0], encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        amixer = bin_dir / "amixer"
        amixer.write_text(
            "#!/bin/bash\n"
            'if [[ "$*" == *"controls"* ]]; then echo "numid=4,iface=PCM,name='"'"'Playback Rate'"'"'"; fi\n'
            f'if [[ "$*" == *"cget"* ]]; then echo "  : values=$(cat {rate_file})"; fi\n',
            encoding="utf-8",
        )
        amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)

        systemctl = bin_dir / "systemctl"
        systemctl.write_text(
            f"#!/bin/bash\n"
            f'if [ "$1" = "restart" ]; then touch {restart_marker}; fi\n'
            f'if [ "$1" = "start" ]; then touch {tmp_path / "bridge-started"}; fi\n'
            f'if [ "$1" = "stop" ]; then rm -f {tmp_path / "bridge-started"}; fi\n',
            encoding="utf-8",
        )
        systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

        sudo_shim = bin_dir / "sudo"
        sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
        sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

        scripts_dir = tmp_path / "scripts"
        shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
        shutil.copy2(WATCHDOG_SCRIPT, scripts_dir / "uac2-stall-watchdog.sh")

        env = os.environ.copy()
        env.update(
            {
                "MPE_UAC2_ASOUND_ROOT": str(asound),
                "MPE_UAC2_WATCHDOG_POLL": poll_seconds,
                "MPE_UAC2_WATCHDOG_COOLDOWN": cooldown,
                "MPE_UAC2_HOST_STREAMING_FLAG": str(streaming_flag),
                "MPE_UAC2_RECOVERY_STATE": str(recovery_marker),
                "MPE_UAC2_WATCHDOG_LOG": str(log_file),
                "HOME": str(tmp_path),
                "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            }
        )
        env.update(hermetic_env_with_profile(tmp_path, "usb-host"))

        proc = subprocess.Popen(
            ["timeout", "10", "bash", str(scripts_dir / "uac2-stall-watchdog.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=tmp_path,
        )

        import time

        for rate in stream_rates[1:]:
            time.sleep(float(poll_seconds) + 0.1)
            rate_file.write_text(rate, encoding="utf-8")

        proc.wait(timeout=12)
        bridge_marker = tmp_path / "bridge-started"
        return restart_marker, streaming_flag, log_file, bridge_marker

    def _run_session_watchdog(self, stream_rates: list[str]) -> tuple[Path, Path, Path, Path]:
        tmp_path = Path(tempfile.mkdtemp())
        asound = tmp_path / "asound"
        status = asound / "card4" / "pcm0p" / "sub0" / "status"
        status.parent.mkdir(parents=True)
        (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
        status.write_text("state: CLOSED\n", encoding="utf-8")

        streaming_flag = tmp_path / "host-streaming"
        restart_marker = tmp_path / "surge-restarted"
        recovery_marker = tmp_path / "recovery.state"
        log_file = tmp_path / "watchdog.log"
        bridge_marker = tmp_path / "bridge-started"

        rate_file = tmp_path / "rate"
        rate_file.write_text(stream_rates[0], encoding="utf-8")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        amixer = bin_dir / "amixer"
        amixer.write_text(
            "#!/bin/bash\n"
            'if [[ "$*" == *"controls"* ]]; then echo "numid=4,iface=PCM,name='"'"'Playback Rate'"'"'"; fi\n'
            f'if [[ "$*" == *"cget"* ]]; then echo "  : values=$(cat {rate_file})"; fi\n',
            encoding="utf-8",
        )
        amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)

        systemctl = bin_dir / "systemctl"
        systemctl.write_text(
            f"#!/bin/bash\n"
            f'if [ "$1" = "restart" ]; then touch {restart_marker}; fi\n'
            f'if [ "$1" = "start" ]; then touch {bridge_marker}; fi\n'
            f'if [ "$1" = "stop" ]; then rm -f {bridge_marker}; fi\n',
            encoding="utf-8",
        )
        systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

        sudo_shim = bin_dir / "sudo"
        sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
        sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

        scripts_dir = tmp_path / "scripts"
        shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
        shutil.copy2(WATCHDOG_SCRIPT, scripts_dir / "uac2-stall-watchdog.sh")

        env = os.environ.copy()
        env.update(
            {
                "MPE_UAC2_ASOUND_ROOT": str(asound),
                "MPE_UAC2_WATCHDOG_POLL": "1",
                "MPE_UAC2_WATCHDOG_COOLDOWN": "0",
                "MPE_UAC2_HOST_STREAMING_FLAG": str(streaming_flag),
                "MPE_UAC2_RECOVERY_STATE": str(recovery_marker),
                "MPE_UAC2_WATCHDOG_LOG": str(log_file),
                "HOME": str(tmp_path),
                "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            }
        )
        env.update(hermetic_env_with_profile(tmp_path, "usb-host-session"))

        proc = subprocess.Popen(
            ["timeout", "10", "bash", str(scripts_dir / "uac2-stall-watchdog.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=tmp_path,
        )

        import time

        for rate in stream_rates[1:]:
            time.sleep(1.1)
            rate_file.write_text(rate, encoding="utf-8")

        proc.wait(timeout=12)
        return restart_marker, streaming_flag, log_file, bridge_marker

    def tearDown(self) -> None:
        pass

    def test_exits_immediately_on_standalone_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env.update(hermetic_env_skip_system())
            env["MPE_AUDIO_PROFILE"] = "standalone"
            env["MPE_UAC2_WATCHDOG_LOG"] = str(Path(tmp) / "log")
            result = subprocess.run(
                ["bash", str(WATCHDOG_SCRIPT)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("not needed", result.stdout)

    def test_host_capture_open_restarts_surge_on_uac2(self) -> None:
        restart_marker, streaming_flag, log_file, _bridge = self._run_watchdog(
            stream_rates=["0", "48000"],
        )
        self.assertTrue(restart_marker.exists())
        self.assertTrue(streaming_flag.is_file())
        log = log_file.read_text(encoding="utf-8")
        self.assertIn("Surge → UAC2", log)

    def test_host_capture_close_restarts_surge_to_idle(self) -> None:
        # Hold streaming rate through init so the watchdog sees active capture before close.
        restart_marker, streaming_flag, log_file, _bridge = self._run_watchdog(
            stream_rates=["48000", "48000", "0"],
        )
        self.assertTrue(restart_marker.exists())
        self.assertFalse(streaming_flag.exists())
        log = log_file.read_text(encoding="utf-8")
        self.assertIn("Surge → idle", log)

    def test_session_mode_starts_bridge_not_surge(self) -> None:
        restart_marker, streaming_flag, log_file, bridge_marker = self._run_session_watchdog(
            stream_rates=["0", "48000"],
        )
        self.assertFalse(restart_marker.exists())
        self.assertTrue(bridge_marker.exists())
        self.assertTrue(streaming_flag.is_file())
        self.assertIn("mic → UAC2", log_file.read_text(encoding="utf-8"))

    def test_session_mode_stops_bridge_on_close(self) -> None:
        restart_marker, streaming_flag, log_file, bridge_marker = self._run_session_watchdog(
            stream_rates=["48000", "0"],
        )
        self.assertFalse(restart_marker.exists())
        self.assertFalse(bridge_marker.exists())
        self.assertFalse(streaming_flag.exists())
        self.assertIn("mic bridge stopped", log_file.read_text(encoding="utf-8"))


class SetupHostUsbMonitorTests(unittest.TestCase):
    def test_mpe_services_enables_stall_watchdog_on_usb_host(self) -> None:
        content = (REPO_ROOT / "scripts" / "lib" / "mpe-services.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("uac2-stall-watchdog.service", content)


if __name__ == "__main__":
    unittest.main()
