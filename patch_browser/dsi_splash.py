"""Branded fullscreen splash frames for the Pi DSI touch display (kmsdrm).

Used for boot, shutdown, and calibration handoff so Linux console or stale
pygame frames never flash on the panel.
"""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path

try:
    import pygame
except ImportError:
    pygame = None  # type: ignore[assignment]

from patch_browser.ui_theme import load_theme_mode_from_prefs, theme_for_mode

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 480
BOOT_MIN_SECONDS = 1.2
BOOT_MAX_SECONDS = 3.0
SHUTDOWN_SECONDS = 3.0
BOOT_HOLD_MAX_SECONDS = 180.0
SHUTDOWN_FAILED_HINT_SECONDS = 8.0
SHUTDOWN_SPINNER_PERIOD = 1.2
BOOT_SPINNER_PERIOD = 1.2
SHUTDOWN_LOG = Path("/tmp/mpe-shutdown-splash.log")
CAL_RETURN_HOLD_SECONDS = 1.2
FAST_RESTART_DEBOUNCE_S = 30.0
LAST_SPLASH_STAMP = Path("/tmp/mpe-dsi-splash-last.ts")
BROWSER_READY_FLAG = Path("/run/mpe-touch-browser-ready")
DISPLAY_REQUEST_FLAG = Path("/run/mpe-touch-display-request")
BOOT_SPLASH_UNIT = "touch-boot-animation.service"
SHUTDOWN_SPLASH_UNIT = "mpe-shutdown-splash.service"


class SplashMode(str, Enum):
    BOOT = "boot"
    SHUTDOWN = "shutdown"
    CAL_ENTER = "cal_enter"
    CAL_RETURN = "cal_return"
    HOLD = "hold"


def configure_kmsdrm_env() -> None:
    """Set SDL kmsdrm env when running headless on the Pi DSI panel."""
    if os.environ.get("MPE_TOUCH_WINDOWED") == "1" or os.environ.get("DISPLAY"):
        return
    driver = os.environ.get("SDL_VIDEODRIVER", "").strip()
    if driver and driver != "kmsdrm":
        return
    os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
    if not os.environ.get("SDL_KMSDRM_DEVICE"):
        detect = REPO_ROOT / "scripts" / "lib" / "detect-drm-card.sh"
        if detect.is_file():
            try:
                card = subprocess.check_output(["bash", str(detect)], text=True).strip()
                if card:
                    os.environ["SDL_KMSDRM_DEVICE"] = card
            except (subprocess.CalledProcessError, OSError):
                pass
    os.environ.setdefault("SDL_KMSDRM_REQUIRE_DRM_MASTER", "1")
    os.environ.setdefault("SDL_VIDEO_EGL", "0")


def _open_fullscreen_surface(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> "pygame.Surface":
    """Open kmsdrm fullscreen with short retries while DRM comes up at boot."""
    windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
    if windowed:
        return pygame.display.set_mode((width, height))
    last_error: pygame.error | None = None
    for attempt in range(24):
        try:
            return pygame.display.set_mode((width, height), pygame.FULLSCREEN)
        except pygame.error as exc:
            last_error = exc
            if "kmsdrm" not in str(exc).lower() or attempt >= 23:
                raise
            time.sleep(0.5)
    if last_error is not None:
        raise last_error
    raise RuntimeError("failed to open kmsdrm display")


def _load_font(size: int) -> "pygame.font.Font":
    for name in ("dejavusans", "dejavusansmono", "liberationsans", "arial", None):
        try:
            if name:
                path = pygame.font.match_font(name)
                if path:
                    return pygame.font.Font(path, size)
            return pygame.font.SysFont(name, size)
        except (OSError, TypeError):
            continue
    return pygame.font.Font(None, size)


def _hide_cursor() -> None:
    if pygame is not None:
        pygame.mouse.set_visible(False)


def _log_shutdown(message: str) -> None:
    try:
        with SHUTDOWN_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def spinner_animation_phase(
    elapsed: float,
    *,
    period: float = SHUTDOWN_SPINNER_PERIOD,
) -> float:
    """Return 0..1 cycle phase for rotating-dot spinners."""
    if period <= 0:
        return 0.0
    return (elapsed % period) / period


def shutdown_animation_phase(elapsed: float, *, period: float = SHUTDOWN_SPINNER_PERIOD) -> float:
    """Return 0..1 cycle phase for the shutdown spinner."""
    return spinner_animation_phase(elapsed, period=period)


def boot_animation_phase(elapsed: float, *, period: float = BOOT_SPINNER_PERIOD) -> float:
    """Return 0..1 cycle phase for the boot splash spinner."""
    return spinner_animation_phase(elapsed, period=period)


def shutdown_subtitle(elapsed: float, *, failed: bool = False) -> str:
    if failed:
        return "Shutdown failed — check sudo/logs"
    if elapsed >= SHUTDOWN_FAILED_HINT_SECONDS:
        return "Still shutting down…"
    return "Shutting down…"


def _subtitle_for_mode(mode: SplashMode) -> str:
    if mode == SplashMode.BOOT:
        return "Starting…"
    if mode == SplashMode.SHUTDOWN:
        return "Shutting down…"
    if mode == SplashMode.CAL_ENTER:
        return "Starting calibration…"
    if mode == SplashMode.CAL_RETURN:
        return "Returning to patch browser…"
    return ""


def _recent_splash_debounce() -> bool:
    try:
        if not LAST_SPLASH_STAMP.is_file():
            return False
        last = float(LAST_SPLASH_STAMP.read_text().strip())
        return (time.time() - last) < FAST_RESTART_DEBOUNCE_S
    except (OSError, ValueError):
        return False


def recent_splash_debounce() -> bool:
    """True when a full boot animation ran within FAST_RESTART_DEBOUNCE_S."""
    return _recent_splash_debounce()


def _mark_splash_ran() -> None:
    try:
        LAST_SPLASH_STAMP.write_text(f"{time.time():.3f}\n", encoding="utf-8")
    except OSError:
        pass


def _systemctl(*args: str) -> None:
    try:
        subprocess.run(["sudo", "systemctl", *args], check=False, capture_output=True)
    except OSError:
        pass


def boot_splash_service_active() -> bool:
    """True while the early-boot splash unit holds DRM."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", BOOT_SPLASH_UNIT],
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def stop_boot_splash_service(*, wait: bool = True, timeout: float = 2.5) -> None:
    """Stop the systemd boot splash so this process can claim kmsdrm."""
    if not boot_splash_service_active():
        return
    _systemctl("stop", BOOT_SPLASH_UNIT)
    if not wait:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not boot_splash_service_active():
            return
        time.sleep(0.05)


def start_boot_splash_service() -> None:
    """Re-arm the boot splash (calibration return / async browser restart)."""
    _systemctl("start", BOOT_SPLASH_UNIT)


def stop_getty_tty1() -> None:
    """Hide login prompt on the panel framebuffer during shutdown."""
    _systemctl("stop", "getty@tty1.service")


def signal_browser_ready() -> None:
    try:
        BROWSER_READY_FLAG.write_text(f"{time.time():.3f}\n", encoding="utf-8")
    except OSError:
        pass


def clear_browser_ready_flag() -> None:
    try:
        BROWSER_READY_FLAG.unlink(missing_ok=True)
    except OSError:
        pass


def request_display_handoff() -> None:
    """Ask touch-boot-animation to release kmsdrm (cooperative exit)."""
    try:
        DISPLAY_REQUEST_FLAG.write_text(f"{time.time():.3f}\n", encoding="utf-8")
    except OSError:
        pass


def clear_display_handoff_request() -> None:
    try:
        DISPLAY_REQUEST_FLAG.unlink(missing_ok=True)
    except OSError:
        pass


def display_handoff_requested() -> bool:
    return DISPLAY_REQUEST_FLAG.is_file()


def wait_for_boot_splash_release(*, timeout: float = 5.0) -> None:
    """Wait until the boot splash unit exits after a cooperative handoff."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not boot_splash_service_active():
            return
        time.sleep(0.05)
    stop_boot_splash_service(wait=True, timeout=max(1.0, timeout / 2))


def _draw_spinner(
    screen: "pygame.Surface",
    theme,
    center_x: int,
    center_y: int,
    phase: float,
) -> None:
    """Rotating dot spinner (*phase* 0..1)."""
    dot_count = 8
    radius = 16
    active = int(phase * dot_count) % dot_count
    for index in range(dot_count):
        angle = (2 * math.pi * index / dot_count) - (math.pi / 2)
        x = int(center_x + radius * math.cos(angle))
        y = int(center_y + radius * math.sin(angle))
        trail = (active - index) % dot_count
        dot_radius = 5 if trail == 0 else max(2, 5 - trail)
        color = theme.accent if trail <= 1 else theme.muted
        pygame.draw.circle(screen, color, (x, y), dot_radius)


def draw_splash_frame(
    screen: "pygame.Surface",
    *,
    mode: SplashMode,
    theme=None,
    progress: float = 0.0,
    animation_phase: float = 0.0,
    subtitle_override: str | None = None,
    title: str = "MPE Sound Module",
) -> None:
    """Paint one branded splash frame onto *screen*."""
    if theme is None:
        theme = theme_for_mode(load_theme_mode_from_prefs())
    screen.fill(theme.bg)

    title_font = _load_font(36)
    sub_font = _load_font(22)
    hint_font = _load_font(18)

    title_surf = title_font.render(title, True, theme.text)
    screen.blit(title_surf, ((screen.get_width() - title_surf.get_width()) // 2, 150))

    subtitle = subtitle_override if subtitle_override is not None else _subtitle_for_mode(mode)
    if subtitle:
        sub_surf = sub_font.render(subtitle, True, theme.muted)
        screen.blit(sub_surf, ((screen.get_width() - sub_surf.get_width()) // 2, 210))

    if mode in (SplashMode.BOOT, SplashMode.SHUTDOWN):
        if mode == SplashMode.SHUTDOWN:
            slow = subtitle_override is not None and "Still" in subtitle_override
            hint_text = "This may take a moment" if slow else "Please wait"
            hint = hint_font.render(hint_text, True, theme.muted)
            screen.blit(hint, ((screen.get_width() - hint.get_width()) // 2, 330))
        _draw_spinner(
            screen,
            theme,
            screen.get_width() // 2,
            295,
            animation_phase,
        )

    pygame.display.flip()


def acquire_browser_display(
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> "pygame.Surface":
    """Stop the boot splash if needed and open kmsdrm with retries."""
    if pygame is None:
        raise RuntimeError("pygame is required for dsi_splash")
    windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
    if windowed or os.environ.get("DISPLAY"):
        if not pygame.get_init():
            pygame.init()
        screen = pygame.display.set_mode((width, height))
        _hide_cursor()
        return screen

    configure_kmsdrm_env()
    if not pygame.get_init():
        pygame.init()
    if boot_splash_service_active():
        request_display_handoff()
        wait_for_boot_splash_release()
    clear_display_handoff_request()
    screen = _open_fullscreen_surface(width, height)
    _hide_cursor()
    theme = theme_for_mode(load_theme_mode_from_prefs())
    draw_splash_frame(
        screen,
        mode=SplashMode.BOOT,
        theme=theme,
        animation_phase=0.0,
    )
    return screen


def paint_immediate(
    *,
    mode: SplashMode,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> tuple["pygame.Surface", tuple[int, int, int]]:
    """Open kmsdrm, paint one splash frame, return (screen, theme.bg). Caller keeps DRM."""
    if pygame is None:
        raise RuntimeError("pygame is required for dsi_splash")
    screen = acquire_browser_display(width=width, height=height)
    _hide_cursor()
    theme = theme_for_mode(load_theme_mode_from_prefs())
    draw_splash_frame(screen, mode=mode, theme=theme, animation_phase=0.0)
    return screen, theme.bg


def run_boot_animation(*, duration: float | None = None, debounce: bool = True) -> None:
    """Fullscreen boot splash until *duration* elapses or the process is killed."""
    if pygame is None:
        return
    if debounce and _recent_splash_debounce():
        paint_hold_black()
        return

    configure_kmsdrm_env()
    pygame.init()
    windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
    if windowed:
        screen = pygame.display.set_mode((DEFAULT_WIDTH, DEFAULT_HEIGHT))
    else:
        screen = _open_fullscreen_surface()
    _hide_cursor()
    theme = theme_for_mode(load_theme_mode_from_prefs())

    total = duration
    if total is None:
        env = os.environ.get("MPE_BOOT_SPLASH_SECONDS", "").strip()
        total = float(env) if env else BOOT_MAX_SECONDS
    total = max(BOOT_MIN_SECONDS, min(total, BOOT_MAX_SECONDS))

    start = time.monotonic()
    clock = pygame.time.Clock()
    _mark_splash_ran()

    try:
        while True:
            elapsed = time.monotonic() - start
            draw_splash_frame(
                screen,
                mode=SplashMode.BOOT,
                theme=theme,
                animation_phase=boot_animation_phase(elapsed),
            )
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
            if duration is not None and elapsed >= duration:
                return
            if duration is None and elapsed >= total:
                # Hold final frame until systemd stops us (touch browser start).
                clock.tick(30)
                continue
            clock.tick(30)
    finally:
        pygame.quit()


def run_shutdown_animation(
    *,
    screen: "pygame.Surface | None" = None,
    hold_until_halt: bool = False,
) -> None:
    """Animated shutdown splash; reuse *screen* when called from the live browser."""
    if pygame is None:
        return
    owns_display = screen is None
    theme = theme_for_mode(load_theme_mode_from_prefs())

    if owns_display:
        configure_kmsdrm_env()
        pygame.init()
        windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
        if windowed:
            screen = pygame.display.set_mode((DEFAULT_WIDTH, DEFAULT_HEIGHT))
        else:
            screen = _open_fullscreen_surface()

    assert screen is not None
    _hide_cursor()
    windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
    if not windowed and not os.environ.get("DISPLAY"):
        stop_getty_tty1()

    start = time.monotonic()
    clock = pygame.time.Clock()
    _log_shutdown(f"shutdown splash started hold={hold_until_halt}")
    # systemd halt/reboot path: hold until this unit is killed (TimeoutStopSec=infinity).
    # Do not exit on SIGTERM — that hands the panel back to a console or stale frame.
    if hold_until_halt:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    while True:
        elapsed = time.monotonic() - start
        if not hold_until_halt and elapsed >= SHUTDOWN_SECONDS:
            break
        draw_splash_frame(
            screen,
            mode=SplashMode.SHUTDOWN,
            theme=theme,
            animation_phase=shutdown_animation_phase(elapsed),
            subtitle_override=shutdown_subtitle(elapsed),
        )
        clock.tick(30)

    if not hold_until_halt:
        screen.fill((0, 0, 0))
        pygame.display.flip()
        if owns_display:
            pygame.quit()


def _run_systemctl(args: list[str], *, log_label: str) -> bool:
    """Run ``systemctl`` with sudo; log outcome to SHUTDOWN_LOG."""
    cmd = ["sudo", "systemctl", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            _log_shutdown(f"{log_label} failed rc={result.returncode} {err}")
            return False
        _log_shutdown(f"{log_label} ok")
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log_shutdown(f"{log_label} error: {exc}")
        return False


def start_shutdown_splash_service() -> bool:
    """Start the dedicated shutdown splash unit (Plymouth-like pattern).

    The splash runs in its own systemd unit ordered ``Before=systemd-poweroff.service``,
    not inside the patch browser process.
    """
    return _run_systemctl(
        ["start", SHUTDOWN_SPLASH_UNIT],
        log_label=f"systemctl start {SHUTDOWN_SPLASH_UNIT}",
    )


def request_system_power_action(power_action: str) -> bool:
    """Ask systemd to halt or reboot (kiosk pattern).

    Uses ``systemctl poweroff`` / ``systemctl reboot`` so systemd owns the
    shutdown transaction (see systemd.special(7)). Detached ``poweroff(8)`` from
    Python can fail silently and leave the UI running.
    """
    verb = "poweroff" if power_action == "shutdown" else "reboot"
    return _run_systemctl([verb], log_label=f"systemctl {verb}")


def trigger_user_shutdown(power_action: str) -> bool:
    """UI-confirmed shutdown: splash unit first, then systemd poweroff/reboot.

    The browser should exit immediately after this returns — splash is held by
    ``mpe-shutdown-splash.service``, not an in-process pygame loop.
    """
    _log_shutdown(f"trigger_user_shutdown action={power_action}")
    stop_getty_tty1()
    splash_ok = start_shutdown_splash_service()
    power_ok = request_system_power_action(power_action)
    if not splash_ok:
        _log_shutdown("shutdown splash unit failed to start — poweroff may show console")
    return power_ok


def hold_shutdown_frame(*, screen: "pygame.Surface | None" = None) -> None:
    """Paint shutdown splash once and loop until systemd kills the process."""
    run_shutdown_animation(screen=screen, hold_until_halt=True)


def paint_hold_black() -> None:
    """Fill panel true black (fast path after debounced restart)."""
    if pygame is None:
        return
    configure_kmsdrm_env()
    pygame.init()
    try:
        windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
        if windowed:
            screen = pygame.display.set_mode((DEFAULT_WIDTH, DEFAULT_HEIGHT))
        else:
            screen = _open_fullscreen_surface()
        _hide_cursor()
        screen.fill((0, 0, 0))
        pygame.display.flip()
    finally:
        pygame.quit()


def run_hold_loop() -> None:
    """Hold branded boot frame until killed or display handoff is requested."""
    if pygame is None:
        sys.exit(0)
    configure_kmsdrm_env()
    pygame.init()
    windowed = os.environ.get("MPE_TOUCH_WINDOWED") == "1"
    if windowed:
        screen = pygame.display.set_mode((DEFAULT_WIDTH, DEFAULT_HEIGHT))
    else:
        screen = _open_fullscreen_surface()
    _hide_cursor()
    theme = theme_for_mode(load_theme_mode_from_prefs())
    _mark_splash_ran()
    clock = pygame.time.Clock()
    hold_start = time.monotonic()
    exiting = False

    def _request_exit(*_args: object) -> None:
        nonlocal exiting
        exiting = True

    signal.signal(signal.SIGTERM, _request_exit)
    signal.signal(signal.SIGINT, _request_exit)

    try:
        while not exiting and not display_handoff_requested():
            elapsed = time.monotonic() - hold_start
            if elapsed >= BOOT_HOLD_MAX_SECONDS:
                _log_shutdown(
                    f"boot hold exceeded {BOOT_HOLD_MAX_SECONDS:.0f}s, exiting splash",
                )
                break
            draw_splash_frame(
                screen,
                mode=SplashMode.BOOT,
                theme=theme,
                animation_phase=boot_animation_phase(elapsed),
            )
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
            clock.tick(30)
    finally:
        pygame.quit()
        clear_display_handoff_request()
