"""Backlight control for DSI / HDMI touch displays on Raspberry Pi."""

from __future__ import annotations

import json
from pathlib import Path


class BacklightController:
    """Read/write Linux sysfs backlight with optional persistence."""

    STATE_FILE = Path.home() / ".patch_browser_brightness.json"

    def __init__(self, state_file: Path | None = None):
        self.state_file = state_file or self.STATE_FILE
        self.device_path = self._find_backlight_device()
        self.available = self.device_path is not None

    def _find_backlight_device(self) -> Path | None:
        backlight_root = Path("/sys/class/backlight")
        if not backlight_root.is_dir():
            return None

        candidates = sorted(backlight_root.iterdir())
        for candidate in candidates:
            brightness_file = candidate / "brightness"
            max_file = candidate / "max_brightness"
            if brightness_file.is_file() and max_file.is_file():
                return candidate
        return None

    @property
    def device_name(self) -> str | None:
        return self.device_path.name if self.device_path else None

    def max_brightness(self) -> int:
        if not self.device_path:
            return 255
        try:
            return int((self.device_path / "max_brightness").read_text().strip())
        except OSError:
            return 255

    def get_brightness(self) -> int | None:
        if not self.device_path:
            return None
        try:
            return int((self.device_path / "brightness").read_text().strip())
        except OSError:
            return None

    def set_brightness(self, value: int, persist: bool = True) -> bool:
        if not self.device_path:
            return False

        clamped = max(0, min(int(value), self.max_brightness()))
        try:
            (self.device_path / "brightness").write_text(str(clamped))
        except OSError as exc:
            print(f"Warning: could not set backlight ({exc})")
            return False

        if persist:
            self.save_state(clamped)
        return True

    def get_percent(self) -> int:
        current = self.get_brightness()
        if current is None:
            return 100
        maximum = self.max_brightness()
        if maximum <= 0:
            return 100
        return round((current / maximum) * 100)

    def set_percent(self, percent: int, persist: bool = True) -> bool:
        maximum = self.max_brightness()
        value = round((max(0, min(percent, 100)) / 100) * maximum)
        return self.set_brightness(value, persist=persist)

    def save_state(self, value: int) -> None:
        try:
            payload = {
                "brightness": value,
                "percent": round((value / self.max_brightness()) * 100),
                "device": self.device_name,
            }
            self.state_file.write_text(json.dumps(payload, indent=2))
        except OSError as exc:
            print(f"Warning: could not persist brightness ({exc})")

    def load_saved_percent(self) -> int | None:
        if not self.state_file.exists():
            return None
        try:
            payload = json.loads(self.state_file.read_text())
            percent = payload.get("percent")
            if isinstance(percent, (int, float)):
                return int(percent)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        return None

    def restore_saved(self) -> bool:
        saved = self.load_saved_percent()
        if saved is None:
            return False
        return self.set_percent(saved, persist=False)
