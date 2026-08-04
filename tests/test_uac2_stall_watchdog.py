"""Unit tests for scripts/uac2-stall-watchdog.sh stall detection."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_SCRIPT = REPO_ROOT / "scripts" / "uac2-stall-watchdog.sh"


class Uac2StallWatchdogTests(unittest.TestCase):
    def _run_watchdog_once(
        self,
        *,
        stream_rate: str,
        appl_ptr: str,
        stall_polls: int = 2,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            asound = tmp_path / "asound"
            status = asound / "card4" / "pcm0p" / "sub0" / "status"
            status.parent.mkdir(parents=True)
            (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
            status.write_text(f"state: RUNNING\nappl_ptr    : {appl_ptr}\n", encoding="utf-8")

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            restart_marker = tmp_path / "surge-restarted"

            amixer = bin_dir / "amixer"
            amixer.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    if [[ "$*" == *"controls"* ]]; then
                      echo "numid=4,iface=PCM,name='Playback Rate'"
                    elif [[ "$*" == *"cget"* ]]; then
                      echo "  : values={stream_rate}"
                    fi
                    """
                ),
                encoding="utf-8",
            )
            amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)

            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                f"#!/bin/bash\nif [ \"$1\" = \"restart\" ]; then touch {restart_marker}; fi\n",
                encoding="utf-8",
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

            sudo_shim = bin_dir / "sudo"
            sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
            sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

            scripts_dir = tmp_path / "scripts"
            shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
            watchdog = scripts_dir / "uac2-stall-watchdog.sh"
            shutil.copy2(WATCHDOG_SCRIPT, watchdog)

            log_file = tmp_path / "watchdog.log"
            env = os.environ.copy()
            env.update(
                {
                    "MPE_AUDIO_PROFILE": "usb-host",
                    "MPE_UAC2_ASOUND_ROOT": str(asound),
                    "MPE_UAC2_WATCHDOG_POLL": "1",
                    "MPE_UAC2_WATCHDOG_STALL_POLLS": str(stall_polls),
                    "MPE_UAC2_WATCHDOG_COOLDOWN": "1",
                    "MPE_UAC2_WATCHDOG_GRACE": "0",
                    "MPE_UAC2_WATCHDOG_LOG": str(log_file),
                    "HOME": str(tmp_path),
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                }
            )

            return subprocess.run(
                ["timeout", "8", "bash", str(watchdog)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
                cwd=tmp_path,
            )

    def test_exits_immediately_on_standalone_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
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

    def test_no_restart_when_host_not_streaming(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            asound = tmp_path / "asound"
            status = asound / "card4" / "pcm0p" / "sub0" / "status"
            status.parent.mkdir(parents=True)
            (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
            status.write_text("state: RUNNING\nappl_ptr    : 4096\n", encoding="utf-8")

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            restart_marker = tmp_path / "surge-restarted"
            amixer = bin_dir / "amixer"
            amixer.write_text(
                "#!/bin/bash\n"
                'if [[ "$*" == *"controls"* ]]; then echo "numid=4,iface=PCM,name='"'"'Playback Rate'"'"'"; fi\n'
                'if [[ "$*" == *"cget"* ]]; then echo "  : values=0"; fi\n',
                encoding="utf-8",
            )
            amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)
            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                f"#!/bin/bash\nif [ \"$1\" = \"restart\" ]; then touch {restart_marker}; fi\n",
                encoding="utf-8",
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

            sudo_shim = bin_dir / "sudo"
            sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
            sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

            scripts_dir = tmp_path / "scripts"
            shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
            watchdog = scripts_dir / "uac2-stall-watchdog.sh"
            shutil.copy2(WATCHDOG_SCRIPT, watchdog)

            env = os.environ.copy()
            env.update(
                {
                    "MPE_AUDIO_PROFILE": "usb-host",
                    "MPE_UAC2_ASOUND_ROOT": str(asound),
                    "MPE_UAC2_WATCHDOG_POLL": "1",
                    "MPE_UAC2_WATCHDOG_STALL_POLLS": "2",
                    "MPE_UAC2_WATCHDOG_COOLDOWN": "1",
                    "MPE_UAC2_WATCHDOG_GRACE": "0",
                    "MPE_UAC2_WATCHDOG_LOG": str(tmp_path / "log"),
                    "HOME": str(tmp_path),
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                }
            )
            subprocess.run(
                ["timeout", "4", "bash", str(watchdog)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertFalse(restart_marker.exists())

    def test_restarts_surge_when_host_streams_and_appl_ptr_stuck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            asound = tmp_path / "asound"
            status = asound / "card4" / "pcm0p" / "sub0" / "status"
            status.parent.mkdir(parents=True)
            (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
            status.write_text("state: RUNNING\nappl_ptr    : 4096\n", encoding="utf-8")

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            restart_marker = tmp_path / "surge-restarted"

            amixer = bin_dir / "amixer"
            amixer.write_text(
                "#!/bin/bash\n"
                'if [[ "$*" == *"controls"* ]]; then echo "numid=4,iface=PCM,name='"'"'Playback Rate'"'"'"; fi\n'
                'if [[ "$*" == *"cget"* ]]; then echo "  : values=44100"; fi\n',
                encoding="utf-8",
            )
            amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)

            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                f"#!/bin/bash\nif [ \"$1\" = \"restart\" ]; then touch {restart_marker}; fi\n",
                encoding="utf-8",
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

            sudo_shim = bin_dir / "sudo"
            sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
            sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

            scripts_dir = tmp_path / "scripts"
            shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
            watchdog = scripts_dir / "uac2-stall-watchdog.sh"
            shutil.copy2(WATCHDOG_SCRIPT, watchdog)

            log_file = tmp_path / "watchdog.log"
            env = os.environ.copy()
            env.update(
                {
                    "MPE_AUDIO_PROFILE": "usb-host",
                    "MPE_UAC2_ASOUND_ROOT": str(asound),
                    "MPE_UAC2_WATCHDOG_POLL": "1",
                    "MPE_UAC2_WATCHDOG_STALL_POLLS": "2",
                    "MPE_UAC2_WATCHDOG_COOLDOWN": "1",
                    "MPE_UAC2_WATCHDOG_GRACE": "0",
                    "MPE_UAC2_WATCHDOG_LOG": str(log_file),
                    "HOME": str(tmp_path),
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                }
            )
            subprocess.run(
                ["timeout", "8", "bash", str(watchdog)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertTrue(restart_marker.exists(), "expected Surge restart when wedged")
            self.assertIn("wedged", log_file.read_text(encoding="utf-8"))

    def test_immediate_restart_when_host_opens_on_wedged_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            asound = tmp_path / "asound"
            status = asound / "card4" / "pcm0p" / "sub0" / "status"
            status.parent.mkdir(parents=True)
            (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
            status.write_text(
                "state: RUNNING\nappl_ptr    : 4096\nhw_ptr      : 50000\n",
                encoding="utf-8",
            )

            route_file = tmp_path / "surge-route"
            route_file.write_text("uac2\n", encoding="utf-8")

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            restart_marker = tmp_path / "surge-restarted"
            recovery_marker = tmp_path / "recovery.state"

            amixer = bin_dir / "amixer"
            amixer.write_text(
                "#!/bin/bash\n"
                'if [[ "$*" == *"controls"* ]]; then echo "numid=4,iface=PCM,name='"'"'Playback Rate'"'"'"; fi\n'
                'if [[ "$*" == *"cget"* ]]; then echo "  : values=44100"; fi\n',
                encoding="utf-8",
            )
            amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)

            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                f"#!/bin/bash\nif [ \"$1\" = \"restart\" ]; then touch {restart_marker}; fi\n",
                encoding="utf-8",
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

            sudo_shim = bin_dir / "sudo"
            sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
            sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

            scripts_dir = tmp_path / "scripts"
            shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
            watchdog = scripts_dir / "uac2-stall-watchdog.sh"
            shutil.copy2(WATCHDOG_SCRIPT, watchdog)

            log_file = tmp_path / "watchdog.log"
            env = os.environ.copy()
            env.update(
                {
                    "MPE_AUDIO_PROFILE": "usb-host",
                    "MPE_UAC2_ASOUND_ROOT": str(asound),
                    "MPE_UAC2_WATCHDOG_POLL": "1",
                    "MPE_UAC2_WATCHDOG_STALL_POLLS": "4",
                    "MPE_UAC2_WATCHDOG_COOLDOWN": "0",
                    "MPE_UAC2_WATCHDOG_FAST_PROBE": "0",
                    "MPE_UAC2_WATCHDOG_POST_RESTART_GRACE": "1",
                    "MPE_SURGE_AUDIO_ROUTE_FILE": str(route_file),
                    "MPE_UAC2_RECOVERY_STATE": str(recovery_marker),
                    "MPE_UAC2_WATCHDOG_LOG": str(log_file),
                    "HOME": str(tmp_path),
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                }
            )
            subprocess.run(
                ["timeout", "4", "bash", str(watchdog)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            log_text = log_file.read_text(encoding="utf-8")
            self.assertTrue(restart_marker.exists(), "expected immediate restart on wedged stream open")
            self.assertIn("wedged", log_text)
            self.assertTrue(recovery_marker.is_file())
            self.assertIn("recovering", recovery_marker.read_text(encoding="utf-8"))


    def test_lazy_route_switches_on_host_stream_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            asound = tmp_path / "asound"
            status = asound / "card4" / "pcm0p" / "sub0" / "status"
            status.parent.mkdir(parents=True)
            (asound / "cards").write_text(" 4 [UAC2Gadget]: UAC2_Gadget\n", encoding="utf-8")
            status.write_text("state: CLOSED\n", encoding="utf-8")

            route_file = tmp_path / "surge-route"
            route_file.write_text("analog\n", encoding="utf-8")
            force_flag = tmp_path / "force-uac2"
            recovery_marker = tmp_path / "recovery.state"

            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            restart_marker = tmp_path / "surge-restarted"

            amixer = bin_dir / "amixer"
            amixer.write_text(
                "#!/bin/bash\n"
                'if [[ "$*" == *"controls"* ]]; then echo "numid=4,iface=PCM,name='"'"'Playback Rate'"'"'"; fi\n'
                'if [[ "$*" == *"cget"* ]]; then echo "  : values=44100"; fi\n',
                encoding="utf-8",
            )
            amixer.chmod(amixer.stat().st_mode | stat.S_IXUSR)

            systemctl = bin_dir / "systemctl"
            systemctl.write_text(
                f"#!/bin/bash\nif [ \"$1\" = \"restart\" ]; then touch {restart_marker}; fi\n",
                encoding="utf-8",
            )
            systemctl.chmod(systemctl.stat().st_mode | stat.S_IXUSR)

            sudo_shim = bin_dir / "sudo"
            sudo_shim.write_text("#!/bin/bash\nif [ \"$1\" = \"-n\" ]; then shift; fi\nexec \"$@\"\n", encoding="utf-8")
            sudo_shim.chmod(sudo_shim.stat().st_mode | stat.S_IXUSR)

            scripts_dir = tmp_path / "scripts"
            shutil.copytree(REPO_ROOT / "scripts" / "lib", scripts_dir / "lib")
            watchdog = scripts_dir / "uac2-stall-watchdog.sh"
            shutil.copy2(WATCHDOG_SCRIPT, watchdog)

            log_file = tmp_path / "watchdog.log"
            env = os.environ.copy()
            env.update(
                {
                    "MPE_AUDIO_PROFILE": "usb-host",
                    "MPE_UAC2_ASOUND_ROOT": str(asound),
                    "MPE_UAC2_WATCHDOG_POLL": "1",
                    "MPE_UAC2_WATCHDOG_COOLDOWN": "0",
                    "MPE_UAC2_WATCHDOG_FAST_PROBE": "0",
                    "MPE_SURGE_AUDIO_ROUTE_FILE": str(route_file),
                    "MPE_FORCE_UAC2_FLAG": str(force_flag),
                    "MPE_UAC2_RECOVERY_STATE": str(recovery_marker),
                    "MPE_UAC2_WATCHDOG_LOG": str(log_file),
                    "HOME": str(tmp_path),
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                }
            )
            subprocess.run(
                ["timeout", "4", "bash", str(watchdog)],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            log_text = log_file.read_text(encoding="utf-8")
            self.assertTrue(restart_marker.exists(), "expected lazy route Surge restart")
            self.assertTrue(force_flag.is_file(), "expected force-UAC2 flag before restart")
            self.assertIn("lazy route", log_text)


class SetupHostUsbMonitorTests(unittest.TestCase):
    def test_install_script_references_only_optional_dropin(self) -> None:
        script = (REPO_ROOT / "scripts" / "setup-host-usb-monitor.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("51-mpe-usb-no-suspend.conf", script)
        self.assertNotIn("61-mpe-usb-monitor.conf", script)
        self.assertNotIn("module-loopback", script)

    def test_mpe_services_enables_stall_watchdog_on_usb_host(self) -> None:
        content = (REPO_ROOT / "scripts" / "lib" / "mpe-services.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("uac2-stall-watchdog.service", content)
        self.assertIn("disable --now uac2-stall-watchdog.service", content)


if __name__ == "__main__":
    unittest.main()
