# Whiteboard Engine Architecture

**Author:** Candle engineering (Senior Django Architect / Frontend lead)
**Date:** 2026-09-03
**Applies to:** the Phase 3 local whiteboard drawing engine
(`frontend/src/whiteboard/*`), the full-viewport page
(`templates/whiteboard/whiteboard.html`), the backend domain boundary
(`apps/whiteboard/*`), and how all of it is structured to admit persistence,
real-time sync, mobile, and PWA later without a rewrite.

---

## 1. Scope and non-goals (Phase 3)

Phase 3 ships a **local-only, deterministic, full-viewport whiteboard**:

- **Rendering:** native HTML `<canvas>` 2D, no Fabric.js / Konva / PixiJS.
- **Input:** Pointer Events (pen / touch / mouse), no touch-action hacks beyond
  `touch-action: none` on the stage.
- **Tools:** Pen, Eraser, Select/Pan, plus Undo, Redo, Clear, Zoom, Fit — icon
  only (Lucide), per the approved set.
- **State:** operation-log based or model (see `whiteboard-state.md`).

**Explicitly out of scope** (Phase 4+, never hacked into this phase):

- ~~No WebSockets / real-time collaboration.~~ — **Phase 5 adds it** (`realtime/*`).
- No server persistence or autosave; the board is not uploaded. — **Phase 4 added HTTP persistence**.
- No `localStorage` as a source of truth.
- No React/Vue/Angular; the page extends `base.html` and is initialized by a
  dedicated ES module entry.

The architecture is chosen so that none of the above requires an engine
rewrite — see `whiteboard-future-sync.md`.

## 2. Module map

The engine is intentionally split into **pure core** (DOM-free, unit-tested in a
node Vitest environment) and **adapters** (DOM/canvas-bound, verified via the
type check + build + smoke test):

```
frontend/src/whiteboard/
  types.ts        Shared plain-data types (Point, Stroke, Viewport, Op, …)
  geometry.ts     Pure geometry: decimate, smoothing, hit-test, bounds   [pure]
  viewport.ts     Pure world<->screen transform, zoom-at-anchor, fit     [pure]
  color.ts        Pure palette + color/width/opacity normalization       [pure]
  tools.ts        Pure pen/eraser semantics                              [pure]
  state.ts        Pure board + reversible-op history (undo/redo)         [pure]
  realtime/types.ts      Wire contract (messages, errors, states)        [pure]
  realtime/controller.ts Collaboration brain (state machine, versioning, dedupe) [pure]
  realtime/transport.ts  WebSocket transport (reconnect, heartbeat)      [pure]
  ---
  renderer.ts     Canvas rasterizer (offscreen static layer)             [DOM]
  input.ts        Pointer/Wheel -> engine commands                       [DOM]
  engine.ts       Orchestration + command surface (implements Host)      [DOM]
  index.ts        Page bootstrap / toolbar wiring (Vite entry)           [DOM]
```

**The boundary rule (enforced by design and by the Vitest config):** only the
five `[pure]` modules are consumed by tests; they never import `window`,
`document`, or a canvas. The `[DOM]` modules import from the pure ones, never
the reverse. This is what makes the drawing math deterministically testable and
re-usable by a future worker/server side.

## 3. Data flow

Pointer input → **input.ts** decides the gesture (draw / erase / pan) → calls
`Engine` (which implements the `Host` interface) → `Engine` delegates:

- **Draw** → `tools.finalizePenStroke` (decimate + smooth) → `state.commit(add)`.
- **Erase** → `tools.clipToEraser` → `state.commit(remove)` (+ `add` for the
  surviving segments).
- **Pan / zoom** → `viewport.panBy` / `viewport.zoomAt` mutate the viewport.

After any mutation `Engine` calls `renderer.render(board, viewport, size, live)`
which redraws (or reuses the cached static layer — see `whiteboard-rendering.md`).

The **backend** (`apps/whiteboard`) is deliberately thin in Phase 3: a
`Whiteboard` row (1:1 to a partnership) that exists to scope the resource and
enforce the domain boundary. The drawing itself never leaves the browser; the
model row is what a future phase will hang persisted operations onto.

## 3b. Phase 5 — remote apply & version chaining

When a partner (or a replay) delivers a committed operation, the engine applies
it through `applyRemoteOperation(envelope)`:

- It maps the server operation to a local reversible `Op`:
  `create_stroke → add`, `delete_object → remove` (by exact `object_id`, no-op
  if absent), `clear_canvas → clear`.
- It commits that `Op` to the local board and re-renders **without** calling
  `_emitServerOps`, so a remote operation is never re-sent to the server.
- It is idempotent: dedupe by `operation_id` happens in the `RealtimeController`
  (an `applied` set); board-level no-ops (delete of a missing stroke, clear on
  empty) return `false` and change nothing.

**Multi-op gesture version chain.** A single gesture can expand to several ops
(eraser: `remove` + N `add`s; clear: one; multi-stroke: several). The server
applies one op per version, so `_emitServerOps` emits a **chain** — each op's
`base_version = _serverVersion + index_within_gesture` — and then advances the
engine's local `_serverVersion` by the number of emitted ops. This fixes the
"all sub-ops stamped the same base_version" bug (only the first would commit).

The WebSocket path additionally computes its own base in `RealtimeController`
(`confirmed + inFlight`) so it stays correct even when the engine's optimistic
high-water mark lags the real server version mid-gesture. See
`conflict-strategy.md` for the full recovery model.

## 4. Determinism

The document model and the transform math are pure functions of their inputs:

- Same strokes + same viewport ⇒ same rendered output for the testable layers.
- `state.commit/undo/redo` operate on immutable arrays — no hidden mutable
  global state that a second partner (or an automated test) could desync.
- Every edit is recorded as a reversible `Op` with an order, which is exactly
  the shape a server needs for an event/op log (see `whiteboard-state.md`).

## 5. Security boundary (server side)

The whiteboard page is served only through `apps/partnerships`-scoped
authorization:

`/partnership/<uuid:public_id>/whiteboard/` → resolve `Partnership` from its
`public_id` **on the server** → `apps.whiteboard.policies.can_view_whiteboard`
(= active membership, re-exported from `apps.partnerships.policies`) → 403 for
non-members and for non-active partnerships redirect home. The URL's UUID only
*locates* the resource; it never *grants* access. Nothing client-supplied
is trusted (see `docs/architecture/shared-resource-access.md`).

## 6. Performance decisions (summary)

Summarized here; the full reasoning is in `whiteboard-rendering.md`:

- Device-pixel-ratio-correct canvas (`resize` + `setTransform` with `dpr`).
- Offscreen static layer caches committed strokes; re-baked only when
  board-version / viewport / size change.
- Live stroke preview draws on top without a full recomposite.
- Input decimation (`decimate`) keeps memory and point count small.

## 7. Related documents

- `docs/architecture/whiteboard-state.md` — document model, ops, undo/redo.
- `docs/architecture/whiteboard-rendering.md` — rasterizer and performance.
- `docs/architecture/whiteboard-future-sync.md` — path to sync/persistence/PWA.
- `docs/architecture/shared-resource-access.md` — ownership & access policy.
- `docs/architecture/websocket-protocol.md` — the WebSocket wire contract (Phase 5).
- `docs/architecture/realtime-collaboration.md` — end-to-end collaboration flow (Phase 5).
- `docs/architecture/conflict-strategy.md` — concurrency, version chains, recovery (Phase 5).
