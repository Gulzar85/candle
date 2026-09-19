# Conflict Strategy — Realtime Edits

**Phase 5 — How concurrent edits reconcile**

Candle does **not** implement CRDTs or operational transformation. It relies on
a hybrid that is far simpler and fully server-authoritative:

1. **Serialization at the server** (the source of truth), via a row-locked
   transaction that assigns a strictly increasing version per accepted op.
2. **Optimistic local apply** for responsiveness.
3. **Version-chained submission** so multi-op gestures stay whole.
4. **Deterministic replay + idempotency by `operation_id`** for convergence.

Because every operation is *a step that moves the board from version N to
N+1*, two editors who both start from N and each submit a different operation
cannot both win: the row lock makes one take N→N+1 and the other get
`STALE_VERSION` at N+1.

---

## The two conflict classes

### 1. Concurrent writes (base-version conflict)

```
Both read version = N
A submits op_a base=N      → wins: version N→N+1
B submits op_b base=N      → STALE_VERSION (server at N+1)
```

B's client receives `operation.rejected {reason: STALE_VERSION,
current_version: N+1, client_version: N}` and recovers:

1. Clear its pending (unconfirmed) set.
2. Adopt `current_version`.
3. Request a `sync.ops` catch-up (the server replays A's `op_a` and anything
   else B missed).
4. Apply the replay deterministically to the local board.

The user's stroke is already on the board (optimistic); if it was not committed
it is a *local-only* artifact. In the common case the replay includes it, so
convergence is seamless; the rare true rejection surfaces a message and a
resync.

### 2. Multi-op gesture ordering (version chain)

A single gesture can produce several operations — an eraser stroke becomes
`remove` + N `add`s, a clear is one op, a multi-stroke add is several — and the
server applies **one op per version**. If they all carried the same
`base_version`, only the first would commit. The frontend therefore emits a
**version chain**:

```
remove   base = N     → version N+1
add #1   base = N+1   → version N+2
add #2   base = N+2   → version N+3
```

- The **engine** stamps chains via `_emitServerOps` (advancing its local
  high-water mark per emitted op) — correct for the HTTP fallback path.
- The **controller** independently computes `base = confirmed + inFlight` for
  the WebSocket path, which also stays correct when a partner's committed
  operation advances the confirmed version mid-gesture.

### 3. Idempotency

Retries and replays are deduplicated by `operation_id` (the frontend keeps an
`applied` set; the server has a unique `(whiteboard, operation_id)` and replies
`duplicate: true`). This makes re-sync and reconnect safe.

---

## Convergence argument

Given:

- a single initial state (empty board, or one reconstructed from HTTP),
- a single ordered sequence of committed operations,

deterministic replay yields a single final state on every client. The server is
the unique orderer; clients are replay engines. Because remote apply is
idempotent by `operation_id`, a client that has already applied the same op
(from an echo or a replay) does not apply it twice.

Remote apply is also *idempotent over the board*: a `delete_object` whose
subject no longer exists and a `clear_canvas` on an empty board are no-ops.
Phase 9's `move_object`/`resize_object` follow the same rule — a target
object that no longer exists locally is a no-op, matching `delete_object`'s
existing convention, not an error.

---

## Rejection → recovery matrix

| Condition | Server response | Client recovery |
|---|---|---|
| `base_version` behind | `STALE_VERSION` with `current_version` | Clear pending, adopt version, `sync.request`, replay |
| Too far behind | `SYNC_REQUIRED` | Full HTTP state reload + resync baseline |
| Archived / read-only | `WHITEBOARD_ARCHIVED` then close(4401) | Read-only mode |
| Access revoked | `FORBIDDEN` then close(4401) | Show "access revoked", stop editing |
| Duplicate resubmit | `operation.committed` with `duplicate: true` | Already applied; just confirm version |

---

## Why not CRDT / OT

CRDTs and OT make *every* participant a co-equal mutator and require careful
semantic-free concurrency (e.g. Yjs, Automerge). Candle's whiteboard is capped
at two partners on a single board with a trivial object model, so the operational
-log + row-lock + replay approach delivers the same perceived outcome with a
fraction of the implementation and audit risk. It keeps PostgreSQL as the
single authoritative record, which the product explicitly requires.

## Trade-offs

- Optimistic edits that lose the base-version race are *not* folded back
  automatically when they lose (they may become local-only). A future phase can
  replay the loser's operation on top of the winner's.
- Collaborative undo is not implemented. Undo is client-local; a "true"
  collaborative undo would need a new operation type and is deferred.
- Eraser paths whose segment ids (suffixed) diverge across heavily-skewed
  boards are reconciled by full HTTP resync rather than point reconciliation.