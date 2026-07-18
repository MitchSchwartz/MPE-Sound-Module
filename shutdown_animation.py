#!/usr/bin/env python3
"""
Shutdown Animation for 1.3" OLED Display

Displays a shutdown message on the OLED screen during system shutdown.
Runs as a systemd service during shutdown/reboot.
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


class ShutdownAnimation:
    """Shutdown animation display manager"""

    def __init__(self):
        """Initialize OLED display"""
        try:
            serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
            self.device = display_device(serial, rotate=2)  # rotate=2 = 180 degrees
            print(f"Shutdown animation initialized on I2C {I2C_PORT}:0x{I2C_ADDRESS:02X} ({DISPLAY_TYPE})")
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

    def show_shutdown_message(self):
        """Display shutdown message"""
        with canvas(self.device) as draw:
            # Title
            draw.text((64, 20), "Pi-Surge-MPE", font=self.font, anchor="mm", fill="white")
            # Message
            draw.text((64, 44), "Shutting down...", font=self.font_small, anchor="mm", fill="white")

        print("Shutdown message displayed")

    def show_goodbye_message(self):
        """Display goodbye message"""
        with canvas(self.device) as draw:
            # Title
            draw.text((64, 20), "Pi-Surge-MPE", font=self.font, anchor="mm", fill="white")
            # Message
            draw.text((64, 44), "Goodbye!", font=self.font_small, anchor="mm", fill="white")

        print("Goodbye message displayed")

    def clear_display(self):
        """Clear the display"""
        self.device.clear()
        print("Display cleared")


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Shutdown animation for Pi-Surge-MPE OLED display")
    parser.add_argument("--clear-only", action="store_true",
                       help="Only clear the display (for very quick shutdown)")

    args = parser.parse_args()

    # Create animation
    anim = ShutdownAnimation()

    if args.clear_only:
        # Just clear the display
        anim.clear_display()
    else:
        # Show shutdown message
        anim.show_shutdown_message()
        time.sleep(2)

        # Show goodbye message
        anim.show_goodbye_message()
        time.sleep(1.5)

        # Clear display before poweroff
        anim.clear_display()

    print("Shutdown animation complete")


if __name__ == "__main__":
    main()
