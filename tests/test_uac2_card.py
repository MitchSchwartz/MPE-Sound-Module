"""Unit tests for scripts/lib/uac2-card.sh helpers."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UAC2_LIB = REPO_ROOT / "scripts" / "lib" / "uac2-card.sh"


def _bash_uac2(script_body: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", f"source {UAC2_LIB!s}; {script_body}"],
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
    )


class Uac2CardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.asound = Path(self._tmp.name) / "asound"
        self.asound.mkdir()
        self.env = {"MPE_UAC2_ASOUND_ROOT": str(self.asound)}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_card_index_uac2gadget(self) -> None:
        (self.asound / "cards").write_text(
            " 0 [Headphones]: ...\n 4 [UAC2Gadget]: UAC2_Gadget\n",
            encoding="utf-8",
        )
        result = _bash_uac2("uac2_card_index", self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "4")

    def test_card_index_passthrough_name(self) -> None:
        (self.asound / "cards").write_text(
            " 6 [Passthrough]: USB Audio Passthrough\n",
            encoding="utf-8",
        )
        result = _bash_uac2("uac2_card_index", self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "6")

    def test_card_index_missing_returns_nonzero(self) -> None:
        (self.asound / "cards").write_text(" 0 [Headphones]: ...\n", encoding="utf-8")
        result = _bash_uac2("uac2_card_index", self.env)
        self.assertNotEqual(result.returncode, 0)

    def test_pcm_status_path(self) -> None:
        result = _bash_uac2('uac2_pcm_status_path "4"', self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            str(self.asound / "card4" / "pcm0p" / "sub0" / "status"),
        )

    def test_appl_ptr_parses_status_file(self) -> None:
        status = self.asound / "card4" / "pcm0p" / "sub0" / "status"
        status.parent.mkdir(parents=True)
        status.write_text(
            "state: RUNNING\nappl_ptr    : 1068890\nhw_ptr      : 1440217\n",
            encoding="utf-8",
        )
        result = _bash_uac2(f'uac2_appl_ptr "{status}"', self.env)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), "1068890")

    def test_appl_ptr_missing_file_fails(self) -> None:
        result = _bash_uac2(
            f'uac2_appl_ptr "{self.asound}/card9/pcm0p/sub0/status"',
            self.env,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
