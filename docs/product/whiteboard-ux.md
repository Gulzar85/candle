# Whiteboard UX

How the whiteboard behaves as a product surface, and the explicitly documented
limitations of the Phase 7 interaction model.

## Layout

The whiteboard is a full-viewport page (no app shell):

```
┌─────────────────────────────── toolbar ────────────────────────────────┐
│ [pen][eraser][select] | [delete][undo][redo][clear] │ Board title │ … │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│                             canvas (bg-canvas)                         │
│                                                                         │
├─────────────────────────────── status bar ─────────────────────────────┤
│ cursor status                       presence · save · zoom · count     │
└─────────────────────────────────────────────────────────────────────────┘
```

- **Toolbar** — tools, selection delete, undo/redo, clear, zoom, a stroke-style
  popover (color/width/opacity, reachable at every breakpoint), a shortcut-help
  dialog, and the inline-editable board title.
- **Status bar** — live state (ready), realtime presence, save/sync, zoom % and
  stroke count.

## Tools

- **Pen (P)** — draw a freehand stroke with the current color/width/opacity.
- **Eraser (E)** — scrub strokes; erasing clips them into surviving segments and
  emits `remove` + `add` operations (single undo step).
- **Select (V)** — click a stroke to select it (dashed blue highlight). There is
  **no multi-select**; see limitations. Dragging pans the canvas.

## Selection + delete

Selecting then deleting is the only supported object mutation beyond drawing/erasing.

1. Switch to **Select** (V) or hold the select tool.
2. Click a stroke — it highlights.
3. Press **Delete/Backspace**, or click the **delete** toolbar button.

Deleting emits a single `delete_object` operation (server-authoritative), is
undoable, and syncs/replays idempotently like every other operation. When the
selected stroke disappears (deleted locally or removed by the partner), the
highlight clears automatically. Clicking empty canvas deselects; **Escape** also
clears the selection.

## Rename

The board has a human label (`Whiteboard.title`). Click the title in the toolbar
to edit inline; Enter/blur saves via `PATCH /api/whiteboards/<id>/rename/`,
Escape cancels. Renaming is a metadata change — it does **not** create an
operation and does not touch the version ledger, so it is safe to reconcile
offline.

## Destructive actions

- **Clear board** requires confirmation (browser confirm) because it is a
  `clear_canvas` operation that cannot be undone across sessions. The
  confirmation gate lives once, in `Engine.clear()` (`onConfirmClear`), so the
  toolbar button and the `Ctrl/Cmd+Shift+X` shortcut can never drift apart.

## Zoom / pan

- **Ctrl/Cmd + wheel** — zoom about the cursor.
- **Wheel / two-finger trackpad** — pan (any tool).
- **Space + drag** or **middle-drag** — pan from any tool.
- **Select tool drag** — pan.
- Toolbar zoom in/out/reset/fit-to-view.

## Documented limitations (Phase 7 non-goals)

The operation ledger supports exactly `create_stroke`, `delete_object`, and
`clear_canvas`. This deliberately limits object editing:

- **No multi-select** — selection is a single stroke (multi-object delete cannot
  be serialized without a ledger extension).
- **No moving / resizing / restyling existing objects**.
- **No text or shape objects.**

These are documented limitations, not silent half-features. Adding them is a
separate, future effort (a versioned ledger extension), out of scope for Phase 7.