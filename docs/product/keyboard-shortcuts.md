# Keyboard Shortcuts

The whiteboard has a small, memorable set of shortcuts. All bindings are
implemented in `frontend/src/whiteboard/engine.ts` (`handleGlobalKeydown`) and
are skipped when focus is in an editable field (input/textarea/contenteditable).

| Shortcut | Action |
| --- | --- |
| `P` | Select the pen tool |
| `E` | Select the eraser tool |
| `V` | Select the select/pan tool |
| `Delete` / `Backspace` | Delete the selected stroke |
| `Escape` | Clear the selection |
| `Ctrl`/`Cmd + Z` | Undo |
| `Ctrl`/`Cmd + Y` or `Ctrl`/`Cmd + Shift + Z` | Redo |
| `Ctrl`/`Cmd + Shift + X` | Clear the board (confirm dialog) |
| `Space` / `Middle-drag` / `Ctrl+drag` | Pan (from any tool) |
| `Ctrl`/`Cmd + wheel` | Zoom about the cursor |
| `?` | Open the keyboard-shortcut help dialog |

On touch devices, pinch (two fingers) zooms and pans; the shortcut-help button
is hidden below the `sm` breakpoint since touch devices have no keyboard.

Duplicates of the toolbar's core actions are intentional: the engine keeps a
single command surface (`setTool`, `undo`, `redo`, `clear`, `deleteSelection`),
and the keyboard just invokes those same methods, so the two can never drift.