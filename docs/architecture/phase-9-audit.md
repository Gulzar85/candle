# Phase 9 Audit — Product Expansion

Date: 2026-09-04. Written after implementation, documenting what was found
during research (before any code was written) and what changed. Follows this
project's established convention of auditing real code state rather than
trusting prior docs blindly.

## Current capabilities (confirmed by direct code reading, before this phase)

- **Operation model**: `WhiteboardOperation` is an append-only, immutable,
  server-sequenced ledger (`apps/whiteboard/models.py`). Three operation
  types existed before this phase: `create_stroke`, `delete_object`,
  `clear_canvas`. Idempotency via client-supplied `operation_id`; ordering
  via server-assigned `sequence`.
- **State reconstruction**: `WhiteboardStateBuilder.replay(operations,
  start=None)` (`apps/whiteboard/state_reconstruction.py`) already accepted
  an optional starting state — a designed seam, unused until this phase.
- **Object model**: stroke-only. No shapes, no images, no move/resize/
  rotate, no multi-select, no grouping (`frontend/src/whiteboard/engine.ts`,
  `types.ts`). The select tool could only single-select and delete.
- **Board model**: `Whiteboard.partnership` is a `OneToOneField` — exactly
  one board per partnership, enforced structurally, with no list/create/
  duplicate endpoints anywhere.
- **Partnership model**: two-person, enforced at the DB level (a partial
  unique index limiting one active membership per user, plus a Postgres
  trigger limiting a partnership to two active members —
  `apps/partnerships/migrations/0002_parternship_db_constraints.py`).
- **Offline sync**: durable IndexedDB queue feeding an always-anchored,
  server-authoritative flush — no client-side conflict policy
  (`sync-engine.ts` submits every op against the newest server version;
  permanent rejections become save errors). Note: the pre-Phase-9
  `conflict-resolver.ts`-based quarantine policy described in earlier audits
  was removed during Phase 9 after a user-reported bug (conflict panel
  surfacing during routine writing); the current policy is the
  no-quarantine flush. A closed `OperationType` union whose `default` case
  safely rejects unknown types.
- **WebSocket protocol**: versioned, generic over `operation_type`/
  `payload` at the transport layer — no vocabulary hardcoding at that layer.
  Hard limits: `MAX_MESSAGE_BYTES=250,000`/frame,
  `MAX_OPERATION_PAYLOAD_BYTES=100,000`/op (`apps/whiteboard/realtime.py`).

## Existing limitations (confirmed, still true after this phase)

- **DP-1** (Phase 8 audit): no snapshot/compaction mechanism; state
  reconstruction replays the full operation history every time.
  **Still open, unchanged by this phase.** This phase's `RESTORE_VERSION`
  reuses the `replay(start=...)` seam DP-1 anticipated, but for a distinct,
  product-driven reason (letting a user view/restore history), not as the
  performance optimization DP-1 describes. The two motivations happen to
  share a mechanism; neither closes the other's question.
- **SC-1** (Phase 8 audit): no cap on total objects per board.
  **Still open, unchanged by this phase.** Import gets its own bound
  (`MAX_IMPORT_OBJECTS`) to prevent import specifically from being a novel
  amplification vector, but this does not resolve the general question.
- No attachments/images, no multi-board support, no sharing/access model
  beyond the two-person partnership. All confirmed unimplemented — this
  phase deliberately did not add them (see "Recommended feature order").

## Product opportunities identified, and what was built

The original 46-section spec covered a much larger surface than was
implemented. Given four explicit scope decisions (recorded in
`docs/product/phase-9-features.md`), this phase built:

1. Whiteboard version history with a human-readable UI, folded together
   with "activity feed" (in this codebase they would be near-duplicate
   presentational views over the same ledger).
2. Restore to an earlier version, as a new forward-moving,
   `RESTORE_VERSION` operation — never deletes or rewrites history.
3. Single-object select/move/resize (no multi-select, no grouping).
4. PNG export, JSON export, and validated JSON import.

## Architectural constraints respected

- **New operation types are appended, never renamed** (`enums.py`'s own
  documented rule) — `MOVE_OBJECT`, `RESIZE_OBJECT`, `RESTORE_VERSION` were
  added after `CLEAR_CANVAS`, values never reused.
- **No second source of truth**: move/resize/restore all persist as
  ordinary `WhiteboardOperation` rows through the unchanged
  `WhiteboardOperationService.submit_batch` — no new tables for board
  content, no parallel write path.
- **`start=` copy is shallow** (`dict(start.objects)` — the object dicts
  themselves are shared references). `MOVE_OBJECT`/`RESIZE_OBJECT` are the
  first operation types that patch a subset of an existing object's
  fields; both always rebind a fresh dict rather than mutating in place,
  specifically to preserve this contract. Covered by a dedicated regression
  test (`tests/whiteboard/test_replay.py::test_move_does_not_mutate_start_state`,
  `test_resize_does_not_mutate_start_state`).
- **Restore payload never crosses the WebSocket wire.** A real
  first-attempt cap (`MAX_RESTORE_PAYLOAD_BYTES = 150,000`) was found to be
  wrong after actually measuring snapshot sizes (see "Performance
  implications" below) — real snapshots run ~150 bytes/object, so 150KB
  would have rejected any board past ~1,000 objects. Corrected to 5,000,000
  (5MB) before shipping, based on the measured data, not the original
  guess.

## Security implications

See `docs/security/phase-9-security-review.md` for the full review. Summary:
no new authorization path was introduced — every new endpoint (`restore`,
`history`, `import`) resolves the whiteboard via the exact same
`_get_whiteboard_and_partnership` helper every existing endpoint uses.
Object ids in import are always regenerated server-side, never trusted from
the client. Import's `MAX_IMPORT_OBJECTS` bound is a novel-vector guard, not
a fix for the still-open SC-1.

## Performance implications

A real local benchmark (`docs/performance/phase-9-benchmarks.md`) measured
`reconstruct_state_at` cost and actual restore-snapshot JSON size at 100,
1,000, 5,000, and 20,000 objects — this is what caught the
`MAX_RESTORE_PAYLOAD_BYTES` sizing error above before it shipped. Move/
resize add negligible replay cost (simple point-array transforms, same
order of magnitude as the existing `create_stroke` replay cost measured in
the Phase 8 benchmark).

## Storage implications

Move/resize/restore add no new database tables — everything is a
`WhiteboardOperation` row with a JSONField payload, same as every existing
operation type. A large board's history now also durably contains its own
past states via `RESTORE_VERSION` payloads (bounded by
`MAX_RESTORE_PAYLOAD_BYTES`/`MAX_RESTORE_OBJECTS`), which is a real,
bounded storage cost increase for boards that use restore heavily — not
unbounded, and consistent with the append-only ledger's existing growth
characteristics.

## Recommended feature order (for a future pass, not committed to)

Deferred this phase, in the order a future pass might reasonably reconsider
them, from least to most architecturally disruptive:

1. **Comments/annotations** — a separate model from the operation ledger
   (discussion metadata, not canvas content), moderate effort, no schema
   changes to `Whiteboard`/`WhiteboardOperation` needed.
2. **Attachments/images** — needs a new multipart upload path (the API is
   JSON-only today), a storage-backend decision (production is still local
   `FileSystemStorage`), and Pillow-based re-encoding security following
   `apps/accounts/avatars.py`'s precedent.
3. **Multi-board support** (duplication/organization/templates) — requires
   lifting `Whiteboard.partnership` from `OneToOneField` to a real
   multi-board model: new URLs, a new dashboard UI, and touching every
   authorization check point. A genuine architecture change, not a
   feature bolt-on — needs its own explicit product decision, not an
   assumption.
4. **Sharing/access beyond two people** — deliberately not recommended
   without a demonstrated product need; the two-person model is
   structurally, deliberately load-bearing (see
   `docs/architecture/partnerships.md`, `docs/architecture/
   shared-resource-access.md`) and the product's own UI copy says "made
   for two."
