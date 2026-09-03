# Whiteboard Rendering Strategy

**Author:** Candle engineering
**Date:** 2026-09-03
**Applies to:** `frontend/src/whiteboard/renderer.ts`, `viewport.ts`, and the
canvas surface in `templates/whiteboard/whiteboard.html`.

---

## 1. Canvas setup & DPI correctness

The stage and canvas fill the viewport (`position: absolute; inset: 0` inside a
`flex-1 min-h-0` main). The renderer keeps the backing store in sync with the
element's CSS size at `devicePixelRatio`:

```ts
resize(size) {
  dpr = max(1, devicePixelRatio)
  canvas.width  = size.w * dpr
  canvas.height = size.h * dpr
}
```

The ctx transform combines DPR, the viewport zoom, and the world→screen offset in
one `setTransform`, so strokes are drawn directly in **world coordinates** and
stay crisp on high-DPI (Retina) displays:

```ts
setTransform(dpr*zoom, 0, 0, dpr*zoom, dpr*(size.w/2 - cx*zoom), dpr*(size.h/2 - cy*zoom))
```

## 2. The offscreen static layer

Rendering every stroke on every pointer-move would be wasteful. Instead the
renderer keeps an **offscreen canvas** holding all committed strokes, baked at
the current viewport/size:

- `render()` compares `{ board.version, size, viewport }` against the last baked
  state (`sameState`).
- If unchanged → blit the static layer and draw only the `liveStrokes` (the
  stroke in progress, and the eraser preview) on top.
- If changed (a commit, a pan/zoom, or a resize) → re-bake the static layer once.

Result: during an active pen stroke we redraw only the single live polyline per
move; committed content is a single `drawImage`. This keeps drawing smooth even
with many strokes.

## 3. Stroke rasterization

A stroke is a polyline in world space:

```ts
strokeStyle = color; globalAlpha = style.opacity; lineWidth = style.width
lineCap = "round"; lineJoin = "round"
moveTo(p0); for (...) lineTo(pn); stroke()
```

Round caps/joins give the familiar soft freehand look. Points were already
decimated + smoothed upstream (`tools.ts`), so the number of `lineTo` calls is
small; opacity is reset after each stroke.

## 4. The viewport transform

`viewport.ts` is pure math (unit tested):

- Center-based viewport `{ cx, cy, zoom }` — the world point at screen center.
- `screenToWorld` / `worldToScreen` map between CSS px and world units.
- `zoomAt(anchor)` keeps the world point under the cursor stationary
  (wheel/gesture zoom-at-cursor).
- `panBy` shifts by a screen delta (select-drag, space-drag, or wheel scroll).
- `fitToShow` frames a bounding box within the padded viewport; a degenerate
  (single-point) box is framed at the default zoom so fit-to-view never blanks
  the canvas.

Because the transform is pure, zoom/pan correctness is verified by unit tests
independently of the rasterizer.

## 5. Performance characteristics

| Path | Work per frame |
|------|----------------|
| Pointer-move while drawing | 1 live polyline + 1 static `drawImage` |
| Pan / zoom | re-bake static layer (single pass over strokes) |
| Commit (stroke done) | re-bake static layer |
| Empty/static board | nothing redrawn (state matches) |

Bounding has a ceiling in practice: a private two-person board has a modest
stroke count, and each stroke is decimated, so a re-bake is a cheap single pass.
This is deliberately **layered, not multi-layer** — one static canvas plus the
live overlay is the simplest design that hits the target, and avoids the
compositing/state complexity of a full tile or layer system that Phase 4 sync
would not need.

## 6. Related documents

- `docs/architecture/whiteboard-engine.md` — module map, data flow.
- `docs/architecture/whiteboard-state.md` — how `version` triggers re-bakes.
- `docs/architecture/whiteboard-future-sync.md` — rendering implications of sync.
