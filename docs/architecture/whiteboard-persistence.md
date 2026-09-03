# Whiteboard Persistence Architecture

**Phase 4 — Server-side operation persistence**

## Overview

Whiteboard persistence is operation-based, not screenshot-based. The server stores
an append-only, deterministically-replayable operation ledger. Any client can
reconstruct the current whiteboard state by replaying operations in sequence order.

```
User draws
    ↓
Operation created (client)
    ↓
Queued in memory
    ↓
POST /api/whiteboards/<id>/operations/
    ↓
Django APIView
    ↓
WhiteboardOperationService.submit_batch()
    ↓
transaction.atomic()
    select_for_update() on Whiteboard row
    ↓
Validate → Dedup → Version → Sequence → Insert
    ↓
PostgreSQL
    ↓
200 OK with version ack
```

## Data Model

```
Partnership (1) ──── (1) Whiteboard
                         │
                         ├── public_id (UUID, for URLs)
                         ├── status (ACTIVE / ARCHIVED)
                         ├── version (monotonic counter)
                         ├── last_operation_at
                         └── title
                         │
                         └── (N) WhiteboardOperation
                                  ├── operation_id (UUID, idempotency key)
                                  ├── sequence (server-assigned ordering)
                                  ├── actor (FK → User)
                                  ├── operation_type (enum)
                                  ├── payload (JSONB)
                                  ├── base_version (client's baseline)
                                  ├── resulting_version (server-assigned)
                                  └── created_at
```

## Ownership

The Whiteboard belongs to the **Partnership**, not to any individual User. Both
partners can create operations. Each operation records which user (`actor`)
performed it.

## Lifecycle

- **ACTIVE**: board accepts new operations.
- **ARCHIVED**: board is read-only. No new operations accepted. Board remains
  readable. Either partner can archive/restore (Phase 4: admin-only; Phase 5:
  UI controls).

## Concurrency

Concurrent operations are serialized by `SELECT ... FOR UPDATE` on the Whiteboard
row within an `atomic()` transaction. The server assigns sequence numbers and
versions sequentially. If two clients submit at the same time, one succeeds and
the other receives a `STALE_VERSION` error with the current version, requiring
the client to refresh.

## Idempotency

Each operation carries a client-generated `operation_id` (UUID). If the same
`operation_id` is submitted twice (e.g., network retry), the server returns the
existing acknowledgement without creating a duplicate row. This is enforced by
a database unique constraint on `(whiteboard, operation_id)`.

## State Reconstruction

The `WhiteboardStateBuilder` replays operations in sequence order to build the
current object state:

- `CREATE_STROKE` → upsert object by `object_id` (z-order: append to end)
- `DELETE_OBJECT` → remove object by `object_id`
- `CLEAR_CANVAS` → remove all objects

Given the same operations, replay is always deterministic.

## Snapshots

Snapshots are **not implemented** in Phase 4. Performance testing with 10,000
operations shows replay completes in <50ms. Snapshots should be considered when:

- Replay time exceeds 200ms for typical boards
- Operation count exceeds 10,000 regularly
- Offline sync requires fast state loading

When introduced, snapshots should be:
- Periodic (e.g., every 1000 operations)
- Optional
- Never created after every operation (that defeats the operation architecture)

## Privacy

Whiteboard content is private shared data. Operations are never exposed through:
- Public URLs
- Unauthenticated endpoints
- Search engines
- Public caches
- Analytics payloads
- Debug logs

Stroke payloads are never logged.
