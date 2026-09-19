# Phase 9 Performance Benchmarks

## Status

**Real numbers, measured locally on 2026-09-04** — not production capacity
figures. Same caveats as the Phase 8 benchmark
(`docs/performance/whiteboard-benchmarks.md`): single developer machine,
local dev PostgreSQL, single connection, no concurrent load, no network
latency. These measurements exist for one concrete purpose: to catch sizing
mistakes before they ship, which they did (see "What this caught," below) —
not to make a production capacity claim.

## Method

Two scripts, run manually (not part of the automated suite), both deleting
all test data immediately after and verified gone:

1. **Restore snapshot size + `reconstruct_state_at` cost**: created N
   `create_stroke` operations via `bulk_create` against the real local dev
   Postgres, then measured `selectors.reconstruct_state_at(board, N)` (the
   exact function `RestoreService` calls to build a restore payload) and
   the real JSON-serialized size of the resulting snapshot.
2. **Move/resize replay cost**: measured `WhiteboardStateBuilder.replay()`
   directly (no DB — pure in-memory, matching how the Phase 8 benchmark
   isolated replay cost from DB cost) over a mix of `create_stroke`/
   `move_object`/`resize_object` operations on a growing object set.

## Results — restore snapshot size and cost

| Objects | `reconstruct_state_at` | Snapshot size |
|---:|---:|---:|
| 100 | 14.0 ms | 14.9 KB |
| 1,000 | 29.0 ms | 149.8 KB |
| 5,000 | 220.8 ms | 753.1 KB |
| 20,000 | 631.3 ms | 3,025.1 KB (≈ 3 MB) |

Real rate: **roughly 150 bytes per object** in the serialized snapshot.

## What this caught: the `MAX_RESTORE_PAYLOAD_BYTES` sizing error

The implementation plan's first-pass estimate for `MAX_RESTORE_PAYLOAD_BYTES`
was 150,000 bytes (150 KB), reasoned only from "comfortably under the
250KB WebSocket frame limit" — without having yet measured real snapshot
sizes. Running this benchmark before shipping showed that estimate was
wrong by roughly an order of magnitude: **at just 1,000 objects, a real
snapshot is already ~150 KB** — meaning the original cap would have
rejected restore for any board with objects numbering in the low
thousands, which is not an unrealistic size for an actively-used two-person
board over time.

Corrected before shipping: `MAX_RESTORE_PAYLOAD_BYTES` raised to
5,000,000 (5 MB) — matching the existing global `MAX_REQUEST_BODY_BYTES`
default already established elsewhere in this codebase (a consistent,
already-vetted number, not a new one invented for this), and comfortably
covering a full `MAX_RESTORE_OBJECTS` (20,000) snapshot's worst-case
~3 MB with real headroom to spare. This cap is a DB/JSONField-only
ceiling — it is never sent over the WebSocket wire (see
`docs/architecture/whiteboard-history.md`), so sizing it generously against
Postgres/JSONField capacity, not the WS frame limit, is the correct
frame of reference; the original reasoning conflated the two.

This is exactly the kind of mistake "measure before optimizing" (and before
capping) is meant to catch, and it's recorded here rather than silently
fixed, per this project's standing discipline of not hiding what a real
measurement actually found.

## Results — move/resize replay cost

| Operations (mixed create/move/resize) | Replay time | Final object count |
|---:|---:|---:|
| 100 | 0.3 ms | 34 |
| 1,000 | 2.2 ms | 334 |
| 5,000 | 11.1 ms | 1,667 |

Consistent with the Phase 8 pure-`create_stroke` benchmark's range (0.1 ms
at 100 ops, 7.9 ms at 5,000 ops) — move/resize add no meaningful replay
overhead. This matches the structural expectation: both operation types
touch exactly one object's own point array per operation, the same
computational shape as `create_stroke`'s `_stroke_object()` work, not a
whole-board scan.

## What this tells us

- **Move/resize replay cost is not a concern at any board size these
  numbers suggest is realistic** for this product (a private two-person
  whiteboard). No action needed; this is not a candidate for optimization.
- **Restore's cost (`reconstruct_state_at`) scales with target_sequence,
  not with the delta being restored** — restoring to sequence 100 on a
  20,000-operation board still replays up to that point, so cost is
  bounded by *how far back* you restore to, not by the board's current
  size. At the sizes measured, even the slowest case (631 ms at 20,000
  objects) is well within the bounds of an already rate-limited (10/min),
  deliberately infrequent action — not a candidate for optimization either,
  but worth re-measuring if a real deployment's monitoring
  (`docs/operations/monitoring.md`) ever shows `WhiteboardRestoreView`
  response time becoming user-visible, the same concrete-trigger philosophy
  the Phase 8 benchmark established for the DP-1/SC-1 snapshotting
  question.
- **The snapshot-size finding is the one number worth remembering**: ~150
  bytes/object is the real, measured rate this project should use for any
  future capacity reasoning involving `WhiteboardOperation` payloads or
  restore snapshots, rather than re-guessing.

## Deferred to actual deployment

Same as the Phase 8 benchmark: concurrent-user throughput, real network
latency, and production-grade database hardware/configuration are all
explicitly out of scope for what a single local process can honestly
measure.
