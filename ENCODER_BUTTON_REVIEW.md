# Encoder and Button Handling Review

## Current Issues Identified

### 1. Power Menu Exits on Button Release
**Problem**: When power menu appears (after 8-second hold), releasing the button immediately closes the menu.

**Root Cause**: 
- Power menu appears while button is still held (`button_press_start_time` was set 8 seconds ago)
- When user releases button, `press_duration` = 8+ seconds
- `_handle_dialog_confirmation()` sees `press_duration >= BOLD_PRESS_MIN` (0.5s) and processes it
- Since `dialog_selection == 2` (Cancel, the default), it calls `_close_dialog()`

**Fix Needed**: Track when dialog was opened and ignore the button release that opened it.

### 2. Inconsistent Encoder Handler Usage
**Problem**: Three different encoder handlers with different filtering logic:
- `_on_encoder_rotate()` - evdev handler (now uses unified filter ✓)
- `_on_rotate_cw()` - gpiozero handler (uses unified filter ✓)
- `_on_rotate_ccw()` - gpiozero handler (uses unified filter ✓)

**Status**: Now unified, but need to verify all paths use it consistently.

### 3. Dialog Button Handling Logic Issues
**Problem**: Button handling in dialogs has several issues:
- No distinction between "opening dialog" button release vs "selecting option" button release
- Power menu appears while button held, but any release triggers confirmation
- Quick releases (< 0.5s) are ignored, but 8-second hold release is processed

**Fix Needed**: 
- Track dialog open time
- Ignore button release that opened the dialog
- Only process subsequent button presses/releases for selection

### 4. Double Scrolling in Dialogs
**Problem**: Fixed by processing only 1 step per batch, but underlying issue may remain.

**Status**: Should be fixed, but need to verify evdev handler isn't calling callback multiple times.

### 5. Inconsistent Cooldown Handling
**Problem**: Different cooldown values and logic scattered throughout:
- Normal mode: `ENCODER_POST_BUTTON_COOLDOWN` (50ms)
- Dialogs: Hardcoded 25ms in multiple places
- Power menu timer: 200ms cooldown

**Fix Needed**: Centralize cooldown logic and make it configurable per context.

## Recommended Refactoring

### 1. Create Unified Button Handler
```python
def _should_process_button_release(self, press_duration, dialog_active):
    """Unified logic for determining if button release should be processed"""
    # Check if this is the release that opened a dialog (should be ignored)
    if dialog_active and hasattr(self, 'dialog_open_time'):
        time_since_dialog_open = time.time() - self.dialog_open_time
        if time_since_dialog_open < 0.1:  # Dialog just opened
            return False, "dialog_opening_release"
    
    # Check minimum press duration
    if dialog_active and press_duration < BOLD_PRESS_MIN:
        return False, "press_too_short"
    
    return True, "ok"
```

### 2. Track Dialog State Properly
- Add `dialog_open_time` when dialog opens
- Use this to distinguish opening release from selection release

### 3. Centralize Cooldown Logic
```python
def _get_encoder_cooldown(self):
    """Get appropriate cooldown duration based on context"""
    if self.dialog_active:
        return 0.025  # 25ms for dialogs
    return ENCODER_POST_BUTTON_COOLDOWN  # 50ms for normal mode
```

### 4. Unify Dialog Confirmation Logic
- All dialogs should use same confirmation pattern
- Track when dialog was opened
- Ignore the release that opened it
- Process subsequent releases for selection

## Files That Need Changes

1. `patch_browser_ui.py`:
   - `_on_button_up()` - Add dialog open time tracking
   - `_handle_dialog_confirmation()` - Check if this is opening release
   - `_show_copy_to_mitch_dialog()` - Set dialog_open_time
   - `_handle_power_menu_dialog()` - Set dialog_open_time when transitioning
   - Power menu timer - Set dialog_open_time when menu appears
   - Add unified button release filter method
   - Centralize cooldown logic

## Testing Checklist

- [ ] Power menu doesn't close when button is released after appearing
- [ ] Power menu selections work correctly with bold press
- [ ] No double scrolling in any dialog
- [ ] No selection jumps when button pressed in dialogs
- [ ] Encoder navigation works smoothly in all dialogs
- [ ] Normal browsing mode still works correctly
- [ ] Copy to !Mitch dialog works correctly
- [ ] Power confirm dialog works correctly

