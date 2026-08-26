# DSI terminal + keyboard shortcuts (#113)

Plan drafted 2026-08-26. Supersedes the open questions in issue #113, which was
written before the keyboard-shortcut requirement existed.

## What changed, and why it changes the whole design

Issue #113 treated "there is no physical keyboard" as the load-bearing
constraint. Requirement 3 of that issue is a layout problem (an on-screen
keyboard driving a PTY on an 800x480 panel) and the stranding risk in the "how
do I get back" section follows directly from it: strand the user at a console
with no input device and the appliance is bricked until it is power-cycled.

The request to enter via **Ctrl+Alt+T inverts that constraint**:

> If the entry path is a keyboard chord, a keyboard is physically attached
> whenever the terminal is reachable.

That is not a mitigation, it is an elimination. The unreachable-console failure
mode cannot occur, because the thing that would rescue you is the same thing
that got you in. The on-screen keyboard work drops out of scope entirely, and
the terminal becomes a build for the case it was always actually for — a
keyboard is plugged into the appliance and someone wants a shell on it.

**Design consequence:** the terminal is gated on a keyboard, by construction.
Do not add a touch affordance that opens it. A touch-reachable terminal
re-introduces the exact failure this design removes.

## Phase 0 — keyboard input at all (prerequisite, small, independently useful)

The touch browser has **no keyboard handling today**. Both `pygame.event.get()`
loops in `patch_browser/touch_browser_app.py` (lines ~476 and ~508) dispatch
mouse and touch events only; the sole `KEYDOWN` handler in the codebase is in
`patch_browser/calibration_loader.py`, a separate flow. Nothing reads modifiers.

So Phase 0 is: add a modifier-aware `KEYDOWN` branch and a shortcut table.

- Fullscreen KMSDRM pygame owns the console — there is no desktop environment to
  intercept Ctrl+Alt+T, so the chord is uncontested. Verify SDL is delivering
  key events under the KMSDRM videodriver before building on it; this is the one
  assumption in Phase 0 that could be false, and it is cheap to test.
- Shortcuts must be **inert when no keyboard is attached**, which is the default
  state. That is automatic — no events, no dispatch — but it means the feature
  is invisible to the normal player, which is correct.
- Route every shortcut through the same confirm screen the touch path uses, not
  straight to the action. A stray chord from a controller that enumerates as a
  keyboard must not restart the stack mid-set.

Ship Phase 0 on its own. It immediately gives **#112 (restart bench) a keyboard
shortcut for free**, which is worth having independently of the terminal:
recovery you can reach without finding a small button on a 5" panel.

Proposed initial table:

| Chord | Action | Confirm |
|---|---|---|
| `Ctrl+Alt+T` | Terminal (Phase 2) | yes |
| `Ctrl+Alt+R` | Restart bench (#112) | yes |

## Phase 1 — the return path, proven first

Unchanged from #113, and still correct: build the way home before the way out.

With a keyboard guaranteed present, the cheapest return path is now viable and
should be the primary one:

- The terminal session starts with a shell whose exit returns to the GUI. `exit`
  or Ctrl+D is the way back, and it is the way back every Unix user already
  knows.
- Backstop regardless: a watchdog that restores the GUI after N minutes with no
  input, so a crash *inside* the terminal cannot hold the panel.

Prove both before any terminal renders a single character.

## Phase 2 — the terminal itself

Two builds, and the audio requirement decides between them:

- **Real VT** — release the display, switch the console to the panel. Needs
  `release_display_for_shutdown()` in `patch_browser/dsi_splash.py` to be
  reversible; #113 flags this as unverified and it is still unverified. If it is
  one-way by design, this option is dead.
- **PTY rendered inside pygame** — the app keeps the display and draws a
  terminal widget. More code, but the app never tears down.

**The audio-keeps-running requirement points at the PTY.** A debug shell is
most wanted *while* something misbehaves; a terminal that kills playback to open
cannot debug playback. Whether stopping `touch-patch-browser` actually drops
audio is the Phase 0-equivalent spike from #113 and has **not been run** — the
browser is not in the audio graph, so audio probably survives, but "probably" is
the word that this project keeps getting caught by. Run it before choosing.

## Open questions

- Does SDL deliver `KEYDOWN` under the KMSDRM videodriver on this appliance?
- Is `release_display_for_shutdown()` reversible?
- Does audio survive `systemctl stop touch-patch-browser`?

## Ordering

Phase 0 → Phase 1 → spike the two open questions → Phase 2. Phase 0 alone is
worth shipping even if the terminal is never built.
