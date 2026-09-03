# IndexedDB Schema

**Date:** 2026-09-03
**Scope:** Phase 6 — the on-device schema owned by
`WhiteboardLocalStore` (`frontend/src/whiteboard/local-store.ts`). All IndexedDB
access flows through this one module; nothing else touches the database.

---

## Database

A single database per installation, opened and upgraded by `idb.ts` (`openDB`),
with object stores matching the schema table below. Rows are never duplicated
across accounts because every record carries an `owner_key`; reads filter by the
owner partition (the account `public_id` set on `WhiteboardLocalStore`).

## Object stores

### `whiteboards` (keyPath `whiteboard_id`)

Metadata + cached logical state for one board.

| index | keyPath | purpose |
|-------|---------|---------|
| `by_owner` | `owner_key` | owner partition listing |
| `by_updated` | `updated_at` | recency ordering for eviction |

Row shape (`LocalWhiteboard`):

```
whiteboard_id, owner_key, server_version, last_sequence,
strokes: Stroke[] (capped), cached_at, updated_at
```

The `strokes` cache is **logical** (replayable), **not** a screenshot — the app
uses the operation model for offline restore.

### `operations` (keyPath `operation_id`)

The durable local operation queue. This is the crash-safety store.

| index | keyPath | purpose |
|-------|---------|---------|
| `by_whiteboard` | `["whiteboard_id","status"]` | scoped status queries |
| `by_status` | `status` | status scanning (e.g. recovery) |
| `by_sequence` | `client_sequence` | ordered submission |
| `by_owner` | `owner_key` | owner partition listing |

Row shape (`LocalOperation`): identity, `base_version`, `client_sequence`,
status, `retry_count`, `last_error`, `last_attempt_at`, server ack fields, timestamps.

Status lifecycle: `PENDING → SUBMITTING → CONFIRMED` (compacted), or
`PENDING → CONFLICT` (quarantined) / `FAILED` (retry exhausted). See `sync-engine.md`.

### `meta` (keyPath `key`)

Small key/value globals.

| key | value |
|-----|-------|
| `client_id` | persistent installation UUID (see `client-id.ts`) |

### `conflicts` (keyPath `operation_id`)

Quarantined operations that could not be reconciled automatically.

| index | keyPath | purpose |
|-------|---------|---------|
| `by_whiteboard` | `["whiteboard_id","status"]` | per-board conflict listing |
| `by_owner` | `owner_key` | owner partition |

Row shape (`LocalConflictRecord`): embeds the full `LocalOperation`, plus
`reason`, `detected_at`, `status`, `updated_at`. Resolving a conflict returns the
embedded op to `PENDING` (fresh `retry_count`) for re-submission; discarding
deletes the record.

## Partitioning (multi-account safety)

Every store has `by_owner`. All list/count reads use `idbGetAllFromIndex(..., "by_owner", ownerKey)`
then filter. Logout (`clearOwnerPartition`) deletes only the caller's partition.
`hasUnsyncedWork` blocks logout while `PENDING`/`SUBMITTING` rows exist, so a user
cannot sign out and strand unsynced changes unwittingly.

## Limits & eviction

| Rule | Value | Enforcement |
|------|-------|-------------|
| Pending operations per owner | `MAX_PENDING_OPERATIONS = 5_000` | `saveOperation` throws `LocalStorageError` beyond this (avoids unbounded growth offline). |
| Cached strokes per board | `MAX_CACHED_STROKES_PER_BOARD = 20_000` | truncation in `cacheWhiteboard`. |
| Cached boards | `MAX_CACHED_BOARDS = 20` | `enforceBoardLimits` drops oldest boards **with no pending ops** first (never silently evicts unsynced work). |
| Quota exceeded | n/a | `wrapError` converts `QuotaExceededError` → `LocalStorageError(quotaExceeded)` surfaced to the UI. |

## Compaction

`compactConfirmedOperations(whiteboardId, keepServerVersion)` removes confirmed
operations whose `server_version <= keepServerVersion` — i.e. acknowledged ops
fall out of the queue after a successful ack, keeping the queue bounded.

## Crash recovery

`recoverInterrupted()` flips any `SUBMITTING` operation back to `PENDING` so an
interrupted submission (tab kill, power loss) is retried on the next open rather
than left in limbo.

## Tests

There is no IndexedDB in the Node test environment, so behavior is exercised via
`WhiteboardLocalStoreLike` with an in-memory `FakeStore` in
`sync-engine.test.ts`. The interface is the contract tests depend on.