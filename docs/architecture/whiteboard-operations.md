# Whiteboard Operations

**Phase 4 — Operation types, payload schemas, and validation**

The operation is the unit of change. Every whiteboard mutation — from either
partner, over HTTP or WebSocket — is one row in `WhiteboardOperation`,
validated by `OperationValidator` (`apps/whiteboard/validator.py`) before the
service layer (`apps/whiteboard/service.py`) ever touches the database. See
`whiteboard-versioning.md` for sequence/version semantics and
`whiteboard-sync-contract.md` for the wire envelope.

## The operation set

The original three types below matched exactly what the Phase 3 client
engine produced (`WhiteboardOperationType` in `apps/whiteboard/enums.py`).
Phase 9 appended three more — `move_object`, `resize_object`,
`restore_version` — following this section's own stated rule: the set is
append-only, future types are added, never renamed, because persisted rows
and future sync clients depend on the stable string value. See
`docs/architecture/phase-9-operation-types.md` for the three Phase 9 types'
full payload schemas and validation rules; they are not repeated here.

| Type | Meaning |
|---|---|
| `create_stroke` | Create one stroke object (upsert by `object_id`). |
| `delete_object` | Remove one object by `object_id`. |
| `clear_canvas` | Empty the whole board — a logical operation, never a bulk row delete. |
| `move_object` *(Phase 9)* | Translate one object by a relative `(dx, dy)`. |
| `resize_object` *(Phase 9)* | Scale one object's points from a fixed anchor. |
| `restore_version` *(Phase 9)* | Reset board state to an earlier point in the same append-only history — a logical operation, like `clear_canvas`. |

## Payload schemas

### `create_stroke`

```json
{
  "object_id": "s-42-abc123",
  "points": [{"x": 10.2, "y": 20.5}, {"x": 11.0, "y": 21.3}],
  "color": "#2563eb",
  "width": 3,
  "opacity": 1,
  "creator_id": "AB"
}
```

Validated: `object_id` non-empty string; `points` an array of ≥2 `{x, y}`
pairs of finite numbers, capped at `limits.MAX_STROKE_POINTS` (4,000 —
configurable via `WHITEBOARD_MAX_STROKE_POINTS`); `color` a 6-digit hex string;
`width` a number in `[1, 64]`; `opacity` a number in `[0, 1]`; `creator_id`
optional, string if present (informational — presence UI only, never an
authorization input).

### `delete_object`

```json
{"object_id": "s-42-abc123"}
```

Validated: `object_id` non-empty string. Deleting an id that no longer exists
is not an error — `WhiteboardStateBuilder.apply` treats it as a no-op, which is
what makes retries and out-of-order redelivery safe.

### `clear_canvas`

```json
{}
```

No fields. Any extra keys are ignored (forward-compatible) rather than
rejected, but the payload is still subject to the overall size ceiling.

## Erase is delete + create, not a fourth operation type

The eraser tool does not get its own operation type. Erasing a stroke emits
`delete_object` for the original plus `create_stroke` for each surviving
clipped segment (see `frontend/src/whiteboard/tools.ts::clipToEraser` and
`Engine.finishErase`). This keeps the operation set at three, keeps erase
auditable ("who deleted this, and what did they add back"), and means the
server-side replay logic never needs to know what "erase" means — it only ever
applies `create_stroke` and `delete_object`.

## What the server never trusts

`OperationValidator._check_no_forbidden_fields` rejects any operation carrying
`actor`, `created_at`, `sequence`, `resulting_version`, `user_id`,
`whiteboard`, or `public_id` — these are exclusively server-determined. A
client can *propose* an operation; it cannot assert who performed it, when, or
at what sequence/version. `operation_id` (client-supplied UUID) and
`base_version` (non-negative int) are the only client-controlled envelope
fields, and both are themselves validated (§ below) before they reach the
service layer.

## Idempotency

`operation_id` is the retry key: `WhiteboardOperation` has a unique constraint
on `(whiteboard, operation_id)`. `WhiteboardOperationService.submit_operations`
checks for an existing row with that id *inside* the same row-locked
transaction that would otherwise create one; a match short-circuits to
returning the original ack with `duplicate: true` rather than inserting a
second row. This is what makes "client sends operation → response lost →
client retries" safe — see `whiteboard-sync-contract.md` for the full retry
contract.

## Size and rate limits

Whiteboard-specific, configurable via Django settings (`apps/whiteboard/limits.py`):

| Limit | Default | Setting |
|---|---|---|
| Points per stroke | 4,000 | `WHITEBOARD_MAX_STROKE_POINTS` |
| Operation payload (bytes) | 100,000 | `WHITEBOARD_MAX_OPERATION_PAYLOAD_BYTES` |
| Operations per batch request | 50 | `WHITEBOARD_MAX_BATCH_OPERATIONS` |

An oversized stroke or payload is rejected with `OPERATION_TOO_LARGE` (413)
before any database write — validation always runs before the transaction
that would persist it.

The overall HTTP request body size (any endpoint, not whiteboard-specific) is
capped separately and earlier in the stack, by `apps.core.middleware.RequestSizeGuard`
(setting: `MAX_REQUEST_BODY_BYTES`, default 5 MB) — see `.env.example`. A
prior whiteboard-only `WHITEBOARD_MAX_REQUEST_BODY_BYTES` constant (250 KB)
was never actually wired to that middleware and has been removed; 5 MB is the
real, enforced limit and is consistent with a legitimate full-size batch
(`MAX_OPERATION_PAYLOAD_BYTES` × `MAX_BATCH_OPERATIONS` ≈ 100 KB × 50).
