"""Hermetic helpers — subprocess tests must not read /etc/mpe/mpe.env on the Pi."""

from __future__ import annotations

from pathlib import Path


def write_hermetic_mpe_env(tmp_dir: Path, profile: str, **extra: str) -> str:
    """Write MPE_AUDIO_PROFILE (and extras) to a temp env file; return its path."""
    path = tmp_dir / "mpe.env"
    lines = [f"MPE_AUDIO_PROFILE={profile}"]
    for key, value in extra.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def hermetic_env_skip_system() -> dict[str, str]:
    """Set MPE_ENV_FILE so paths.sh skips /etc/mpe/mpe.env (process env only)."""
    return {"MPE_ENV_FILE": ""}


def hermetic_env_with_profile(tmp_dir: Path, profile: str, **extra: str) -> dict[str, str]:
    """Return env updates pointing subprocesses at a temp appliance env file."""
    return {"MPE_ENV_FILE": write_hermetic_mpe_env(tmp_dir, profile, **extra)}


def isolated_path_prefix(tmp_dir: Path) -> str:
    """Empty directory to prepend to PATH so jack_lsp and similar tools are absent."""
    empty = tmp_dir / "isolated_bin"
    empty.mkdir(exist_ok=True)
    return str(empty)


def isolated_path_only(tmp_dir: Path) -> str:
    """PATH containing no system binaries — use for missing-tool probe tests."""
    return isolated_path_prefix(tmp_dir)
