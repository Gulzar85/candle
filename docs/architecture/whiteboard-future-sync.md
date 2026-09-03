# Whiteboard — Path to Persistence, Sync, Mobile, and PWA

**Author:** Candle engineering
**Date:** 2026-09-03
**Applies to:** how the Phase 3 architecture is deliberately shaped so the
future phases (persistence, real-time collaboration, mobile orientation, offline
PWA) land cleanly — without a rewrite of the engine.

---

## 1. Principle: separate the document from the transport

Phase 3 keeps the board entirely in the browser. The thing that makes that safe
to grow later is that the engine is built as **a pure, op-log document model**
plus **thin adapters**. The document (strokes, ops, viewport) has no idea where it
came from or where it goes — it can be rendered by the canvas adapter, sent to a
server, stored, or restored, with the same code.

Nothing in Phase 3 stores the drawing on the server; the `Whiteboard` model is a
scoping/authority placeholder. Each future capability below maps to a boundary we
already drew.

## 2. Persistence (Phase 4)

The natural substrate already exists: the **reversible op stream** in
`state.ts` (`past`). Persisting a board is persisting its ops:

- Persist each committed `Op` (or a batch) as an append-only ledger on the
  `Whiteboard` model, keyed to the partnership. Replay `commit` over an empty
  board to reconstruct the document deterministically.
- Load = fetch ops, replay once; rendering is unchanged because the renderer
  only consumes the resulting stroke list + `version`.
- Because ops are orderable and idempotent-by-stroke-id, the same code used for
  local history is the storage format. No serializer rewrite.

Backend shape: add rows like `WhiteboardOp` (`whiteboard`, `seq`, `op_type`,
`data`, `created_at`) with a `select_for_update()`-guarded append point, mirroring
the existing partnership concurrency pattern.

## 3. Real-time sync (Phase 5)

Two partners each run the same pure engine. Sync = sharing ops, not sharing
canvases:

- Each client broadcasts its committed ops (through the server via a future
  WebSocket / channel, authorized by the existing `can_access_whiteboard`
  predicate).
- A receiving client applies remote ops through `commit` in op order; conflicts
  are resolved by ordering (sequence numbers / timestamps) and, because every op
  is idempotent by stroke id and the model has no server-authoritative snapshot
  prerequisite, reconciles without a LWW-snapshot rewrite.
- The `version` field makes "what changed since I last rendered" trivial, and the
  static layer re-bake on version change is already built.

The security boundary is carried forward intact: only active members can open the
socket, and the server never trusts client ids — exactly the Phase 2/3 policy.

## 4. Mobile orientation

- Pointer Events already unify mouse / touch / pen on a `touch-action: none`
  stage; no separate touch code path was written.
- A single-contact drawing model + wheel/pan handled through one gesture state
  machine leaves room to add two-finger pinch zoom without changing the document
  model.
- Viewport math is device-agnostic (pure world↔screen). The full-viewport layout
  and `min-h-0` scrolling isolation were chosen to map directly to a mobile
  full-screen drawing surface.

## 5. PWA / offline

- The board's local-only, deterministic model means "offline = exactly Phase 3
  behavior." Nothing depends on the network.
- A service worker can cache the page/assets; a future phase can flush the local
  op log (a bounded, replayable ledger) to the server once connected — a natural
  consumer of the same op substrate.
- Identity (`creatorId`) is already attached to strokes, so offline-authored
  strokes reconcile with remote ones by id.

## 6. Explicit non-goals that protect the design

We intentionally did **not** add, in Phase 3: server persistence, autosave,
WebSockets, or localStorage-as-source-of-truth. Adding any of them pre-maturely
would have welded the document model to a transport or a store. The op-log
abstraction is the seam every future phase plugs into.

## 7. Related documents

- `docs/architecture/whiteboard-engine.md` — module map, data flow, security.
- `docs/architecture/whiteboard-state.md` — the op/document model.
- `docs/architecture/whiteboard-rendering.md` — rendering & performance.
- `docs/architecture/shared-resource-access.md` — ownership & access policy.
