# Whiteboard Sync Contract

**Phase 4 — Client/server synchronization foundation**

## Overview

Phase 4 establishes the persistence and synchronization foundation. The client
maintains a local whiteboard state that is persisted to the server through an
operation queue. The server is authoritative for versions and sequences.

## Client Architecture

```
Canvas Engine
    ↓
Local Op (add/remove/clear)
    ↓
Board State (past/future stacks)
    ↓
Server Operation Envelope
    ↓
SyncManager (queue + retry)
    ↓
WhiteboardRepository (HTTP)
    ↓
Django API
    ↓
PostgreSQL
```

## Version Model

| Concept | Source | Description |
|---------|--------|-------------|
| `serverVersion` | Server ack | Last version confirmed by the server |
| `localVersion` | Client | Version the client believes it is at (increments on each local commit) |
| `pendingCount` | Client | Number of operations awaiting server acknowledgement |

### Version flow

1. Client loads state: `serverVersion = N`, `localVersion = N`
2. Client draws: `localVersion = N+1`, operation enqueued
3. Server accepts: `serverVersion = N+1`, `localVersion = N+1`
4. Server rejects (stale): `localVersion` remains at N+1, error shown

## Operation Queue

Pending operations are stored in memory (not IndexedDB). The queue:

- Preserves operation order (FIFO)
- Sends operations as a batch to reduce network overhead
- Retries on transient failures with exponential backoff
- Stops retrying on `STALE_VERSION` (requires full reload)
- Stops retrying after 5 failed attempts

### Retry strategy

| Attempt | Delay |
|---------|-------|
| 1 | 500ms |
| 2 | 1s |
| 3 | 2s |
| 4 | 4s |
| 5 | 8s |
| 6+ | Error state |

Rate limit (429) retries after the server-specified `retry_after` period.

## Save Status UI

| Status | Dot color | Label | Retry button |
|--------|-----------|-------|--------------|
| `saved` | Green | "Saved" | Hidden |
| `saving` | Yellow (pulse) | "Saving..." | Hidden |
| `pending` | Gray | "Changes pending" | Hidden |
| `error` | Red | Error message | Visible (if pending ops) |

## Error Recovery

| Error | Client behavior |
|-------|-----------------|
| Network failure | Retry with backoff, keep local state |
| Server error (5xx) | Retry with backoff |
| Rate limit (429) | Retry after cooldown |
| Stale version (409) | Show "Refresh to continue", stop retry |
| Invalid operation (400) | Show error, discard operation |

Local drawing is never lost during the session. Failed operations remain in
the queue for retry.

## Undo/Redo

Phase 4 undo/redo is **client-side only**. The past/future stacks track local
operations. Undo does NOT send a DELETE to the server — it only reverts the
local view.

Phase 5 will make undo a server operation:

```
CREATE_STROKE → UNDO_CREATE_STROKE
```

or equivalently:

```
CREATE_STROKE → DELETE_OBJECT
```

## Future WebSocket Integration (Phase 5)

The SyncManager is designed so Phase 5 can swap the transport:

```
Current:  SyncManager → WhiteboardRepository (HTTP)
Phase 5:  SyncManager → WhiteboardRepository (WebSocket)
```

The repository abstraction makes this a transport swap. The operation envelope
format and version model remain identical.

## Future Offline Support (Phase 6)

Phase 6 will add:

- IndexedDB for durable pending operation storage
- Service worker for background sync
- Conflict resolution for concurrent offline edits
- Full version range synchronization

The current in-memory queue is the subset of Phase 6's architecture that works
without persistent storage.

## CSRF Handling

The CSRF token is read from the page's `<meta name="csrf-token">` tag (set by
Django's template). The repository sends it as the `X-CSRFToken` header on all
POST requests. This maintains Django's CSRF protection while allowing JavaScript
API calls.
