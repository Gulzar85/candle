# Whiteboard Versioning

**Phase 4 — Version and sequence management**

## Version Model

Every whiteboard has a monotonic `version` counter starting at 0. Each accepted
operation increments the version by exactly 1. The version equals the sequence
number of the most recently applied operation.

```
Whiteboard.version = 0  (empty board)
    ↓ Operation 1 applied
Whiteboard.version = 1
    ↓ Operation 2 applied
Whiteboard.version = 2
    ↓ ...
```

## Sequence Model

Every operation has a `sequence` number assigned by the server. Sequences are
monotonic, unique within a whiteboard, and start at 1.

```
sequence = 1 → first operation
sequence = 2 → second operation
sequence = N → Nth operation
```

Sequences are never reused, even after deletions or clears.

## Base Version

Clients submit `base_version` with each operation, representing the version
they observed when creating the operation. The server validates:

```
base_version == whiteboard.version
```

If the server version has advanced (e.g., partner submitted an operation), the
server returns `STALE_VERSION` with:

```json
{
  "error": "STALE_VERSION",
  "current_version": 12,
  "client_version": 10
}
```

## Concurrency

Two clients submitting simultaneously:

1. Both read `version = 10`
2. Both create operations with `base_version = 10`
3. First to acquire the row lock succeeds: `version → 11`
4. Second finds `version = 11 ≠ 10`: receives `STALE_VERSION`

This is enforced by `SELECT ... FOR UPDATE` within `transaction.atomic()`.

## Undo/Redo Versioning

Phase 4 undo/redo is client-side only. The server never sees undo operations.

Phase 5 will make undo a server operation. Two approaches:

**Approach A: Explicit undo operations**
```
CREATE_STROKE (seq=1, version=1)
UNDO_CREATE_STROKE (seq=2, version=2)  // or DELETE_OBJECT
```

**Approach B: Delete-as-undo**
```
CREATE_STROKE (seq=1, version=1)
DELETE_OBJECT (seq=2, version=2)  // undo expressed as delete
```

Both preserve history. The choice depends on Phase 5's collaboration model.

## Clear Canvas

Clear is a logical operation, not a physical delete of rows:

```
CLEAR_CANVAS (seq=5, version=5)
```

After replay, all objects before sequence 5 are irrelevant. A later client
receiving this operation knows: "Everything before this point was cleared."

## Deterministic Replay

Given:
- Same initial state
- Same ordered operations

The result is always the same. This is the foundation for:
- State reconstruction
- Synchronization
- Offline support
- Debugging
- Auditing
