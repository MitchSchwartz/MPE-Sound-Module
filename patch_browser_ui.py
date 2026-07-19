#!/usr/bin/env python3
"""
Pi-Surge-MPE Patch Browser UI

Single-encoder patch browser with 1.3" OLED display for browsing and loading
Surge XT patches in headless/CLI mode.

Features:
- 128x64 OLED display (I2C) showing category and patch names
- Single rotary encoder with click button
- Click to toggle between category/patch scroll modes
- Auto-loads selected patch immediately (low CPU overhead)
- Scans all Surge factory and 3rd-party patches
"""

import time
import signal
import sys
import os
import threading
from pathlib import Path
from dataclasses import dataclass, field
from gpiozero import RotaryEncoder, Button
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from PIL import ImageFont
import subprocess

# Kernel-level encoder support (optional, falls back to gpiozero)
try:
    import evdev
    from evdev import InputDevice, ecodes
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

# Try to detect display type automatically, fallback to SH1106
try:
    from luma.oled.device import sh1106 as display_device
    DISPLAY_TYPE = "SH1106"
except ImportError:
    from luma.oled.device import ssd1306 as display_device
    DISPLAY_TYPE = "SSD1306"

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class Config:
    """Centralized configuration for patch browser"""
    # GPIO Pins
    encoder_clk: int = 17  # GPIO 17 (Pin 11)
    encoder_dt: int = 27   # GPIO 27 (Pin 13)
    encoder_sw: int = 22   # GPIO 22 (Pin 15)

    # I2C Configuration
    i2c_port: int = 1
    i2c_address: int = 0x3C

    # Timing Constants
    debounce_time: float = 0.005  # Encoder debounce (5ms)
    button_debounce: float = 0.01  # Button debounce (10ms)
    scroll_processing_interval: float = 0.001  # Scroll event processing (1ms)
    load_debounce_time: float = 1.25  # Wait before loading patch (1.25s)
    button_encoder_isolation: float = 0.05  # Isolation window (50ms)
    encoder_post_button_cooldown: float = 0.05  # Cooldown after button (50ms)

    # Press Duration Thresholds
    bold_press_min: float = 0.5  # Mode toggle (500ms)
    long_press_min: float = 2.0  # Copy to favorites (2s)
    poweroff_press_min: float = 8.0  # Power menu (8s)

    # Scroll Modes
    scroll_mode_category: int = 0
    scroll_mode_patch: int = 1

    # User quick-access folder (on-disk name under ~/Documents/Surge XT/Patches/)
    # Override via MPE_FAVORITES_NAME in /etc/mpe/mpe.env — see docs/PATCH_BROWSER_UI.md
    favorites_name: str = field(
        default_factory=lambda: os.environ.get("MPE_FAVORITES_NAME", "!Quick Access")
    )

# Global config instance
CONFIG = Config()

# Alias used throughout scanner/UI (set MPE_FAVORITES_NAME before patch-browser starts)
FAVORITES_NAME = CONFIG.favorites_name


def favorites_display_name(name=None):
    """Browser category label — leading ! sorts first. Idempotent if name already has !."""
    n = name if name is not None else FAVORITES_NAME
    return n if n.startswith("!") else f"!{n}"


def favorites_folder_matches(name):
    """True if on-disk folder/category name matches MPE_FAVORITES_NAME (with or without !)."""
    return name.lstrip("!").lower() == FAVORITES_NAME.lstrip("!").lower()

# Backwards compatibility - keep old constant names pointing to config
ENCODER_CLK = CONFIG.encoder_clk
ENCODER_DT = CONFIG.encoder_dt
ENCODER_SW = CONFIG.encoder_sw
I2C_PORT = CONFIG.i2c_port
I2C_ADDRESS = CONFIG.i2c_address
DEBOUNCE_TIME = CONFIG.debounce_time
BUTTON_DEBOUNCE = CONFIG.button_debounce
SCROLL_PROCESSING_INTERVAL = CONFIG.scroll_processing_interval
LOAD_DEBOUNCE_TIME = CONFIG.load_debounce_time
BUTTON_ENCODER_ISOLATION = CONFIG.button_encoder_isolation
ENCODER_POST_BUTTON_COOLDOWN = CONFIG.encoder_post_button_cooldown
BOLD_PRESS_MIN = CONFIG.bold_press_min
LONG_PRESS_MIN = CONFIG.long_press_min
POWEROFF_PRESS_MIN = CONFIG.poweroff_press_min
SCROLL_MODE_CATEGORY = CONFIG.scroll_mode_category
SCROLL_MODE_PATCH = CONFIG.scroll_mode_patch

# Surge Patch Directories
SURGE_PATCH_DIRS = [
    Path.home() / "surge" / "resources" / "data" / "patches_factory",
    Path.home() / "surge" / "resources" / "data" / "patches_3rdparty",
    Path.home() / "Documents" / "Surge XT" / "Patches",  # User patches (includes favorites category)
]

# State persistence files
LAST_PATCH_FILE = Path.home() / ".patch_browser_last_patch.json"

# ============================================================================
# PATCH SCANNER
# ============================================================================

class PatchScanner:
    """Scans and organizes Surge patches by category"""

    def __init__(self, patch_dirs, last_patch_file=LAST_PATCH_FILE):
        self.patch_dirs = patch_dirs
        self.last_patch_file = last_patch_file
        self.patches = {}  # {category: [patch_paths]}

        # Threading support for background scanning
        self.scan_complete = threading.Event()
        self.scan_lock = threading.Lock()
        self.scan_thread = None

        # Do NOT call scan_patches() here anymore - will be called explicitly

    def scan_patches(self):
        """Scan all patch directories and organize by category"""
        print("Scanning Surge patches...")
        total_patches = 0

        # Clear patches dict for full scan (removes any quick-scan temporary data)
        with self.scan_lock:
            self.patches = {}

        for patch_dir in self.patch_dirs:
            if not patch_dir.exists():
                print(f"Warning: Patch directory not found: {patch_dir}")
                continue

            # Walk through directory structure
            for root, dirs, files in os.walk(patch_dir):
                # Category is the immediate subdirectory name
                rel_path = Path(root).relative_to(patch_dir)
                category = str(rel_path.parts[0]) if rel_path.parts else "Uncategorized"

                # Force the favorites category to the top by prepending "!"
                if favorites_folder_matches(category):
                    category = favorites_display_name(category)

                # Find all .fxp files
                fxp_files = [f for f in files if f.lower().endswith('.fxp')]

                if fxp_files:
                    if category not in self.patches:
                        self.patches[category] = []

                    for fxp_file in fxp_files:
                        patch_path = Path(root) / fxp_file
                        patch_name = fxp_file.replace('.fxp', '').replace('.FXP', '')

                        # Check for duplicates by name (prevent same patch appearing twice in same category)
                        # This handles cases where a patch exists in both original location and the favorites folder
                        existing_patch = next((p for p in self.patches[category] if p['name'] == patch_name), None)
                        if existing_patch:
                            # Patch with same name already exists - prefer the one in the favorites folder
                            if category == favorites_display_name() and favorites_folder_matches(str(patch_path)):
                                # Replace with the one from the favorites folder
                                existing_patch['path'] = str(patch_path)
                            # Otherwise keep the existing one (skip duplicate)
                            continue

                        self.patches[category].append({
                            'name': patch_name,
                            'path': str(patch_path),
                            'category': category
                        })
                        total_patches += 1

        # Sort categories and patches alphabetically, but put favorites first
        def sort_key(item):
            category_name = item[0]
            if category_name == favorites_display_name():
                return ('', category_name)  # Empty string sorts before all letters
            return (category_name, category_name)

        # Update patches dict atomically to prevent race conditions with UI thread
        with self.scan_lock:
            self.patches = {k: sorted(v, key=lambda x: x['name'])
                           for k, v in sorted(self.patches.items(), key=sort_key)}

        print(f"Found {total_patches} patches in {len(self.patches)} categories")
        # Debug: Show first 3 categories
        cat_names = list(self.patches.keys())
        print(f"First 3 categories: {cat_names[:3]}")
        return self.patches

    def scan_patches_background(self):
        """
        Start background thread to scan patches.
        Returns immediately, sets scan_complete event when done.
        """
        def _scan_worker():
            try:
                self.scan_patches()
                self.scan_complete.set()
                print("Background patch scan complete")
            except Exception as e:
                print(f"Error during background scan: {e}")
                # Still signal completion and add error category
                with self.scan_lock:
                    if not self.patches:
                        self.patches = {"Error": [{"name": "Scan failed", "path": "", "category": "Error"}]}
                self.scan_complete.set()

        self.scan_thread = threading.Thread(target=_scan_worker, daemon=True, name="PatchScanner")
        self.scan_thread.start()
        print("Started background patch scanning...")

    def wait_for_scan(self, timeout=None):
        """Wait for background scan to complete"""
        return self.scan_complete.wait(timeout=timeout)

    def quick_scan_category(self, category_path):
        """
        Quickly scan a single category directory without full scan.
        Used to load last patch immediately before full background scan.

        Args:
            category_path: Path object pointing to category directory

        Returns:
            List of patch dicts in the category
        """
        patches = []

        if not category_path.exists():
            return patches

        for fxp_file in category_path.glob("*.fxp"):
            patch_name = fxp_file.stem
            category_name = category_path.name

            # Force the favorites category to the top
            if favorites_folder_matches(category_name):
                category_name = favorites_display_name(category_name)

            patches.append({
                'name': patch_name,
                'path': str(fxp_file),
                'category': category_name
            })

        return sorted(patches, key=lambda x: x['name'])

    def save_last_patch(self, category, patch_path):
        """Save the last loaded patch for restoration on reconnect"""
        import json
        try:
            state = {
                'category': category,
                'patch_path': str(patch_path)
            }
            with open(self.last_patch_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save last patch state: {e}")

    def load_last_patch(self):
        """Load the last patch state (returns dict with category and patch_path, or None)"""
        import json
        if self.last_patch_file.exists():
            try:
                with open(self.last_patch_file, 'r') as f:
                    state = json.load(f)
                    # Validate the patch still exists
                    patch_path = Path(state['patch_path'])
                    if patch_path.exists():
                        return state
                    else:
                        print(f"Last patch no longer exists: {patch_path}")
            except Exception as e:
                print(f"Warning: Could not load last patch state: {e}")
        return None

    def get_categories(self):
        """Get sorted list of category names (favorites will be first due to ! prefix)"""
        with self.scan_lock:
            return list(self.patches.keys())

    def get_patches_in_category(self, category):
        """Get all patches in a specific category"""
        with self.scan_lock:
            return self.patches.get(category, [])

    
    def is_in_favorites_folder(self, patch_path):
        """Check if a patch is already in the favorites folder"""
        patch_path_obj = Path(patch_path)
        for patch_dir in self.patch_dirs:
            favorites_folder = patch_dir / FAVORITES_NAME
            if favorites_folder.exists() and patch_path_obj.is_relative_to(favorites_folder):
                return True
        return False
    
    def get_favorites_folder_path(self):
        """Get the path to the favorites folder (creates if needed)"""
        # Use the user patches directory for the favorites folder
        user_patches_dir = Path.home() / "Documents" / "Surge XT" / "Patches"
        favorites_folder = user_patches_dir / FAVORITES_NAME
        favorites_folder.mkdir(parents=True, exist_ok=True)
        return favorites_folder
    
    def copy_patch_to_favorites(self, patch_path):
        """Copy a patch file to the favorites folder"""
        try:
            source_path = Path(patch_path)
            if not source_path.exists():
                print(f"Error: Source patch not found: {patch_path}")
                return False
            
            favorites_folder = self.get_favorites_folder_path()
            dest_path = favorites_folder / source_path.name
            
            # Check if file already exists in the favorites folder
            if dest_path.exists():
                print(f"Patch already exists in favorites folder: {source_path.name}")
                return False  # Don't create duplicate
            
            # Copy the file
            import shutil
            shutil.copy2(source_path, dest_path)
            print(f"Copied patch to favorites folder: {source_path.name}")
            
            # Rescan to pick up the new patch
            self.scan_patches()
            return True
        except Exception as e:
            print(f"Error copying patch to favorites folder: {e}")
            return False

# ============================================================================
# OLED DISPLAY MANAGER
# ============================================================================

class PatchDisplay:
    """Manages the 128x64 OLED display for patch browsing"""

    def __init__(self, i2c_port=I2C_PORT, i2c_address=I2C_ADDRESS):
        print(f"Initializing {DISPLAY_TYPE} display on I2C port {i2c_port}, address 0x{i2c_address:02X}")

        # Initialize I2C interface
        serial = i2c(port=i2c_port, address=i2c_address)

        # Initialize display device with 180 degree rotation (inverted vertically)
        self.device = display_device(serial, rotate=2)  # rotate=2 = 180 degrees

        # Load default font (built-in, always available)
        self.font_small = ImageFont.load_default()

        # Try to load a larger font if available
        try:
            # Try to use a TrueType font for better readability
            self.font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 12)
        except:
            self.font_large = self.font_small

        print(f"Display initialized: {self.device.width}x{self.device.height}")
        self.show_splash()

    def show_splash(self):
        """Show startup splash screen"""
        with canvas(self.device) as draw:
            draw.text((0, 10), "Pi-Surge-MPE", fill="white", font=self.font_large)
            draw.text((0, 25), "Patch Browser", fill="white", font=self.font_small)
            draw.text((0, 40), "Initializing...", fill="white", font=self.font_small)
        time.sleep(0.2)  # Reduced from 1s for faster boot

    def show_patch(self, category, patch_name, loaded_category, category_idx, category_total,
                   patch_idx, patch_total, scroll_mode, patches_list=None, categories_list=None, loading=False, scanner=None, loaded_patch_name=None):
        """
        Display current patch selection with optimized layout

        PATCH MODE Layout:
        ┌─────────────────────┐
        │ Category    [12/51] │  Header with count
        │ ───────────────     │  Separator
        │ > Current Patch   · │  Selected (with >) + loading dot
        │   Next Patch        │  Preview
        │   Next Patch 2      │  Preview
        └─────────────────────┘

        CATEGORY MODE Layout:
        ┌─────────────────────┐
        │              [12/51] │  Just count
        │ ───────────────     │  Separator
        │ > Current Category  │  Selected (with >)
        │   Next Category     │  Preview
        │   Next Category 2   │  Preview
        └─────────────────────┘
        """
        with canvas(self.device) as draw:
            if scroll_mode == SCROLL_MODE_PATCH:
                # PATCH MODE: Show category name + patch count
                category_display = category[:14] if len(category) > 14 else category
                count_text = f"[{patch_idx+1}/{patch_total}]"
                draw.text((0, 0), category_display, fill="white", font=self.font_small)
                # Right-align the count
                count_width = len(count_text) * 6  # Approximate width
                draw.text((128 - count_width, 0), count_text, fill="white", font=self.font_small)
            else:
                # CATEGORY MODE: Show "LoadedCategory/LoadedPatch" on left, count on right
                # Display format: "Category/Patch" (showing what's actually playing, not what's being browsed)
                if loaded_category:
                    # Show the actually loaded patch info
                    path_display = f"{loaded_category}/{patch_name[:8]}" if patch_name != "(No patches)" else loaded_category
                else:
                    # Fallback if nothing loaded yet
                    path_display = f"{category}/{patch_name[:8]}" if patch_name != "(No patches)" else category
                # Truncate to fit with count
                path_display = path_display[:14] if len(path_display) > 14 else path_display
                draw.text((0, 0), path_display, fill="white", font=self.font_small)

                # Category count (right-aligned)
                count_text = f"[{category_idx+1}/{category_total}]"
                count_width = len(count_text) * 6
                draw.text((128 - count_width, 0), count_text, fill="white", font=self.font_small)

            # Separator line
            draw.line((0, 10, 127, 10), fill="white")

            # Current item (larger, with indicator)
            if scroll_mode == SCROLL_MODE_PATCH:
                current_display = patch_name[:17] if len(patch_name) > 17 else patch_name
            else:
                current_display = category[:19] if len(category) > 19 else category

            draw.text((0, 14), f">{current_display}", fill="white", font=self.font_large)

            # Show indicators for current patch (right-aligned)
            if scroll_mode == SCROLL_MODE_PATCH and scanner and patches_list and patch_idx < len(patches_list):
                current_patch = patches_list[patch_idx]
                # Show loaded indicator (filled circle) if this is the currently loaded patch
                if loaded_patch_name and loaded_category == category and loaded_patch_name == patch_name:
                    draw.ellipse((108, 16, 112, 20), fill="white", outline="white")

            # Show loading indicator (small dot at end of current line, left of heart)
            # Only show if we're actively loading AND it's not the currently loaded patch
            if loading and scroll_mode == SCROLL_MODE_PATCH:
                draw.ellipse((114, 16, 118, 20), fill="white", outline="white")

            # Preview next items (smaller font)
            y_offset = 28
            if scroll_mode == SCROLL_MODE_PATCH and patches_list:
                # Show next 2-3 patches with favorite hearts
                for i in range(1, min(4, len(patches_list))):
                    next_idx = (patch_idx + i) % len(patches_list)
                    next_patch_obj = patches_list[next_idx]
                    next_patch = next_patch_obj['name']
                    next_display = next_patch[:20] if len(next_patch) > 20 else next_patch
                    draw.text((2, y_offset), next_display, fill="white", font=self.font_small)
                    y_offset += 12
            elif scroll_mode == SCROLL_MODE_CATEGORY and categories_list:
                # Show next 2-3 categories
                for i in range(1, min(4, len(categories_list))):
                    next_idx = (category_idx + i) % len(categories_list)
                    next_cat = categories_list[next_idx]
                    next_display = next_cat[:20] if len(next_cat) > 20 else next_cat
                    draw.text((2, y_offset), next_display, fill="white", font=self.font_small)
                    y_offset += 12

    def show_loading(self, patch_name):
        """Show loading screen when applying patch"""
        with canvas(self.device) as draw:
            draw.text((0, 20), "Loading...", fill="white", font=self.font_large)
            patch_display = patch_name[:18] if len(patch_name) > 18 else patch_name
            draw.text((0, 35), patch_display, fill="white", font=self.font_small)

    def show_error(self, message, timeout=7):
        """
        Show error message with optional timeout

        Args:
            message: Error text to display
            timeout: Seconds to show error (0 = indefinite)
        """
        with canvas(self.device) as draw:
            draw.text((0, 10), "ERROR:", fill="white", font=self.font_large)
            # Word wrap for long messages
            words = message.split()
            line = ""
            y = 25
            for word in words:
                test_line = f"{line} {word}".strip()
                if len(test_line) > 20:
                    draw.text((0, y), line, fill="white", font=self.font_small)
                    line = word
                    y += 12
                else:
                    line = test_line
            if line:
                draw.text((0, y), line, fill="white", font=self.font_small)

        if timeout > 0:
            time.sleep(timeout)
            self.device.clear()

    def show_error_and_continue(self, message, timeout=7):
        """
        Show error message, wait, then clear display and continue.
        Helper for error recovery pattern.
        """
        print(f"ERROR: {message}")
        self.show_error(message, timeout=timeout)
        # Display is already cleared by show_error

    def show_dialog(self, dialog_type, selection, patch=None, power_action=None):
        """Show confirmation dialog"""
        with canvas(self.device) as draw:
            if dialog_type == "copy_to_favorites":
                # Show copy to favorites confirmation
                patch_name = patch['name'][:18] if patch and len(patch['name']) > 18 else (patch['name'] if patch else "")
                draw.text((0, 0), f"Copy to {favorites_display_name()}?"[:20], fill="white", font=self.font_small)
                draw.text((0, 12), patch_name, fill="white", font=self.font_small)
                draw.text((0, 30), "> No" if selection == 0 else "  No", fill="white", font=self.font_small)
                draw.text((0, 42), "  Yes" if selection == 0 else "> Yes", fill="white", font=self.font_small)
            elif dialog_type == "power_menu":
                # Show power menu with shutdown/restart/cancel
                draw.text((0, 0), "Power Menu", fill="white", font=self.font_small)
                draw.text((0, 20), "> Shutdown" if selection == 0 else "  Shutdown", fill="white", font=self.font_small)
                draw.text((0, 32), "  Restart" if selection != 1 else "> Restart", fill="white", font=self.font_small)
                draw.text((0, 44), "  Cancel" if selection != 2 else "> Cancel", fill="white", font=self.font_small)
            elif dialog_type == "power_confirm":
                # Show power confirmation
                action_text = "Shutdown?" if power_action == "shutdown" else "Restart?"
                draw.text((0, 0), action_text, fill="white", font=self.font_small)
                draw.text((0, 20), "Are you sure?", fill="white", font=self.font_small)
                draw.text((0, 36), "> No" if selection == 0 else "  No", fill="white", font=self.font_small)
                draw.text((0, 48), "  Yes" if selection == 0 else "> Yes", fill="white", font=self.font_small)
            elif dialog_type == "surge_error":
                # Show Surge error/restart screen
                status = patch.get('status', 'ERROR') if patch else 'ERROR'
                details = patch.get('details', 'Unknown error') if patch else 'Unknown error'
                can_restart = patch.get('can_restart', False) if patch else False

                draw.text((0, 0), "SURGE XT CLI", fill="white", font=self.font_small)
                draw.text((0, 12), f"Status: {status}", fill="white", font=self.font_small)

                # Show error details (word wrapped)
                words = details.split()
                line = ""
                y = 26
                for word in words:
                    test_line = f"{line} {word}".strip()
                    if len(test_line) > 20:
                        draw.text((0, y), line, fill="white", font=self.font_small)
                        line = word
                        y += 11
                        if y > 48:  # Stop if too many lines
                            break
                    else:
                        line = test_line
                if line and y <= 48:
                    draw.text((0, y), line, fill="white", font=self.font_small)

                # Show restart option if applicable
                if can_restart:
                    draw.text((0, 54), "> Restart  Back" if selection == 0 else "  Restart  > Back",
                             fill="white", font=self.font_small)
                else:
                    draw.text((0, 54), "Press to continue", fill="white", font=self.font_small)

# ============================================================================
# PATCH LOADER
# ============================================================================

class PatchLoader:
    """Loads patches into Surge XT CLI via OSC"""

    def __init__(self, osc_host='127.0.0.1', osc_port=53280):
        """
        Initialize OSC client for Surge XT

        Args:
            osc_host: IP address of Surge (default: localhost)
            osc_port: OSC port (default: 53280)
        """
        try:
            from pythonosc import udp_client
            self.osc_client = udp_client.SimpleUDPClient(osc_host, osc_port)
            self.osc_enabled = True
            print(f"OSC client initialized: {osc_host}:{osc_port}")
        except ImportError:
            print("Warning: python-osc not installed, patch loading disabled")
            self.osc_enabled = False
        except Exception as e:
            print(f"Warning: OSC client failed to initialize: {e}")
            self.osc_enabled = False

    def set_volume(self, volume=1.5):
        """
        Set Surge XT master volume via OSC
        
        Args:
            volume: Volume level (1.0 = default, 1.5 = 150%, etc.)
        
        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.osc_enabled:
            return False
        
        try:
            # Set volume for both scenes
            self.osc_client.send_message('/param/a/amp/volume', volume)
            self.osc_client.send_message('/param/b/amp/volume', volume)
            return True
        except Exception as e:
            print(f"Error setting volume via OSC: {e}")
            return False

    def load_patch(self, patch_path):
        """
        Load a patch into Surge XT via OSC

        Surge OSC command: /patch/load <path_without_fxp_extension>
        Surge automatically appends .fxp to the path

        Args:
            patch_path: Full path to .fxp file

        Returns:
            True if command sent successfully, False otherwise
        """
        if not self.osc_enabled:
            print(f"OSC disabled, cannot load: {patch_path}")
            return False

        try:
            # Remove .fxp extension (Surge adds it automatically)
            path_no_ext = str(patch_path)
            if path_no_ext.endswith('.fxp') or path_no_ext.endswith('.FXP'):
                path_no_ext = path_no_ext[:-4]

            # Send OSC message: /patch/load <path>
            self.osc_client.send_message('/patch/load', [path_no_ext])
            print(f"Loaded patch: {Path(patch_path).name}")
            
            # Volume is at default (100%) - no adjustment needed
            
            return True

        except Exception as e:
            print(f"Error loading patch via OSC: {e}")
            return False

# ============================================================================
# SURGE XT CLI HEALTH MONITOR
# ============================================================================

class SurgeMonitor:
    """Monitors Surge XT CLI process health and provides restart capability"""

    def __init__(self, log_file=None):
        if log_file is None:
            log_file = os.environ.get('MPE_SURGE_LOG', os.path.expanduser('~/surge-cli.log'))
        """
        Initialize Surge health monitor

        Args:
            log_file: Path to Surge CLI log file for error checking
        """
        self.log_file = Path(log_file)
        self.surge_pid = None
        self.last_check_time = 0
        self.check_interval = 2.0  # Check every 2 seconds
        self.is_healthy = False
        self.last_error = None
        self.startup_time = time.time()

        # Try to find existing Surge process
        self._find_surge_process()

    def _find_surge_process(self):
        """Find running Surge XT CLI process"""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'surge-xt-cli'],
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode == 0 and result.stdout.strip():
                self.surge_pid = int(result.stdout.strip().split()[0])
                self.is_healthy = True
                print(f"Found existing Surge process: PID {self.surge_pid}")
            else:
                self.is_healthy = False
                self.last_error = "Surge not running"
        except Exception as e:
            print(f"Error finding Surge process: {e}")
            self.is_healthy = False
            self.last_error = str(e)

    def check_health(self):
        """
        Check if Surge is running and healthy

        Returns:
            tuple: (is_healthy: bool, error_message: str or None)
        """
        current_time = time.time()

        # Rate limit checks
        if current_time - self.last_check_time < self.check_interval:
            return self.is_healthy, self.last_error

        self.last_check_time = current_time

        # Skip checks during initial startup (give Surge time to start)
        if current_time - self.startup_time < 5.0:
            return True, None

        # Check if process is still running
        if self.surge_pid is not None:
            try:
                # Send signal 0 to check if process exists
                os.kill(self.surge_pid, 0)
                self.is_healthy = True
                self.last_error = None
                return True, None
            except OSError:
                # Process doesn't exist
                self.is_healthy = False
                self.last_error = "Surge crashed or exited"

                # Try to get error from log
                error_detail = self._get_last_error_from_log()
                if error_detail:
                    self.last_error = error_detail

                return False, self.last_error
        else:
            # No PID tracked, try to find process
            self._find_surge_process()
            return self.is_healthy, self.last_error

    def _get_last_error_from_log(self):
        """Extract last error message from Surge log file"""
        try:
            if not self.log_file.exists():
                return None

            # Read last 50 lines of log
            result = subprocess.run(
                ['tail', '-50', str(self.log_file)],
                capture_output=True,
                text=True,
                timeout=1
            )

            if result.returncode != 0:
                return None

            lines = result.stdout.strip().split('\n')

            # Look for error indicators
            for line in reversed(lines):
                line_lower = line.lower()
                if any(keyword in line_lower for keyword in ['error', 'fatal', 'crash', 'failed', 'unable']):
                    # Extract meaningful part
                    if 'error' in line_lower:
                        parts = line.split('Error:', 1)
                        if len(parts) > 1:
                            return parts[1].strip()[:50]  # Limit to 50 chars
                    return line.strip()[:50]

            return None
        except Exception as e:
            print(f"Error reading Surge log: {e}")
            return None

    def restart_surge(self):
        """
        Restart Surge XT CLI service

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            print("Attempting to restart Surge XT CLI...")
            result = subprocess.run(
                ['sudo', 'systemctl', 'restart', 'surge-xt-cli.service'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                # Wait for process to start
                time.sleep(3)
                self._find_surge_process()

                if self.is_healthy:
                    return True, "Surge restarted"
                else:
                    return False, "Restart failed - check logs"
            else:
                return False, f"systemctl error: {result.stderr[:30]}"

        except subprocess.TimeoutExpired:
            return False, "Restart timeout"
        except Exception as e:
            return False, f"Error: {str(e)[:30]}"

    def get_status_summary(self):
        """
        Get human-readable status summary

        Returns:
            dict: {'status': str, 'details': str, 'can_restart': bool}
        """
        is_healthy, error = self.check_health()

        if is_healthy:
            return {
                'status': 'Running',
                'details': f'PID {self.surge_pid}',
                'can_restart': False
            }
        else:
            return {
                'status': 'Not Running',
                'details': error or 'Unknown error',
                'can_restart': True
            }


# ============================================================================
# ENCODER EVENT HANDLER (KERNEL-LEVEL)
# ============================================================================

class EncoderEventHandler:
    """Reads rotary encoder events from Linux kernel via evdev

    This class interfaces with the kernel's rotary-encoder driver to get
    hardware-timed, debounced encoder events. Much more reliable than
    software polling with gpiozero.
    """

    def __init__(self, scroll_callback):
        """Initialize encoder event handler

        Args:
            scroll_callback: Function to call with encoder value (+1 CW, -1 CCW)
        """
        self.device = self._find_encoder_device()
        self.scroll_callback = scroll_callback
        self.running = True
        self.thread = None
        self.last_event_time = 0
        self.last_direction = 0
        self.event_counter = 0
        self.direction_change_delay_ms = 2  # Minimal delay (2ms) - hardware capacitors handle noise

    def _find_encoder_device(self):
        """Find rotary encoder device by capabilities"""
        if not EVDEV_AVAILABLE:
            raise ImportError("python-evdev not installed")

        # Try by-path first (most stable across reboots)
        paths = [
            '/dev/input/by-path/platform-rotary@11-event',
            '/dev/input/by-path/platform-rotary@17-event',
        ]
        for path in paths:
            if Path(path).exists():
                return InputDevice(str(path))

        # Fallback: scan all devices by capabilities
        for path in evdev.list_devices():
            device = InputDevice(path)
            caps = device.capabilities()
            # Look for device with REL_DIAL capability (rotary encoder)
            if ecodes.EV_REL in caps:
                if ecodes.REL_DIAL in caps[ecodes.EV_REL]:
                    print(f"Found rotary encoder: {device.name} at {path}")
                    return device

        raise FileNotFoundError("Rotary encoder device not found. Is the device tree overlay configured?")

    def _event_loop(self):
        """Blocking event loop (runs in separate thread)"""
        try:
            for event in self.device.read_loop():
                if not self.running:
                    break

                # Process encoder rotation events
                if event.type == ecodes.EV_REL:
                    # Check for both REL_X and REL_DIAL (depends on overlay config)
                    if event.code in (ecodes.REL_X, ecodes.REL_DIAL):
                        current_time = time.time() * 1000  # Convert to ms
                        direction = event.value  # +1 (CW) or -1 (CCW)

                        # Direction change detection with time-based filter
                        if direction != self.last_direction:
                            # Direction changed - only accept if enough time passed since last event
                            time_since_last = current_time - self.last_event_time
                            if time_since_last < self.direction_change_delay_ms:
                                # Too soon for direction change - likely noise, ignore
                                continue

                            # Accept direction change
                            self.last_direction = direction
                            self.event_counter = 1
                            self.last_event_time = current_time
                        else:
                            # Same direction - increment counter
                            self.event_counter += 1

                            # Process after seeing 1 event (hardware capacitors handle noise)
                            # KY-040 generates 2 events per detent, but with hardware debouncing we can process immediately
                            if self.event_counter >= 1:
                                self.scroll_callback(direction)
                                self.event_counter = 0  # Reset for next detent
                                self.last_event_time = current_time
        except OSError:
            pass  # Device closed during shutdown

    def start(self):
        """Start event handler thread"""
        self.thread = threading.Thread(target=self._event_loop, daemon=True, name="EncoderThread")
        self.thread.start()

    def stop(self):
        """Stop event handler and close device"""
        self.running = False
        if self.device:
            self.device.close()

# ============================================================================
# MAIN BROWSER APPLICATION
# ============================================================================

class PatchBrowser:
    """Main application coordinating encoder, display, and patch loading"""

    def _wait_for_surge_ready(self, timeout=30):
        """Wait for Surge XT CLI to be ready before proceeding
        
        Checks:
        1. Surge service is active
        2. OSC port is listening
        3. Surge process is running
        
        Returns when ready, or after timeout
        """
        import subprocess
        import socket
        
        print("Waiting for Surge XT CLI to be ready...")
        start_time = time.time()
        
        while (time.time() - start_time) < timeout:
            # Check 1: Is Surge service active?
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', 'surge-xt-cli.service'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode != 0 or result.stdout.strip() != 'active':
                    time.sleep(0.5)
                    continue
            except Exception:
                time.sleep(0.5)
                continue
            
            # Check 2: Is OSC port listening?
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(0.1)
                # Try to connect to OSC port (UDP doesn't really "connect" but we can check if it's bound)
                # Instead, check if Surge process is listening on the port
                result = subprocess.run(
                    ['netstat', '-uln'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if '53280' in result.stdout:
                    print("Surge XT CLI is ready (service active, OSC port listening)")
                    return True
            except Exception:
                pass
            
            # Check 3: Is Surge process running?
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'surge-xt-cli'],
                    capture_output=True,
                    timeout=1
                )
                if result.returncode == 0:
                    # Process is running, give it a moment for OSC to initialize
                    time.sleep(1)
                    print("Surge XT CLI process detected, assuming ready")
                    return True
            except Exception:
                pass
            
            time.sleep(0.5)
        
        print(f"Warning: Surge XT CLI not ready after {timeout}s, proceeding anyway")
        return False

    def __init__(self):
        print("=== Pi-Surge-MPE Patch Browser ===\n")

        # Initialize display with error handling
        try:
            self.display = PatchDisplay()
        except Exception as e:
            print(f"FATAL: Display initialization failed: {e}")
            sys.exit(1)  # Cannot continue without display

        # Wait for Surge to be ready before initializing loader
        self._wait_for_surge_ready()
        
        # Initialize loader - Surge should be ready now
        try:
            self.loader = PatchLoader()
            if not self.loader.osc_enabled:
                print("Warning: OSC not available - patch loading will not work")
                # Continue anyway - UI still useful for browsing
        except Exception as e:
            print(f"Warning: Patch loader initialization failed: {e}")
            self.loader = None  # Will need to check for None before loading

        # Initialize Surge health monitor
        try:
            self.surge_monitor = SurgeMonitor()
            print(f"Surge monitor initialized: {self.surge_monitor.get_status_summary()['status']}")
        except Exception as e:
            print(f"Warning: Surge monitor initialization failed: {e}")
            self.surge_monitor = None

        # Initialize scanner WITHOUT full scan (will scan in background)
        self.scanner = PatchScanner(SURGE_PATCH_DIRS)

        # Try to load last patch state BEFORE scanning
        last_patch_state = self.scanner.load_last_patch()

        if last_patch_state:
            # Quick-load last patch IMMEDIATELY
            print(f"Quick-loading last patch: {last_patch_state['category']} / {Path(last_patch_state['patch_path']).stem}")

            # Show loading message
            with canvas(self.display.device) as draw:
                draw.text((0, 20), "Loading last patch...", fill="white", font=self.display.font_small)
                draw.text((0, 35), Path(last_patch_state['patch_path']).stem[:20], fill="white", font=self.display.font_small)

            # Quick scan ONLY the last category to get minimal data
            last_patch_path = Path(last_patch_state['patch_path'])
            category_path = last_patch_path.parent
            quick_patches = self.scanner.quick_scan_category(category_path)

            # Add to scanner's patch dict temporarily
            category_name = last_patch_state['category']
            self.scanner.patches[category_name] = quick_patches
            self.categories = [category_name]  # Temporary single-category list

            # Load the patch immediately (if loader is available)
            if self.loader:
                success = self.loader.load_patch(last_patch_state['patch_path'])
                if success:
                    print(f"Successfully loaded last patch: {Path(last_patch_state['patch_path']).stem}")
                    # Volume is at default (100%) - no adjustment needed
                    # Set loaded_patch_info only if load succeeded
                    self.loaded_patch_info = {
                        'name': Path(last_patch_state['patch_path']).stem,
                        'category': category_name
                    }
                else:
                    print(f"Warning: Failed to load last patch: {last_patch_state['patch_path']}")
                    # Don't show as loaded if it didn't actually load
                    self.loaded_patch_info = None
            else:
                print("Warning: Cannot load patch - loader not initialized")
                # Don't show as loaded if loader isn't available
                self.loaded_patch_info = None

            # Set initial state
            self.category_index = 0
            self.patch_index = next((i for i, p in enumerate(quick_patches)
                                    if str(p['path']) == last_patch_state['patch_path']), 0)

            # loaded_patch_info is already set above if patch loaded successfully
            # If it's None, that means the patch didn't load (which is correct)
        else:
            # No last patch - show "Scanning patches..." message
            with canvas(self.display.device) as draw:
                draw.text((0, 20), "Scanning patches...", fill="white", font=self.display.font_small)
                draw.text((0, 35), "Please wait...", fill="white", font=self.display.font_small)

            self.category_index = 0
            self.patch_index = 0
            self.loaded_patch_info = None

        # Start FULL background scan now
        self.scanner.scan_patches_background()

        self.scroll_mode = SCROLL_MODE_PATCH  # Start in patch mode

        # Track the actually loaded patch (separate from browsing position)
        # Will be updated when patches are loaded

        # Event accumulator (atomic operations only - prevents kernel queue buildup)
        self.scroll_events = 0  # Positive = CW, Negative = CCW
        self.scroll_lock = threading.Lock()

        # Directional debouncing for CCW stability
        self.last_direction = None  # Track last scroll direction
        self.last_scroll_time = 0  # Track last scroll event time
        self.direction_change_min_time = 0.002  # Minimum 2ms between direction changes (hardware capacitors handle noise)

        # Button state
        self.button_press_in_progress = False  # Flag to completely ignore encoder during button press
        self.button_press_start_time = None  # Track when button was first pressed for long-press detection
        self.last_button_time = 0  # Track last button press time for isolation
        self.encoder_cooldown_until = 0  # Timestamp: ignore encoder events until this time (after button operations)
        
        # Track recent encoder events to discard false scrolls when button is pressed
        self.recent_encoder_events = []  # List of (timestamp, value) tuples for last 200ms
        
        # Confirmation dialog state
        self.dialog_active = False
        self.dialog_type = None  # "copy_to_favorites", "power_menu", "power_confirm"
        self.dialog_selection = 0  # Selection index (varies by dialog type)
        self.dialog_patch = None  # Patch to copy (if dialog active)
        self.power_action = None  # "shutdown" or "restart" (when in power_confirm dialog)
        self.dialog_open_time = None  # Timestamp when dialog was opened (to ignore opening button release)
        self.ignore_next_button_release = False  # Flag to ignore next release (e.g., when dialog opens while button held)

        # Load debouncing (wait before loading to prevent rapid loads during scroll)
        self.pending_load_timer = None
        self.is_loading = False

        # Wait briefly for USB devices to stabilize after boot
        time.sleep(1)

        # Initialize encoder - try kernel-level first, fallback to gpiozero
        self.use_evdev = False
        self.encoder = None
        self.encoder_handler = None

        try:
            # Try kernel-level encoder via evdev
            self.encoder_handler = EncoderEventHandler(
                scroll_callback=self._on_encoder_rotate
            )
            self.encoder_handler.start()
            self.use_evdev = True
            print("Using kernel-level encoder (evdev) - high reliability mode")
        except Exception as e:
            # Fall back to gpiozero
            print(f"Kernel encoder unavailable ({e.__class__.__name__}: {e})")
            print("Falling back to software encoder (gpiozero)")
            self.use_evdev = False
            self.encoder = RotaryEncoder(
                ENCODER_CLK,
                ENCODER_DT,
                bounce_time=DEBOUNCE_TIME,
                max_steps=0  # Unlimited rotation
            )
            self.encoder.when_rotated_clockwise = self._on_rotate_cw
            self.encoder.when_rotated_counter_clockwise = self._on_rotate_ccw

        # Initialize button (always use gpiozero for simplicity)
        self.button = Button(
            ENCODER_SW,
            pull_up=True,
            bounce_time=BUTTON_DEBOUNCE
        )
        self.button.when_pressed = self._on_button_down
        self.button.when_released = self._on_button_up

        import sys
        sys.stderr.write(f"[INIT] Button initialized on GPIO {ENCODER_SW}\n")
        sys.stderr.write(f"[INIT] Button callbacks registered: when_pressed={self.button.when_pressed}, when_released={self.button.when_released}\n")
        sys.stderr.flush()
        print(f"Encoder initialized on GPIO {ENCODER_CLK}/{ENCODER_DT}/{ENCODER_SW}")

        # Wait briefly for background scan if not complete
        if not self.scanner.scan_complete.is_set():
            print("Waiting for patch scan to complete...")
            scan_finished = self.scanner.wait_for_scan(timeout=5.0)  # Wait max 5 seconds
            if scan_finished:
                # Update categories list now that scan is complete
                self.categories = self.scanner.get_categories()

                # Re-find category index if it changed during full scan
                if last_patch_state:
                    try:
                        self.category_index = self.categories.index(last_patch_state['category'])
                    except ValueError:
                        self.category_index = 0
            else:
                print("Warning: Patch scan still in progress, UI may be incomplete")
                # Categories will update when scan completes
        else:
            self.categories = self.scanner.get_categories()

        print(f"Loaded {len(self.categories)} categories")
        print(f"Quick-access folder: {FAVORITES_NAME} (browser: {favorites_display_name()})")
        print("Button: 0.5-2s = Change Mode, >2s = Copy to quick-access (hold ~1s to confirm in dialog)")
        print("Press Ctrl+C to exit\n")

        # Load the initial patch first (if not already loaded), then update display
        if not last_patch_state and self.categories:
            self.load_current_patch_immediate()
        elif not self.categories:
            print("Warning: No patch categories found — check Surge patch symlinks (see docs/PATHS.md)")
        self.update_display()

    def _on_encoder_rotate(self, value):
        """Handle encoder rotation from evdev (kernel debounced)

        Args:
            value: +1 for CW, -1 for CCW (from kernel driver)
        """
        current_time = time.time()
        
        # Apply unified filtering (works for both dialogs and normal mode)
        # This ensures consistent behavior regardless of encoder source (evdev or gpiozero)
        should_process, reason = self._should_process_encoder_event(current_time, value)
        if not should_process:
            return  # Blocked by filtering logic

        # Track this event in recent events buffer (for button press lookback)
        self.recent_encoder_events.append((current_time, value))
        # Keep only last 200ms of events
        cutoff_time = current_time - 0.2
        self.recent_encoder_events = [(ts, val) for ts, val in self.recent_encoder_events if ts >= cutoff_time]

        # Accumulate event (kernel already debounced, but we still apply our filtering)
        with self.scroll_lock:
            self.scroll_events += value

        # Update direction tracking for direction change filtering
        if value > 0:
            self.last_direction = "CW"
        else:
            self.last_direction = "CCW"
        self.last_scroll_time = current_time

    def _on_rotate_cw(self):
        """Handle clockwise rotation - unified filtering"""
        current_time = time.time()
        
        # Apply unified filtering (works for both dialogs and normal mode)
        should_process, reason = self._should_process_encoder_event(current_time, 1)
        if not should_process:
            return  # Blocked by filtering logic
        
        # Track in recent events buffer (for lookback window)
        self.recent_encoder_events.append((current_time, 1))
        # Keep only last 200ms of events
        cutoff_time = current_time - 0.2
        self.recent_encoder_events = [(ts, val) for ts, val in self.recent_encoder_events if ts >= cutoff_time]
        
        # Accumulate event
        with self.scroll_lock:
            self.scroll_events += 1
        
        self.last_direction = "CW"
        self.last_scroll_time = current_time

    def _on_rotate_ccw(self):
        """Handle counter-clockwise rotation - unified filtering"""
        current_time = time.time()
        
        # Apply unified filtering (works for both dialogs and normal mode)
        should_process, reason = self._should_process_encoder_event(current_time, -1)
        if not should_process:
            return  # Blocked by filtering logic
        
        # Track in recent events buffer (for lookback window)
        self.recent_encoder_events.append((current_time, -1))
        # Keep only last 200ms of events
        cutoff_time = current_time - 0.2
        self.recent_encoder_events = [(ts, val) for ts, val in self.recent_encoder_events if ts >= cutoff_time]
        
        # Accumulate event
        with self.scroll_lock:
            self.scroll_events -= 1
        
        self.last_direction = "CCW"
        self.last_scroll_time = current_time

    def _on_button_down(self):
        """Handle button press start - record time and start poweroff timer"""
        import threading
        import subprocess
        import sys
        # Log IMMEDIATELY at function entry - before any checks
        try:
            sys.stderr.write("[BUTTON] _on_button_down CALLED\n")
            sys.stderr.flush()
        except:
            pass
        
        current_time = time.time()
        
        # Write to stderr (unbuffered) and flush immediately
        try:
            msg = f"[BUTTON] Button down detected at {current_time:.3f}\n"
            sys.stderr.write(msg)
            sys.stderr.flush()
            print(msg.strip())
        except Exception as e:
            sys.stderr.write(f"[BUTTON] Error logging: {e}\n")
            sys.stderr.flush()

        # Reject spurious button-down events (bounce from previous release)
        # Only block if very recent (within 10ms) to prevent double-triggers
        if (current_time - self.last_button_time) < BUTTON_DEBOUNCE:
            msg = f"[BUTTON] Rejected (too soon after last: {current_time - self.last_button_time:.3f}s)\n"
            sys.stderr.write(msg)
            sys.stderr.flush()
            print(msg.strip())
            return

        # Clear stale button state if somehow still set from previous spurious event
        if self.button_press_in_progress:
            self.button_press_in_progress = False

        # Set flag to completely ignore encoder events during button press
        # Do this FIRST to block any encoder events that might come in during button press
        self.button_press_in_progress = True
        self.button_press_start_time = current_time
        
        # Clear any accumulated encoder events (prevent false triggers)
        with self.scroll_lock:
            self.scroll_events = 0
        
        # Set encoder cooldown to prevent false scrolls from mechanical coupling
        # Also set a pre-button cooldown to ignore events that happened just before button press
        self.encoder_cooldown_until = current_time + self._get_encoder_cooldown()
        self.button_press_start_time = current_time  # Track when button was pressed

        # Start a timer to show power menu after 8 seconds
        import sys
        msg = f"[BUTTON] Starting power menu timer thread...\n"
        sys.stderr.write(msg)
        sys.stderr.flush()
        print(msg.strip())
        
        def power_menu_timer():
            import sys
            import time
            saved_start_time = current_time  # Capture the start time for this press
            msg = f"[POWER] Timer started, waiting {POWEROFF_PRESS_MIN}s for power menu...\n"
            sys.stderr.write(msg)
            sys.stderr.flush()
            print(msg.strip())
            
            # Wait for 8 seconds, checking every second
            for i in range(int(POWEROFF_PRESS_MIN)):
                time.sleep(1)
                # Check if button is still pressed and this is still the same press
                if not self.button.is_pressed:
                    msg = f"[POWER] Button released at {i+1}s, cancelling timer\n"
                    sys.stderr.write(msg)
                    sys.stderr.flush()
                    print(msg.strip())
                    return  # Button released, cancel timer
                
                # Check if button_press_start_time changed (new press started)
                if self.button_press_start_time != saved_start_time:
                    msg = f"[POWER] New button press detected at {i+1}s, cancelling timer\n"
                    sys.stderr.write(msg)
                    sys.stderr.flush()
                    print(msg.strip())
                    return  # New press started, cancel this timer
                
                msg = f"[POWER] Still holding... {i+1}/{int(POWEROFF_PRESS_MIN)}s\n"
                sys.stderr.write(msg)
                sys.stderr.flush()
                print(msg.strip())

            # 8 seconds elapsed and button still pressed - show power menu
            if self.button.is_pressed and self.button_press_start_time == saved_start_time:
                msg = f"\n*** POWER MENU (8 seconds elapsed) ***\n"
                sys.stderr.write(msg)
                sys.stderr.flush()
                print(msg.strip())
                # Show power menu (while button is still held)
                self.dialog_active = True
                self.dialog_type = "power_menu"
                self.dialog_selection = 2  # Start with "Cancel" selected (safest default)
                self.power_action = None
                self.dialog_open_time = time.time()  # Track when power menu opened
                # CRITICAL: Reset button_press_start_time so the release doesn't have a valid press duration
                # This ensures we require a NEW press after menu opens, not just a release
                self.button_press_start_time = None
                # Keep button_press_in_progress True initially, but clear it when button is released
                # Set cooldown to prevent false encoder events
                # Use longer cooldown when power menu first appears (button still held)
                self.encoder_cooldown_until = time.time() + 0.2
                self.update_display()
            else:
                msg = f"[POWER] Timer expired but conditions not met: is_pressed={self.button.is_pressed}, start_time_match={self.button_press_start_time == saved_start_time}\n"
                sys.stderr.write(msg)
                sys.stderr.flush()
                print(msg.strip())

        threading.Thread(target=power_menu_timer, daemon=True).start()

    def _should_process_encoder_event(self, current_time, direction_value):
        """
        Unified encoder event filtering - applies same logic in dialogs and normal mode.
        
        This method centralizes all encoder event filtering logic to ensure consistent
        behavior across normal browsing and dialog modes. It prevents encoder events
        during button operations, applies cooldowns, and filters noise.
        
        Args:
            current_time: Current timestamp (from time.time())
            direction_value: +1 for CW, -1 for CCW
            
        Returns:
            tuple: (should_process: bool, reason: str)
        """
        # Always check physical button state (mechanical coupling)
        if self.button.is_pressed:
            return False, "button_physically_pressed"
        
        # Always check button press in progress flag
        if self.button_press_in_progress:
            return False, "button_press_in_progress"
        
        # Always check cooldown period (applies to both dialogs and normal mode)
        if current_time < self.encoder_cooldown_until:
            return False, "cooldown_active"
        
        # Check lookback window (prevent false scrolls before button clicks)
        # This checks for events that occurred just before a button press
        if self.button_press_start_time:
            time_before_press = current_time - self.button_press_start_time
            # Within 200ms before button press (negative time means before press)
            if -0.2 <= time_before_press < 0:
                return False, "lookback_window"
        
        # Direction change filtering (only in normal mode, dialogs are more forgiving)
        if not self.dialog_active:
            if direction_value > 0 and self.last_direction == "CCW":
                if (current_time - self.last_scroll_time) < self.direction_change_min_time:
                    return False, "direction_change_too_fast"
            elif direction_value < 0 and self.last_direction == "CW":
                if (current_time - self.last_scroll_time) < self.direction_change_min_time:
                    return False, "direction_change_too_fast"
        
        return True, "ok"
    
    def _should_ignore_encoder(self, current_time):
        """Check if encoder events should be ignored due to button state (legacy method)"""
        should_process, _ = self._should_process_encoder_event(current_time, 0)
        return not should_process
    
    def _get_encoder_cooldown(self):
        """Get appropriate cooldown duration based on context"""
        if self.dialog_active:
            return 0.025  # 25ms for dialogs (more responsive)
        return ENCODER_POST_BUTTON_COOLDOWN  # 50ms for normal mode
    
    def _should_process_button_release(self, press_duration, current_time):
        """
        Unified logic for determining if button release should be processed.
        
        This method centralizes button release filtering to ensure consistent
        behavior across normal browsing and dialog modes.
        
        Args:
            press_duration: How long the button was held (None if button_press_start_time was reset)
            current_time: Current timestamp
            
        Returns:
            tuple: (should_process: bool, reason: str)
        """
        # If button_press_start_time is None, this release has no valid press duration
        # This happens when dialog opened while button was held (we reset it)
        if self.button_press_start_time is None:
            return False, "no_valid_press_duration"
        
        # In dialogs, require bold press (0.5s+) to select
        if self.dialog_active:
            if press_duration < BOLD_PRESS_MIN:
                return False, "press_too_short_for_dialog"
        
        return True, "ok"

    def _close_dialog(self):
        """Close the active dialog and return to browsing"""
        self.dialog_active = False
        self.dialog_type = None
        self.dialog_patch = None
        self.dialog_selection = 0
        self.power_action = None
        self.dialog_open_time = None  # Clear dialog open time
        self.ignore_next_button_release = False  # Clear ignore flag
        self.update_display()

    def _handle_copy_to_favorites_dialog(self):
        """Handle copy to favorites dialog confirmation"""
        if self.dialog_selection == 1:  # Yes selected
            if self.dialog_patch:
                success = self.scanner.copy_patch_to_favorites(self.dialog_patch['path'])
                if success:
                    print(f"Copied patch to favorites: {self.dialog_patch['name']}")
                    # Refresh categories to show new patch
                    self.categories = self.scanner.get_categories()
                    self.update_display()
                else:
                    print(f"Failed to copy patch to favorites: {self.dialog_patch['name']}")
        self._close_dialog()

    def _handle_power_menu_dialog(self):
        """Handle power menu dialog selection"""
        if self.dialog_selection == 0:  # Shutdown
            self.dialog_type = "power_confirm"
            self.power_action = "shutdown"
            self.dialog_selection = 0
            self.dialog_open_time = time.time()  # Track when confirm dialog opened
            # Set cooldown to prevent false events from button press
            self.encoder_cooldown_until = time.time() + self._get_encoder_cooldown()
            self.update_display()
        elif self.dialog_selection == 1:  # Restart
            self.dialog_type = "power_confirm"
            self.power_action = "restart"
            self.dialog_selection = 0
            self.dialog_open_time = time.time()  # Track when confirm dialog opened
            # Set cooldown to prevent false events from button press
            self.encoder_cooldown_until = time.time() + self._get_encoder_cooldown()
            self.update_display()
        else:  # Cancel (2)
            self._close_dialog()

    def _handle_power_confirm_dialog(self):
        """Handle power confirmation dialog"""
        import sys
        import subprocess
        
        # Log what was selected for debugging
        sys.stderr.write(f"[POWER] Confirm dialog: selection={self.dialog_selection}, action={self.power_action}\n")
        sys.stderr.flush()
        
        # selection == 0 = "No" selected, selection == 1 = "Yes" selected
        if self.dialog_selection == 1:  # Yes - execute action
            sys.stderr.write(f"[POWER] Executing {self.power_action}\n")
            sys.stderr.flush()
            if self.power_action == "shutdown":
                print("Shutting down system...")
                subprocess.run(['sudo', 'poweroff'])
            elif self.power_action == "restart":
                print("Restarting system...")
                subprocess.run(['sudo', 'reboot'])
        elif self.dialog_selection == 0:  # No - go back to power menu
            sys.stderr.write("[POWER] No selected, returning to power menu\n")
            sys.stderr.flush()
            self.dialog_type = "power_menu"
            self.dialog_selection = 2  # Start with Cancel selected
            self.power_action = None
            self.dialog_open_time = time.time()  # Track when returning to menu
            # Set cooldown to prevent false events from button press
            self.encoder_cooldown_until = time.time() + self._get_encoder_cooldown()
            self.update_display()
        else:
            # Invalid selection - just go back to menu
            sys.stderr.write(f"[POWER] Invalid selection {self.dialog_selection}, returning to menu\n")
            sys.stderr.flush()
            self.dialog_type = "power_menu"
            self.dialog_selection = 2
            self.power_action = None
            self.dialog_open_time = time.time()  # Track when returning to menu
            # Set cooldown to prevent false events from button press
            self.encoder_cooldown_until = time.time() + self._get_encoder_cooldown()
            self.update_display()

    def _handle_surge_error_dialog(self):
        """Handle Surge error dialog"""
        if self.dialog_patch and self.dialog_patch.get('can_restart', False):
            if self.dialog_selection == 0:  # Restart selected
                print("Attempting to restart Surge XT CLI...")
                with canvas(self.display.device) as draw:
                    draw.text((0, 20), "Restarting Surge...", fill="white", font=self.display.font_small)
                    draw.text((0, 35), "Please wait...", fill="white", font=self.display.font_small)

                success, message = self.surge_monitor.restart_surge()

                if success:
                    print(f"Surge restarted successfully: {message}")
                    try:
                        self.loader = PatchLoader()
                    except:
                        pass
                else:
                    print(f"Surge restart failed: {message}")
                    with canvas(self.display.device) as draw:
                        draw.text((0, 20), "Restart failed:", fill="white", font=self.display.font_small)
                        draw.text((0, 35), message[:20], fill="white", font=self.display.font_small)
                    time.sleep(2)
        self._close_dialog()

    def _handle_dialog_confirmation(self, press_duration):
        """
        Handle button press when dialog is active - requires bold press to select.
        
        Note: This method is only called after _should_process_button_release()
        has verified the release should be processed (not opening release, meets duration).
        """
        # Route to appropriate dialog handler
        if self.dialog_type == "copy_to_favorites":
            self._handle_copy_to_favorites_dialog()
        elif self.dialog_type == "power_menu":
            self._handle_power_menu_dialog()
        elif self.dialog_type == "power_confirm":
            self._handle_power_confirm_dialog()
        elif self.dialog_type == "surge_error":
            self._handle_surge_error_dialog()

    def _show_copy_to_favorites_dialog(self, patch):
        """Show confirmation dialog for copying patch to favorites"""
        self.dialog_active = True
        self.dialog_type = "copy_to_favorites"
        self.dialog_patch = patch
        self.dialog_selection = 0
        self.button_press_in_progress = False
        self.dialog_open_time = time.time()  # Track when dialog opened
        # Set cooldown to prevent immediate false events from button press
        self.encoder_cooldown_until = time.time() + self._get_encoder_cooldown()
        print(f"Show copy dialog for: {patch['name']}")
        self.update_display()

    def _toggle_scroll_mode(self):
        """Toggle between category and patch scroll modes"""
        self.scroll_mode = 1 - self.scroll_mode
        mode_name = "CATEGORY" if self.scroll_mode == SCROLL_MODE_CATEGORY else "PATCH"
        print(f"\n*** Mode changed to: {mode_name} ***\n")
        self.update_display()

        # If entering PATCH mode, load the current patch immediately
        if self.scroll_mode == SCROLL_MODE_PATCH:
            self.schedule_patch_load()

    def _on_button_up(self):
        """Handle button release - determine if short (favorite) or long (mode switch) press"""
        import threading
        import sys

        # Log immediately at function entry
        try:
            sys.stderr.write(f"[BUTTON] _on_button_up CALLED\n")
            sys.stderr.flush()
        except:
            pass

        current_time = time.time()
        
        # If button_press_start_time is None, this release has no valid press duration
        # This can happen when power menu opened while button was held (we reset it)
        if self.button_press_start_time is None:
            sys.stderr.write("[BUTTON] _on_button_up: button_press_start_time is None (likely power menu reset)\n")
            sys.stderr.flush()
            # Still clear button state and encoder events
            self.button_press_in_progress = False
            with self.scroll_lock:
                self.scroll_events = 0
            self.encoder_cooldown_until = current_time + self._get_encoder_cooldown()
            return  # No valid press duration, can't process
        
        press_duration = current_time - self.button_press_start_time
        
        try:
            sys.stderr.write(f"[BUTTON] _on_button_up: press_duration={press_duration:.3f}s\n")
            sys.stderr.flush()
        except:
            pass

        # Ignore if button was pressed too recently (debounce) - but only for very rapid presses
        # Allow longer presses even if recent (user might be trying different press durations)
        if press_duration < 0.1 and (current_time - self.last_button_time) < BUTTON_DEBOUNCE:
            self.button_press_start_time = None
            self.button_press_in_progress = False
            return

        self.last_button_time = current_time

        # Handle dialog confirmation first
        if self.dialog_active:
            # Apply unified button release filter
            should_process, reason = self._should_process_button_release(press_duration, current_time)
            if not should_process:
                # Ignore this release (either opening release or too short)
                # Still clear encoder events and set cooldown to prevent false triggers
                with self.scroll_lock:
                    self.scroll_events = 0
                self.encoder_cooldown_until = current_time + self._get_encoder_cooldown()
                
                # Clear button press flag after isolation period
                def clear_button_flag():
                    self.button_press_in_progress = False
                    self.button_press_start_time = None
                
                threading.Timer(BUTTON_ENCODER_ISOLATION, clear_button_flag).start()
                return  # Don't process this release
            
            # CRITICAL: Clear accumulated encoder events BEFORE handling confirmation
            # This prevents encoder events that occurred during button press from being processed
            with self.scroll_lock:
                self.scroll_events = 0
            
            # Set cooldown to prevent false events from mechanical coupling
            self.encoder_cooldown_until = current_time + self._get_encoder_cooldown()
            
            # Handle the dialog confirmation
            self._handle_dialog_confirmation(press_duration)
            
            # Clear button press flag after a brief delay (same as normal mode)
            def clear_button_flag():
                self.button_press_in_progress = False
                self.button_press_start_time = None
            
            threading.Timer(BUTTON_ENCODER_ISOLATION, clear_button_flag).start()
            return  # Don't process other button actions when dialog is active

        # Long press (>= 2s) - Show copy to favorites confirmation dialog
        if press_duration >= LONG_PRESS_MIN:
            patch = self.get_current_patch()
            if patch is not None:
                # Check if patch is already in the favorites folder
                if self.scanner.is_in_favorites_folder(patch['path']):
                    print(f"Patch already in favorites folder: {patch['name']}")
                else:
                    # Show confirmation dialog
                    self._show_copy_to_favorites_dialog(patch)
            else:
                print("No patch selected")

        # Bold press (0.5s to 2s) - Change mode (toggle between category and patch)
        elif press_duration >= BOLD_PRESS_MIN:
            self._toggle_scroll_mode()

        # Quick press (< 0.5s) - Ignore (debouncing)
        else:
            pass  # Quick press ignored

        # Clear accumulated encoder events again (in case any accumulated during button press)
        with self.scroll_lock:
            self.scroll_events = 0
        
        # Set extended cooldown period after button release to prevent false triggers
        # This handles mechanical coupling between button and encoder shaft
        self.encoder_cooldown_until = current_time + ENCODER_POST_BUTTON_COOLDOWN
        
        # Clear the flag after isolation period (encoder events during this time are DROPPED, not queued)
        def clear_button_flag():
            self.button_press_in_progress = False
            self.button_press_start_time = None  # Clear lookback window

        threading.Timer(BUTTON_ENCODER_ISOLATION, clear_button_flag).start()

    def get_current_category(self):
        """Get current category name"""
        if not self.categories:
            return "(No patches)"
        if self.category_index >= len(self.categories):
            self.category_index = 0
        return self.categories[self.category_index]

    def get_current_patches(self):
        """Get patches in current category"""
        category = self.get_current_category()
        return self.scanner.get_patches_in_category(category)

    def get_current_patch(self):
        """Get current patch info"""
        patches = self.get_current_patches()

        # Handle empty categories gracefully
        if not patches:
            return None

        # Ensure index is within bounds
        if self.patch_index >= len(patches):
            self.patch_index = 0

        return patches[self.patch_index]

    def update_display(self):
        """Update OLED display with current state"""
        # Show dialog if active
        if self.dialog_active:
            self.display.show_dialog(self.dialog_type, self.dialog_selection, self.dialog_patch, self.power_action)
            return
        
        category = self.get_current_category()
        patches = self.get_current_patches()
        patch = self.get_current_patch()

        # In CATEGORY mode, show the actually loaded patch in header, not the browsing position
        if self.scroll_mode == SCROLL_MODE_CATEGORY and self.loaded_patch_info:
            patch_name = self.loaded_patch_info['name']
            loaded_category = self.loaded_patch_info['category']
            loaded_patch_name = self.loaded_patch_info['name']
        else:
            # In PATCH mode or if nothing loaded yet, show current browsing position
            loaded_category = self.loaded_patch_info['category'] if self.loaded_patch_info else None
            loaded_patch_name = self.loaded_patch_info['name'] if self.loaded_patch_info else None
            if patch is None:
                patch_name = "(No patches)"
            else:
                patch_name = patch['name']

        self.display.show_patch(
            category=category,
            patch_name=patch_name,
            loaded_category=loaded_category,
            category_idx=self.category_index,
            category_total=len(self.categories),
            patch_idx=self.patch_index,
            patch_total=len(patches),
            scroll_mode=self.scroll_mode,
            patches_list=patches,
            categories_list=self.categories,
            loading=self.is_loading,
            scanner=self.scanner,
            loaded_patch_name=loaded_patch_name
        )

    def _navigate_forward(self):
        """Navigate forward one step (category or patch)"""
        with self.scroll_lock:
            if not self.categories:
                return
            if self.scroll_mode == SCROLL_MODE_CATEGORY:
                self.category_index = (self.category_index + 1) % len(self.categories)
                self.patch_index = 0  # Reset to first patch in new category
            else:
                patches = self.get_current_patches()
                if len(patches) > 0:
                    self.patch_index = (self.patch_index + 1) % len(patches)

    def _navigate_backward(self):
        """Navigate backward one step (category or patch)"""
        with self.scroll_lock:
            if not self.categories:
                return
            if self.scroll_mode == SCROLL_MODE_CATEGORY:
                self.category_index = (self.category_index - 1) % len(self.categories)
                self.patch_index = 0  # Reset to first patch in new category
            else:
                patches = self.get_current_patches()
                if len(patches) > 0:
                    self.patch_index = (self.patch_index - 1) % len(patches)

    def _process_scroll_events(self):
        """Process accumulated scroll events (called from main loop)"""
        with self.scroll_lock:
            events = self.scroll_events
            self.scroll_events = 0  # Reset counter

        if events == 0:
            return

        # Handle dialog mode - encoder navigates menu options
        if self.dialog_active:
            # In dialogs, process only ONE step per scroll event batch
            # This prevents double-scrolling from KY-040 encoder generating 2 events per detent
            # The unified filter should prevent most duplicates, but this is a safety measure
            direction = "CW" if events > 0 else "CCW"
            # Process only 1 step regardless of accumulated events (prevents double scrolling)
            steps = 1
            
            if self.dialog_type == "power_menu":
                # Power menu: 3 options (Shutdown, Restart, Cancel)
                max_selection = 2
                if direction == "CW":
                    self.dialog_selection = (self.dialog_selection + 1) % (max_selection + 1)
                else:
                    self.dialog_selection = (self.dialog_selection - 1) % (max_selection + 1)
            elif self.dialog_type == "power_confirm":
                # Power confirmation: 2 options (No, Yes)
                max_selection = 1
                if direction == "CW":
                    self.dialog_selection = (self.dialog_selection + 1) % (max_selection + 1)
                else:
                    self.dialog_selection = (self.dialog_selection - 1) % (max_selection + 1)
            else:  # copy_to_favorites or other 2-option dialogs
                # 2 options (No, Yes)
                if direction == "CW":
                    self.dialog_selection = (self.dialog_selection + 1) % 2
                else:
                    self.dialog_selection = (self.dialog_selection - 1) % 2
            
            self.update_display()
            return

        # Apply events as navigation steps
        direction = "CW" if events > 0 else "CCW"
        steps = abs(events)

        # Process all events
        for _ in range(steps):
            if direction == "CW":
                self._navigate_forward()
            else:
                self._navigate_backward()

        # Update display ONCE for all events
        self.update_display()

        # Schedule patch load if in patch mode
        if self.scroll_mode == SCROLL_MODE_PATCH:
            self.schedule_patch_load()

    def schedule_patch_load(self):
        """
        Schedule a patch load after debounce delay.
        Cancels any pending load and starts a new timer.
        """
        import threading

        # Cancel any pending load
        if self.pending_load_timer:
            self.pending_load_timer.cancel()
            self.is_loading = False

        # Don't show loading indicator yet - wait for debounce
        # Schedule new load after debounce time
        self.pending_load_timer = threading.Timer(LOAD_DEBOUNCE_TIME, self._execute_patch_load)
        self.pending_load_timer.start()

    def _execute_patch_load(self):
        """Actually execute the patch load (called by timer)"""
        # Show loading indicator briefly
        self.is_loading = True
        self.update_display()

        # Load the patch (with lock to ensure consistent index read)
        with self.scroll_lock:
            patch = self.get_current_patch()

        if patch is not None:
            try:
                if self.loader is None:
                    raise Exception("Patch loader not initialized")

                success = self.loader.load_patch(patch['path'])
                if success:
                    # Track what was actually loaded
                    self.loaded_patch_info = {
                        'name': patch['name'],
                        'category': patch['category']
                    }
                    # Save for restoration on reconnect
                    self.scanner.save_last_patch(patch['category'], patch['path'])
            except Exception as e:
                print(f"Error loading patch: {e}")
                # Show error briefly but don't block
                self.display.show_error_and_continue(f"Load failed: {str(e)[:30]}", timeout=3)

        # Clear loading indicator (don't block on display update)
        self.is_loading = False

    def load_current_patch_immediate(self):
        """Load the currently selected patch immediately (for initial load)"""
        patch = self.get_current_patch()
        if patch is not None and self.loader:
            success = self.loader.load_patch(patch['path'])
            if success:
                # Track what was actually loaded
                self.loaded_patch_info = {
                    'name': patch['name'],
                    'category': patch['category']
                }
                # Save for restoration on reconnect
                self.scanner.save_last_patch(patch['category'], patch['path'])
        elif patch is not None and not self.loader:
            print("Warning: Cannot load patch - loader not initialized")

    def run(self):
        """Main event loop with scroll event processing"""
        try:
            print("Patch browser running. Rotate encoder to navigate, click to change mode.\n")
            while True:
                # Process accumulated scroll events
                self._process_scroll_events()

                # Sleep briefly to batch rapid events (1ms = max 1000 updates/sec)
                time.sleep(SCROLL_PROCESSING_INTERVAL)

        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up GPIO and evdev resources"""
        print("Cleaning up...")

        # Cancel pending load timer if active
        if hasattr(self, 'pending_load_timer') and self.pending_load_timer:
            self.pending_load_timer.cancel()
            print("Cancelled pending load timer")

        # Clean up encoder (evdev or gpiozero)
        if self.use_evdev and self.encoder_handler:
            self.encoder_handler.stop()
        elif self.encoder:
            self.encoder.close()

        # Clean up button (always gpiozero)
        if self.button:
            self.button.close()

        print("Cleanup complete")

# ============================================================================
# SIGNAL HANDLERS
# ============================================================================

def signal_handler(sig, frame):
    """Handle SIGTERM/SIGINT for clean shutdown"""
    print("\nReceived shutdown signal")
    sys.exit(0)

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start the browser
    browser = PatchBrowser()
    browser.run()
