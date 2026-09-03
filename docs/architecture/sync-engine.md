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
                                   │        └─ pushPending: reconcile + ordered submit
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
| `REJECTED` | Server permanently rejected the payload. |
| `CONFLICT` | Could not be reconciled automatically; quarantined. |
| `FAILED` | Exceeded retry budget; needs user attention (never dropped). |

Graph:

```
PENDING ──▶ SUBMITTING ──▶ CONFIRMED ──▶ (compacted/removed)
    ▲          │
    │          ├─ error/network ─▶ PENDING  (retry w/ backoff, up to MAX_RETRIES)
    │          │                        └── exhausting → FAILED
    │          └─ permanent reject ─▶ CONFLICT (quarantined)
    └─ recoverInterrupted() on startup
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
3. Classify each op via `decideReconciliation` (see conflict matrix):
   - submit / rebase → queue for submission (rebase bumps `base_version`).
   - reject → `quarantineConflict` (conflicts store + op marked `CONFLICT`).
4. Submit in `client_sequence` order, one op per request (keeps base-version
   validation and idempotency exact). Confirmed ops are compacted out once acked.
5. After the pass, recompute phase: `synced` (queue empty, no conflicts),
   `pending` (still queued), or `conflict` (any quarantined).

Sync passes are **debounced** through `_scheduleSync` so bursts coalesce into a
single pass.

## Retry / backoff policy

| Server signal | Client action |
|---------------|---------------|
| network / status `0` / `5xx` | Backoff `1s·2^retry_count` capped at 30s + jitter; `retry_count` up to `MAX_RETRIES = 8`; then `FAILED`. |
| `429` rate-limited | Honor `retry_after` (default 10s). |
| `STALE_VERSION` | Reconcile again using the server-reported `current_version`; re-run the pass after 50ms. |
| `FORBIDDEN` / `WHITEBOARD_ARCHIVED` | Permanent: quarantine, notify via `hooks.onConflict`. |
| other 4xx | Permanent: quarantine. |

No path silently deletes a pending operation. The three terminal out-comes are
`CONFIRMED` (compacted), `CONFLICT` (quarantined), or `FAILED` (preserved for
review).

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
| saving | syncing | error | offline`) and the status bar / conflict panel react.

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
  push, partial failure (one acked, one stays pending), conflict quarantine,
  bounded backoff → `FAILED`, idempotent-confirm no-op, and STALE re-reconciliation.
- `sync.test.ts` — HTTP-only fallback path (unscoped `SyncManager`).
- `conflict-resolver.test.ts`, `network-monitor.test.ts` — pure decision +
  connectivity units.