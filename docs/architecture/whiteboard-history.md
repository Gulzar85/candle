# Whiteboard History & Restore

Phase 9. Documents the history feature's architecture: how it reads the
existing operation ledger, what `RESTORE_VERSION` does, and the rules that
keep restore from becoming a second source of truth.

## Operations remain the only source of truth

`WhiteboardOperation` (`apps/whiteboard/models.py`) is unchanged by this
feature — no new tables were added for history or restore. Both the history
panel and the restore mechanism are read/write operations against the
existing ledger:

- **History** is a pure read-side presentation layer
  (`apps/whiteboard/history.py`) over `WhiteboardOperation` rows already
  exposed by `selectors.operations_for_whiteboard`. It introduces no new
  persisted state.
- **Restore** is a new, ordinary operation type, `RESTORE_VERSION`,
  persisted exactly like `create_stroke`/`delete_object`/`clear_canvas`
  through the unchanged `WhiteboardOperationService.submit_batch`.

## Snapshots: used, but not for the reason DP-1 considered

The Phase 8 audit's finding **DP-1** considered adding a snapshot table as a
*performance* optimization for replay cost, and explicitly deferred it
pending an observed trigger (fetch-query latency growth) that has not
occurred. This feature does not reopen that decision — `MAX_RETAINED_OPERATIONS`
is still `0` (unlimited), and no periodic/automatic snapshotting exists.

What restore *does* do is reuse the `WhiteboardStateBuilder.replay(...,
start=...)` seam DP-1's own writeup anticipated ("adding a snapshot table
later doesn't require rewriting the replay logic") — but for a distinct,
product-driven reason: a user asked to see and revert to an earlier state.
`selectors.reconstruct_state_at(whiteboard, target_sequence)` replays
`sequence <= target_sequence` and is correct regardless of concurrent
activity, since operation rows are immutable — "state as of sequence N"
never changes once N has been reached, no matter what happens to the board
afterward.

## Version numbers and sequence

`target_sequence` in a `RESTORE_VERSION` payload refers to an ordinary
`WhiteboardOperation.sequence` value — the same monotonic per-board counter
every operation already has. No new numbering scheme was introduced.

## `RESTORE_VERSION` payload shape

```json
{"target_sequence": 137, "objects": [{"object_type": "stroke", "object_id": "...", "points": [...], "color": "#...", "width": 3, "opacity": 1, "creator_id": null}, ...]}
```

This is the **one deliberate exception** to "the client builds the
payload" that every other operation type follows. The client only ever
supplies `target_sequence` (via `POST /api/whiteboards/<id>/restore/`); the
server computes and embeds the full z-ordered snapshot
(`apps/whiteboard/restore.py::RestoreService.restore`). This keeps restore
correct under concurrency (the snapshot reflects the *actual* history at
submission time, not a client-side guess) and avoids trusting a client to
faithfully reconstruct history it might not have cached.

## Reconstruction: how `apply()` handles `RESTORE_VERSION`

```python
elif op_type == WhiteboardOperationType.RESTORE_VERSION:
    state.objects = {o["object_id"]: dict(o) for o in payload["objects"]}
    state.order = [o["object_id"] for o in payload["objects"]]
```

A logical, in-place reset — like `CLEAR_CANVAS`, never a per-row delete of
history. Every operation before and after the restore remains a real,
queryable row.

## The size problem, and how it's resolved

A restore snapshot for a board with hundreds or thousands of objects can be
large — real measurements
(`docs/performance/phase-9-benchmarks.md`) show roughly 150 bytes/object,
so a 5,000-object board's snapshot is ~750KB. This is far larger than
`realtime.MAX_MESSAGE_BYTES` (250KB, the hard WebSocket frame ceiling) and
would regularly exceed even a naively-chosen "generous" limit — an early
150KB cap on `MAX_RESTORE_PAYLOAD_BYTES` was found to be wrong this exact
way during implementation, before it shipped, and was corrected to 5MB
based on the real measured data (see the phase-9 audit's "Performance
implications" section for the full account of that correction).

The resolution, adopted as the canonical rule everywhere this matters:

- The full `objects` array is **persisted to Postgres and read via the ORM
  only.** It is never returned by the history/operations-list HTTP
  endpoints, and never put on the WebSocket wire.
- The WebSocket broadcast for a completed restore
  (`apps/whiteboard/realtime_signals.py::notify_restore`) sends a
  **lightweight** notification — `target_sequence` and metadata only, no
  `objects`.
- **Every client that observes a `RESTORE_VERSION` operation — live
  broadcast, or `sync.ops` catch-up on reconnect — always resolves it with
  one full state refetch** (`GET /api/whiteboards/<id>/`), never by trying
  to apply the (deliberately incomplete) payload locally. Implemented once,
  centrally, in `frontend/src/whiteboard/index.ts`'s `onApplyRemote`
  callback — both the live-broadcast path and the reconnect-catch-up path
  in `RealtimeController` funnel through that one callback, so the check
  only needs to exist in one place.

This sidesteps the WebSocket size limit entirely and reuses the
already-correct, already-tested full-load code path for both the
initiating client and any live partner.

## Undo

`Engine.loadServerState()` already resets local undo/redo history on every
full load, exactly like an ordinary page load. Since every restore
observer does a full refetch (above), a restore naturally clears local undo
the same way a fresh page load already does — no new undo-inversion code
was needed. "Undoing" a restore is done the same way as correcting any
mistake: revert again, to the entry just before it. This is consistent with
restore being forward-moving and append-only — there is no special
backward-only "unrestore."

## Restore requires connectivity — a real, stated boundary

Restore fundamentally cannot be computed client-side while offline: it
requires replaying the full server-side ledger up to `target_sequence`,
which an offline client does not have cached (IndexedDB's `whiteboards`
store caches only the *current* flattened strokes, not history). **The
history panel disables "Revert" while offline** rather than queuing the
request — this is an inherent scope boundary of the feature, not a gap.

## Offline client reconnecting after a restore happened while away

On reconnect, `sync.ops` catch-up recognizes a `RESTORE_VERSION` op in the
gap (its stripped payload is enough to identify the type) and triggers one
full refetch instead of incrementally replaying the batch — the other ops
in that batch are already reflected in the refetched state, so replaying
them individually would be redundant. The client's own locally-queued
pending ops (drawn while offline) flush through the regular `SyncEngine`
path unchanged:

- `create_stroke` lands on top of the restored board — correct, since
  restore never deletes ledger rows.
- `move_object`/`resize_object`/`delete_object` targeting an object the
  restore removed confirm as server-side no-ops (the same idempotency
  convention as `delete_object` on a missing target) — zero
  restore-specific frontend sync code was needed for this case.

## Human-readable history entries

`apps/whiteboard/history.py::build_history` maps each operation to a short
sentence, framed relative to the viewer ("You" / "Partner" / "Someone" for
a deleted account — never an actual display name, matching this product's
existing presence-avatar framing):

| `operation_type` | Text |
|---|---|
| `create_stroke` | "{actor} added a drawing." |
| `delete_object` | "{actor} removed a drawing." |
| `clear_canvas` | "{actor} cleared the board." |
| `move_object` | "{actor} moved a drawing." |
| `resize_object` | "{actor} resized a drawing." |
| `restore_version` | "{actor} restored an earlier version." |

**Known, accepted imprecision**: the ledger cannot distinguish a
whole-stroke selection-delete from an eraser-driven clip-delete — both
produce `delete_object` rows. The copy stays generic rather than inventing
a distinction the data doesn't support.

**Collapsing**: consecutive same-actor, same-type operations with nothing
from the other actor in between collapse into one entry (e.g. "Partner
erased part of the drawing (×4)"), so a single eraser swipe — which can
emit many `delete_object`+`create_stroke` pairs — doesn't flood the feed.
`clear_canvas` and `restore_version` are never collapsed; they're rare,
significant events. **Known, accepted limitation**: at a pagination
boundary, a single real run of activity can be split across two pages and
shown as two smaller entries instead of one — a cosmetic imprecision, not a
correctness issue, and not worth the complexity of boundary-run-merging
for a first version of this feature.

## Retention

History display is bounded per-request (`limit`, capped at 200 —
`WhiteboardHistoryView`), but nothing about the *underlying* ledger's
retention changed — `MAX_RETAINED_OPERATIONS` is still `0` (unlimited), per
DP-1's still-open status above.

## Activity feed: folded into history, not built separately

The original Phase 9 spec listed "activity feed" as a separate (Tier 2)
feature. In this codebase, a history panel scrolled to the top *is* an
activity feed — both are humanized, newest-first views over the same
`WhiteboardOperation` rows. Building two would be pure duplication, so this
phase builds one. If a future phase wants transient "Partner just cleared
the board" toasts (as opposed to a panel you open), that's a small additive
layer on the existing `operation.committed` WebSocket stream reusing the
same humanization table — it doesn't conflict with this design.
