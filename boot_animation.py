#!/usr/bin/env python3
"""
Boot Animation for 1.3" OLED Display

Displays a loading animation on the OLED screen during system boot.
Runs as a systemd service and exits when the patch browser starts.
"""

import time
import sys
from pathlib import Path
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from PIL import ImageFont

# Try to detect display type automatically, fallback to SH1106
try:
    from luma.oled.device import sh1106 as display_device
    DISPLAY_TYPE = "SH1106"
except ImportError:
    try:
        from luma.oled.device import ssd1306 as display_device
        DISPLAY_TYPE = "SSD1306"
    except ImportError:
        print("ERROR: No OLED display driver found. Install luma.oled")
        sys.exit(1)

# I2C Configuration (must match patch_browser_ui.py)
I2C_PORT = 1
I2C_ADDRESS = 0x3C

# Animation Configuration
ANIMATION_FPS = 10  # Frames per second
FRAME_DELAY = 1.0 / ANIMATION_FPS
DEFAULT_BOOT_DURATION = 6.0  # Default total boot time estimate (seconds)


class BootAnimation:
    """Boot animation display manager"""

    def __init__(self):
        """Initialize OLED display"""
        # Wait for I2C bus to be fully ready
        time.sleep(3)

        try:
            serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
            self.device = display_device(serial, rotate=2)  # rotate=2 = 180 degrees
            print(f"Boot animation initialized on I2C {I2C_PORT}:0x{I2C_ADDRESS:02X} ({DISPLAY_TYPE})")
        except Exception as e:
            print(f"ERROR: Failed to initialize OLED display: {e}")
            sys.exit(1)

        # Load font (use default if custom not available)
        try:
            self.font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except:
            self.font = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

        self.frame = 0
        self.start_time = None  # Track boot start time
        self.boot_stages = [    # Define boot stages with timing
            (0.0, 0.1, "Initializing display..."),
            (0.1, 0.4, "Starting Surge XT..."),
            (0.4, 0.6, "Waiting for OSC..."),
            (0.6, 0.9, "Loading patch browser..."),
            (0.9, 1.0, "Ready!")
        ]

    def draw_spinner(self, draw, x, y, radius, angle):
        """Draw a rotating spinner"""
        import math

        # Draw 8 dots in a circle, with varying brightness based on position
        num_dots = 8
        for i in range(num_dots):
            dot_angle = (angle + (i * 360 / num_dots)) % 360
            rad = math.radians(dot_angle)

            # Calculate dot position
            dot_x = int(x + radius * math.cos(rad))
            dot_y = int(y + radius * math.sin(rad))

            # Draw dot (larger for the "active" dot)
            dot_size = 3 if i == 0 else 2
            draw.ellipse([dot_x - dot_size, dot_y - dot_size,
                         dot_x + dot_size, dot_y + dot_size],
                        fill="white")

    def draw_progress_bar(self, draw, x, y, width, height, progress):
        """Draw a progress bar"""
        # Draw outline
        draw.rectangle([x, y, x + width, y + height], outline="white", fill="black")

        # Draw filled portion
        fill_width = int(width * progress)
        if fill_width > 0:
            draw.rectangle([x + 1, y + 1, x + fill_width - 1, y + height - 1], fill="white")

    def draw_frame(self):
        """Draw a single animation frame with progress bar"""
        with canvas(self.device) as draw:
            # Calculate elapsed time and progress
            if self.start_time is None:
                self.start_time = time.time()

            elapsed = time.time() - self.start_time
            # Estimate total boot time as 6 seconds (adjustable)
            total_boot_time = 6.0
            progress = min(elapsed / total_boot_time, 1.0)

            # Determine current boot stage based on progress
            current_stage = "Booting..."
            for start_pct, end_pct, stage_text in self.boot_stages:
                if start_pct <= progress < end_pct:
                    current_stage = stage_text
                    break
            if progress >= 1.0:
                current_stage = "Ready!"

            # Title
            title = "Pi-Surge-MPE"
            draw.text((64, 6), title, font=self.font, anchor="mm", fill="white")

            # Progress bar (centered, below title)
            bar_width = 100
            bar_height = 8
            bar_x = (128 - bar_width) // 2
            bar_y = 20
            self.draw_progress_bar(draw, bar_x, bar_y, bar_width, bar_height, progress)

            # Spinner (smaller, moved to left of status text)
            spinner_angle = (self.frame * 45) % 360
            self.draw_spinner(draw, 16, 42, 8, spinner_angle)

            # Status text (right of spinner)
            draw.text((28, 42), current_stage, font=self.font_small, anchor="lm", fill="white")

            # Show percentage
            pct_text = f"{int(progress * 100)}%"
            draw.text((64, 56), pct_text, font=self.font_small, anchor="mm", fill="white")

    def run(self, duration=None):
        """
        Run the boot animation

        Args:
            duration: How long to run in seconds (None = run indefinitely)
        """
        self.start_time = time.time()  # Initialize start time for progress tracking
        start_time = time.time()

        print("Boot animation started")

        try:
            while True:
                self.draw_frame()
                self.frame += 1

                # Check if we should exit
                if duration is not None:
                    elapsed = time.time() - start_time
                    if elapsed >= duration:
                        print(f"Boot animation completed after {elapsed:.1f}s")
                        break

                time.sleep(FRAME_DELAY)

        except KeyboardInterrupt:
            print("\nBoot animation stopped by user")

        finally:
            # Clear display before exit
            self.device.clear()

    def show_ready_message(self):
        """Show a 'Ready' message before exiting"""
        with canvas(self.device) as draw:
            draw.text((64, 20), "Pi-Surge-MPE", font=self.font, anchor="mm", fill="white")
            draw.text((64, 44), "Ready!", font=self.font, anchor="mm", fill="white")

        time.sleep(1.5)
        self.device.clear()

    def show_error(self, error_message, timeout=7):
        """
        Show error message on display for specified timeout

        Args:
            error_message: Error text to display
            timeout: How long to show error (seconds)
        """
        with canvas(self.device) as draw:
            draw.text((64, 10), "BOOT ERROR", font=self.font, anchor="mm", fill="white")

            # Word wrap error message
            words = error_message.split()
            line = ""
            y = 28
            max_line_width = 20  # chars
            for word in words:
                test_line = f"{line} {word}".strip()
                if len(test_line) > max_line_width:
                    draw.text((64, y), line, font=self.font_small, anchor="mm", fill="white")
                    line = word
                    y += 12
                    if y > 56:  # Don't overflow display
                        break
                else:
                    line = test_line
            if line and y <= 56:
                draw.text((64, y), line, font=self.font_small, anchor="mm", fill="white")

        time.sleep(timeout)
        self.device.clear()


def main():
    """Main entry point"""
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Boot animation for Pi-Surge-MPE OLED display")
    parser.add_argument("--duration", type=float, default=None,
                       help="How long to run in seconds (default: run until killed)")
    parser.add_argument("--test", action="store_true",
                       help="Test mode: run for 10 seconds then show ready message")

    args = parser.parse_args()

    # Test mode overrides duration
    if args.test:
        duration = 10
    else:
        # Allow override via environment variable (for service configuration)
        duration = args.duration or float(os.environ.get('BOOT_ANIM_DURATION', '0')) or None

    # Create and run animation
    anim = BootAnimation()
    anim.run(duration=duration)

    # Show ready message in test mode
    if args.test:
        anim.show_ready_message()


if __name__ == "__main__":
    main()
