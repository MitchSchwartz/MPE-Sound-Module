"""library-snapshot-settings.sh: what is synced, what is device-bound, what is skipped."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "library-snapshot-settings.sh"


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "library"
        self.repo.mkdir()
        self.capture = self.root / "capture"
        home = self.capture / "home"
        for name, data in {
            ".patch_browser_fx.json": '{"user:A": {}}',
            ".patch_browser_hold.json": "{}",
            ".patch_browser_device_volume.json": '{"usb:1": -17}',
            ".patch_browser_brightness.json": "{}",
            ".patch_browser_poly_state.json": '{"runtime": 1}',
            ".patch_browser_pressure_live.json": "{}",
            ".mpe_sl_hud_state.json": "{}",
            ".local/share/Surge XT/SurgeXTUserDefaults.xml": "<x/>",
            ".mpe/looper-songs/song.wav": "audio",
        }.items():
            (home / name).parent.mkdir(parents=True, exist_ok=True)
            (home / name).write_text(data)
        (self.capture / "etc/mpe").mkdir(parents=True)
        (self.capture / "etc/mpe/mpe.env").write_text("MPE_UI_MODE=touch\n")

    def run_script(self, *args: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root / "nohome"),
            "MPE_ENV_FILE": "",
            "MPE_PERSONAL_REPO": str(self.repo),
            **(extra_env or {}),
        }
        return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)

    def test_out_copies_synced_and_device_files_and_skips_runtime(self) -> None:
        proc = self.run_script("--from", str(self.capture), "--device-id", "dev1")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        s = self.repo / "settings"
        self.assertTrue((s / "patch_browser_fx.json").is_file())
        self.assertTrue((s / "mpe_sl_hud_state.json").is_file())
        self.assertTrue((s / "SurgeXTUserDefaults.xml").is_file())
        self.assertEqual((s / "device/dev1/patch_browser_device_volume.json").read_text(), '{"usb:1": -17}')
        self.assertTrue((s / "device/dev1/patch_browser_brightness.json").is_file())
        self.assertEqual((s / "device/dev1/mpe.env").read_text(), "MPE_UI_MODE=touch\n")
        names = {p.name for p in s.rglob("*") if p.is_file()}
        self.assertNotIn("patch_browser_poly_state.json", names)
        self.assertNotIn("patch_browser_pressure_live.json", names)
        self.assertNotIn("song.wav", names)
        # regular files, never links: stores replace them with os.replace
        self.assertFalse(any(p.is_symlink() for p in s.rglob("*")))

    def test_device_files_are_skipped_without_a_device_id(self) -> None:
        proc = self.run_script("--from", str(self.capture))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.repo / "settings/device").exists())
        self.assertIn("no --device-id", proc.stdout)
        self.assertTrue((self.repo / "settings/patch_browser_fx.json").is_file())

    def test_second_run_changes_nothing(self) -> None:
        self.run_script("--from", str(self.capture), "--device-id", "dev1")
        target = self.repo / "settings/patch_browser_fx.json"
        before = target.stat().st_mtime_ns
        proc = self.run_script("--from", str(self.capture), "--device-id", "dev1")
        self.assertIn("same:", proc.stdout)
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_dry_run_writes_nothing(self) -> None:
        proc = self.run_script("--from", str(self.capture), "--device-id", "dev1", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.repo / "settings").exists())

    def test_in_copies_back_into_a_home_and_leaves_mpe_env_alone(self) -> None:
        self.run_script("--from", str(self.capture), "--device-id", "dev1")
        (self.repo / "settings/patch_browser_fx.json").write_text('{"user:B": {}}')
        dest = self.root / "newhome"
        proc = self.run_script("--in", "--to", str(dest), "--device-id", "dev1", "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual((dest / ".patch_browser_fx.json").read_text(), '{"user:B": {}}')
        self.assertEqual((dest / ".patch_browser_device_volume.json").read_text(), '{"usb:1": -17}')
        self.assertEqual((dest / ".local/share/Surge XT/SurgeXTUserDefaults.xml").read_text(), "<x/>")
        self.assertFalse((dest / "mpe.env").exists())
        self.assertFalse((dest / ".patch_browser_poly_state.json").exists())
        self.assertFalse((dest / ".patch_browser_fx.json").is_symlink())

    def fake_systemctl(self, active: str) -> dict:
        d = self.root / "fakebin"
        d.mkdir(exist_ok=True)
        f = d / "systemctl"
        f.write_text(
            '#!/usr/bin/env bash\n# systemctl is-active --quiet <unit>\n'
            f'case " {active} " in *" ${{@: -1}} "*) exit 0 ;; esac\nexit 3\n'
        )
        f.chmod(0o755)
        return {"PATH": f"{d}:{os.environ['PATH']}"}

    def test_in_refuses_while_the_browser_or_surge_runs(self) -> None:
        self.run_script("--from", str(self.capture), "--device-id", "dev1")
        dest = self.root / "livehome"
        for unit in ("touch-patch-browser.service", "patch-browser.service", "surge-xt-cli"):
            proc = self.run_script("--in", "--to", str(dest), extra_env={**self.fake_systemctl(unit), "MPE_UI_MODE": "oled" if unit == "patch-browser.service" else "touch"})
            self.assertNotEqual(proc.returncode, 0, unit)
            self.assertIn("is running", proc.stderr)
            self.assertFalse((dest / ".patch_browser_fx.json").exists(), unit)

    def test_in_proceeds_when_nothing_runs_and_with_force(self) -> None:
        self.run_script("--from", str(self.capture), "--device-id", "dev1")
        dest = self.root / "livehome"
        ok = self.run_script("--in", "--to", str(dest), extra_env=self.fake_systemctl("something-else"))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        forced = self.run_script("--in", "--to", str(dest), "--force", extra_env=self.fake_systemctl("surge-xt-cli"))
        self.assertEqual(forced.returncode, 0, forced.stderr)

    def test_in_keeps_a_copy_of_every_live_file_it_replaces(self) -> None:
        self.run_script("--from", str(self.capture), "--device-id", "dev1")
        dest = self.root / "livehome"
        dest.mkdir()
        (dest / ".patch_browser_fx.json").write_text('{"user:Live Edit": {}}')
        proc = self.run_script("--in", "--to", str(dest), "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((dest / ".patch_browser_fx.json").read_text(), '{"user:A": {}}')
        kept = list((dest / ".mpe-library-backups").glob("*/.patch_browser_fx.json"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), '{"user:Live Edit": {}}')
        # an unchanged file is not backed up
        self.assertFalse(list((dest / ".mpe-library-backups").glob("*/.patch_browser_hold.json")))

    def test_store_file_overrides_are_honoured_for_the_live_side(self) -> None:
        home = self.root / "liveh"
        home.mkdir()
        elsewhere = self.root / "elsewhere" / "fx.json"
        elsewhere.parent.mkdir()
        elsewhere.write_text('{"user:Override": {}}')
        proc = self.run_script("--from", str(home), extra_env={"MPE_FX_FILE": str(elsewhere)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((self.repo / "settings/patch_browser_fx.json").read_text(), '{"user:Override": {}}')
        # and back in: lands on the override path, not under the home
        elsewhere.write_text("{}")
        proc = self.run_script("--in", "--force", extra_env={"MPE_FX_FILE": str(elsewhere), "MPE_HOME": str(home)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(elsewhere.read_text(), '{"user:Override": {}}')
        self.assertFalse((home / ".patch_browser_fx.json").exists())

    def test_credential_in_a_setting_aborts_the_snapshot(self) -> None:
        token = "ghp_" + "A" * 30
        (self.capture / "home/.patch_browser_ui.json").write_text('{"note": "%s"}' % token)
        proc = self.run_script("--from", str(self.capture))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("credential-shaped", proc.stderr)


if __name__ == "__main__":
    unittest.main()
