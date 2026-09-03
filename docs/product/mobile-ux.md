# Mobile UX

Candle is designed to be fully usable on a phone. This document records the
mobile-specific behaviour and the constraints that keep it working.

## App shell (non-whiteboard pages)

- **Header** is offset by `env(safe-area-inset-top)` so content clears the notch
  / Dynamic Island.
- **Bottom tab bar** is offset by `env(safe-area-inset-bottom)` so the last row
  clears the home indicator.
- **Hamburger → slide-in drawer**: focus moves into the drawer on open, returns
  to the trigger on close, closes on overlay click and Escape. It is a
  `role="dialog"` with `aria-modal`.
- **Home page** feature cards stack in a single column (they were previously
  `hidden` on mobile — no content below the hero).

## Whiteboard on mobile

- The **stroke style** (color / width / opacity) lives in one popover
  (`#wb-style-popover`, opened from the palette icon), reachable at every
  breakpoint — it replaced the old always-hidden-below-`sm` inline swatch row.
- The **canvas** uses `touch-action: none` and pointer events, so single-stroke
  drawing works without gesture interference.
- **Pinch-zoom / two-finger pan** (`InputController.beginPinch` in `input.ts`)
  and top/bottom safe-area handling on the whiteboard toolbar and status bar
  (`env(safe-area-inset-*)` in `templates/whiteboard/whiteboard.html`) are
  implemented — see the input note below.

## Input model (why this stays safe)

`input.ts` owns the pointer gesture state machine. Two rules must never break:

1. **Single-pointer draw semantics are preserved.** Pen/eraser strokes always
   track exactly one active pointer; an added second finger must not corrupt an
   in-progress stroke.
2. **Select-click vs. pan.** In select tool, a small press+release is a click
   (select); travel past a tiny tolerance turns it into a pan.

New multi-touch code must sit alongside, not replace, these rules.

## Safe-area checklist

- `viewport-fit=cover` is set in `base.html` `<meta name="viewport">`.
- Header/tab bar apply the `env(safe-area-*)` insets (`.dark`-independent).
- Tap targets are ≥ 44px (buttons use `min-h-[40px]`/`h-11` where interactive).