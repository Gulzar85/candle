# Sync Engine

**Date:** 2026-09-03
**Scope:** Phase 6 — `SyncEngine` (`frontend/src/whiteboard/sync-engine.ts`) and
its lifecycle.

The engine owns the durable local queue and drives server synchronization. It
does not touch the canvas or DOM; the whiteboard engine (`index.ts`) calls
`submit` after a local commit, and the engine reports status through a hook so
the UI can render state.

---

## Overview

```
WhiteboardEngine ──submit(op)──▶ SyncEngine ──store──▶ IndexedDB queue
                                   │
                                   │  triggerSync (debounced)
                                   │        ▼
                                   │   runSync
                                   │        ├─ read pending ops
                                   │        ├─ read server version (controller if present)
                                   │        └─ pushPending: anchored ordered submit
                                   ▼
                              RealTimeController / WhiteboardRepository (server)
```

Two transports exist for the server push:

- **WebSocket (preferred)** — the realtime controller's submit path.
- **HTTP fallback** — `WhiteboardRepository.submitBatch` when the socket is
  unavailable (also the durable path when scoped; the only path when unscoped).

The engine is written against `WhiteboardLocalStoreLike`, a structural subset of
`WhiteboardLocalStore`, so tests run against an in-memory fake in Node (no
IndexedDB in Vitest).

## Operation lifecycle

| Status | Meaning |
|--------|---------|
| `PENDING` | Durable on device; awaiting submission. |
| `SUBMITTING` | An attempt is in flight; a crash here is recoverable. |
| `CONFIRMED` | Server acknowledged; eligible for compaction. |
| `REJECTED` | Legacy — no longer produced; retained only for old rows. |
| `CONFLICT` | Legacy — no longer produced; retained only so old rows can be healed at reopen. |
| `FAILED` | Server permanently rejected, or retry budget exhausted; surfaced as a save error (never dropped). |

Graph:

```
PENDING ──▶ SUBMITTING ──▶ CONFIRMED ──▶ (compacted/removed)
    ▲          │
    │          ├─ error/network ─▶ PENDING  (retry w/ backoff, up to MAX_RETRIES)
    │          │                        └── exhausting → FAILED
    │          ├─ STALE_VERSION ─▶ PENDING  (re-anchor, reschedule ~50ms)
    │          └─ permanent reject ─▶ FAILED (save error; never quarantined)
    └─ recoverInterrupted() on startup; requeueConflicts() heals legacy rows
```

## Durability point

`submit` writes the operation with `status: "PENDING"` via `saveOperation`
**before** emitting any server traffic and before returning `true`. The promise
resolving is the crash-safety guarantee. If the write fails (unavailable /
quota), `submit` returns `false` and the status becomes `error` — the user is not
falsely told the change is saved.

## Sync pass (`runSync` → `pushPending`)

1. Read pending ops, ordered by `client_sequence`.
2. Read the current server version (preferred source: a `setServerVersionGetter`
   wired from the realtime controller's confirmed version; fallback: the engine's
   cached `_serverVersion`).
3. Anchor the pass to the newest known version. There is **no classification
   step** — every op is submitted against this anchor, which each ack advances,
   so the queue never replays against a stale recorded base. (A partner's
   mid-flight commit can still advance the board between the anchor and a given
   submit; that is exactly what the server's `STALE_VERSION` reply catches, and
   the engine re-anchors and re-runs the pass.)
4. Submit in `client_sequence` order, one op per request (keeps base-version
   validation and idempotency exact). Confirmed ops are compacted out once acked,
   and each ack advances the pass anchor for the next op.
5. After the pass, recompute phase: `synced` (queue empty), `pending` (still
   queued), or `error` (any permanent failure). There is no `conflict` phase.

Sync passes are **debounced** through `_scheduleSync` so bursts coalesce into a
single pass.

## Retry / backoff policy

| Server signal | Client action |
|---------------|---------------|
| network / status `0` / `5xx` | Backoff `1s·2^retry_count` capped at 30s + jitter; `retry_count` up to `MAX_RETRIES = 8`; then `FAILED`. |
| `429` rate-limited | Honor `retry_after` (default 10s). |
| `STALE_VERSION` | Re-anchor to the server-reported `current_version`; re-run the pass after 50ms. |
| `FORBIDDEN` / `WHITEBOARD_ARCHIVED` | Permanent: mark `FAILED` (save error in the status bar). |
| other 4xx | Permanent: mark `FAILED`. |

No path silently deletes a pending operation, and no path parks one for manual
review: the two terminal outcomes are `CONFIRMED` (compacted) or `FAILED`
(preserved, surfaced as a save error). `requeueConflicts()` /
`resolveAllConflicts()` exist only to heal legacy quarantined rows from older
builds back into the pending queue.

## Crash recovery

A `SUBMITTING` operation means the app was interrupted mid-write. On
`initialize`, `recoverInterrupted()` flips any `SUBMITTING` row back to `PENDING`
so it is retried on reconnect. `initialize` returns `{ recovered }` (the count)
and restores the engine's pending count and phase.

## Network integration

`onConnectionState(online, reconnecting)` maps transport + `navigator.onLine`
signals onto `NetworkMonitor`:

- `online && !reconnecting` → `markConnected` + `triggerSync` (flush the queue).
- `reconnecting` → `markDisconnected(true)`.
- otherwise → `markDisconnected()`.

On reconnect the engine automatically pushes whatever accumulated offline.

## Status surface to the UI

`onStatusChange` emits a `SyncEngineStatus`:

```
{ phase, network, pendingCount, conflictCount, serverVersion, lastError }
```

`SyncManager` folds this into its own status model (`saveStatus: saved | pending
| saving | syncing | error | offline`) and the status bar reacts. `conflictCount`
is retained as legacy-healing bookkeeping and is 0 in normal operation.

## Public facade (`SyncManager`)

- Durable-first: when scoped to a whiteboard + owner, it builds and drives a
  `SyncEngine`.
- HTTP-only fallback: when unscoped (no IndexedDB path), it queues a small
  in-memory `_legacyQueue` and retries via `submitHttpLegacy`; `retryPending`
  re-submits the queued ops.
- `retryPending()`/`triggerSync()` → flush; `cacheWhiteboard`/
  `loadCachedWhiteboard()` → offline reopen; `hasUnsyncedWork()`/`clearLocalData`
  → logout guard.

## Testing

- `sync-engine.test.ts` — in-memory fake store; covers submit durability, ordered
  push, partial failure (one acked, one stays pending), stale destructive ops
  re-anchoring and submitting (no quarantine), permanent rejection → `FAILED`,
  bounded backoff → `FAILED`, idempotent-confirm no-op, STALE re-sync, and
  legacy-conflict healing via `requeueConflicts`/`resolveAllConflicts`.
- `sync.test.ts` — HTTP-only fallback path (unscoped `SyncManager`).
- `network-monitor.test.ts` — connectivity unit.