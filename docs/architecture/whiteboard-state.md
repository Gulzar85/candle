# Whiteboard State & Operation Model

**Author:** Candle engineering
**Date:** 2026-09-03
**Applies to:** `frontend/src/whiteboard/state.ts`, `types.ts`, `tools.ts` —
the deterministic, pure document model and its undo/redo.

---

## 1. The document model

A board is an **ordered list of strokes**:

```ts
interface Stroke { id: string; points: Point[]; style: StrokeStyle; creatorId?: string }
```

- `points` are **world-space**; the viewport transform maps them to screen.
- `style` is a resolved color / width / opacity.
- `id` is unique per browser session (`<prefix>-<n>-<timestamp>`).
- `creatorId` tags who drew it, so presence/authorship survives when we later
  reconcile multiple clients.

The board is **immutable**: every computed value is a new object; no in-place
mutation. This makes undo/redo trivial, makes rendering cacheable by a monotonic
`version`, and removes a whole class of shared-state races that would otherwise
bite real-time sync.

## 2. Operations (the unit of history and future sync)

All edits are expressed as **reversible operations**:

```ts
type Op =
  | { type: "add";    strokes: Stroke[] }   // engine-erased segments also use add
  | { type: "remove"; strokes: Stroke[] }   // the concrete strokes removed
  | { type: "clear";  strokes: Stroke[] };  // all strokes present at clear time
```

Each op carries **the strokes it affected**, so it can be inverted exactly
without consulting any other state:

```ts
invert(add)    -> remove(strokes)
invert(remove) -> add(strokes)
invert(clear)  -> add(strokes)
```

### Why operations and not snapshots?

- **Bounded memory**: undo history is a list of small ops, not N full-board
  copies.
- **Deterministic**: `apply(invert(op))` and `apply(op)` are pure functions.
- **Future-ready**: an ordered, reversible op stream is precisely an event/op
  log — the natural substrate for server persistence and real-time sync
  (see `whiteboard-future-sync.md`). A snapshot model would require a rewrite to
  sync; an op model does not.

## 3. History semantics

The board tracks two stacks:

- `past` — applied ops in commit order (the ledger / undo stack).
- `future` — undone ops available to redo (most-recently-undone last).

```
commit(op) : strokes += op ; past.push(op) ; future = []
undo()     : strokes += invert(last past) ; future.push(last past)
redo()     : strokes += last future ; past.push(last future)
```

**Linear-history rule:** any new commit clears `future`, so you can never redo
across a point where the document diverged — standard and predictable.

Every mutation bumps `version`, which is the renderer's cache-invalidation key
(see `whiteboard-rendering.md`).

## 4. Pure tool semantics

Tools produce ops from raw pointer samples without touching the DOM:

- **Pen** (`finalizePenStroke`): decimate samples by a minimum distance, then
  smooth with Chaikin corner-cutting, then `add`.
- **Eraser** (`clipToEraser`): clip points out of strokes that pass within the
  eraser radius; produce a `remove` (originals) plus an `add` (surviving
  segments as new strokes). Two ops in one gesture — two undo steps restore it.

Putting this logic in pure functions means the exact math the UI applies today is
the exact math a future server applies when validating/reconciling client ops.

## 5. Design notes / invariants

- Undo/redo return the same board (no-op) when their stack is empty — callers
  can compare by reference to detect "nothing to do".
- A `remove` op created directly (future features) must carry the removed
  strokes; we never reconstruct them from ids.
- Stroke identity (`id`) is the only thing a later sync layer needs to make
  `add`/`remove` idempotent across clients.

## 6. Related documents

- `docs/architecture/whiteboard-engine.md` — module map and data flow.
- `docs/architecture/whiteboard-rendering.md` — how `version` drives redraws.
- `docs/architecture/whiteboard-future-sync.md` — the op-log as a sync substrate.
