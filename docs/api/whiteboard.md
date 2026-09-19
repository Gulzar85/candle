# Whiteboard API

**Phase 4 endpoints for whiteboard persistence**

All endpoints require authentication (session cookie) and partnership membership.
CSRF protection is enforced via the `X-CSRFToken` header (token read from the
page's `<meta name="csrf-token">` tag).

---

## POST /api/whiteboards/\<public_id\>/operations/

Submit one or more operations to a whiteboard.

### Request

**Single operation:**
```json
{
  "operation_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation_type": "create_stroke",
  "base_version": 5,
  "payload": {
    "object_id": "stroke-abc123",
    "points": [{"x": 10, "y": 20}, {"x": 11, "y": 21}],
    "color": "#2563eb",
    "width": 3,
    "opacity": 1.0,
    "creator_id": "alice"
  }
}
```

**Batch:**
```json
{
  "operations": [
    { "operation_id": "...", "operation_type": "create_stroke", ... },
    { "operation_id": "...", "operation_type": "delete_object", ... }
  ]
}
```

**Array:**
```json
[
  { "operation_id": "...", "operation_type": "create_stroke", ... }
]
```

### Response (200 OK)

```json
{
  "acks": [
    {
      "operation_id": "550e8400-...",
      "sequence": 6,
      "version": 6,
      "duplicate": false
    }
  ],
  "version": 6,
  "applied": 1
}
```

### Duplicate operation (200 OK, idempotent)

```json
{
  "acks": [
    {
      "operation_id": "550e8400-...",
      "sequence": 6,
      "version": 6,
      "duplicate": true
    }
  ],
  "version": 6,
  "applied": 0
}
```

### Operation types

| Type | Payload | Description |
|------|---------|-------------|
| `create_stroke` | `{object_id, points, color, width, opacity, creator_id?}` | Create a freehand stroke |
| `delete_object` | `{object_id}` | Delete an object |
| `clear_canvas` | `{}` (empty or omitted) | Clear all objects |

### Errors

| Status | Code | Description |
|--------|------|-------------|
| 400 | `INVALID_OPERATION` | Structurally invalid operation |
| 400 | `INVALID_PAYLOAD` | Payload failed schema/range validation |
| 401 | `UNAUTHORIZED` | Not authenticated |
| 403 | `FORBIDDEN` | Not a partnership member |
| 409 | `STALE_VERSION` | Client version behind server |
| 409 | `WHITEBOARD_READ_ONLY` | Board is archived |
| 413 | `OPERATION_TOO_LARGE` | Payload exceeds limit |
| 429 | `RATE_LIMITED` | Too many requests |

### Rate limiting

60 requests per 60-second window per user. Returns `retry_after` in the error
payload when rate limited.

### Limits

| Limit | Default | Setting |
|-------|---------|---------|
| Max payload per operation | 100 KB | `WHITEBOARD_MAX_OPERATION_PAYLOAD_BYTES` |
| Max points per stroke | 4,000 | `WHITEBOARD_MAX_STROKE_POINTS` |
| Max batch size | 50 | `WHITEBOARD_MAX_BATCH_OPERATIONS` |
| Max request body (any endpoint) | 5 MB | `MAX_REQUEST_BODY_BYTES` (global, enforced by `apps.core.middleware.RequestSizeGuard`, not whiteboard-specific) |

---

## GET /api/whiteboards/\<public_id\>/

Load the reconstructed whiteboard state.

### Response (200 OK)

```json
{
  "version": 15,
  "objects": [
    {
      "object_type": "stroke",
      "object_id": "stroke-abc",
      "points": [{"x": 10, "y": 20}, {"x": 11, "y": 21}],
      "color": "#2563eb",
      "width": 3,
      "opacity": 1.0,
      "creator_id": "alice"
    }
  ],
  "count": 1
}
```

---

## GET /api/whiteboards/\<public_id\>/operations/list/

Load operations in sequence order (for incremental sync).

### Query parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `after_sequence` | 0 | Return ops with sequence > this value |
| `limit` | 100 | Max operations to return (max 500) |

### Response (200 OK)

```json
{
  "operations": [
    {
      "operation_id": "550e8400-...",
      "sequence": 1,
      "operation_type": "create_stroke",
      "base_version": 0,
      "resulting_version": 1,
      "created_at": "2026-09-03T12:00:00Z",
      "actor_id": 42,
      "payload": { ... }
    }
  ],
  "version": 15,
  "count": 1
}
```

---

## POST /api/whiteboards/\<public_id\>/restore/

*(Phase 9.)* Restore the board to an earlier point in its own history —
creates a new, forward-moving `restore_version` operation. Never deletes or
rewrites history. See `docs/architecture/whiteboard-history.md`.

### Request

```json
{
  "operation_id": "550e8400-e29b-41d4-a716-446655440000",
  "base_version": 12,
  "target_sequence": 7
}
```

### Response (200 OK)

Same ack shape as `POST .../operations/` — `{"acks": [...], "version": N,
"applied": N}`.

### Errors

`STALE_VERSION` if `base_version` no longer matches the current version
(exactly the same server-side base-version validation as any other operation submission).
`INVALID_OPERATION` (400) if `target_sequence` is negative or beyond the
board's current version. `WHITEBOARD_READ_ONLY` (409) on an archived board.
Rate-limited (10/min, distinct from ordinary operation submission's
60/min).

Every other client connected to this board is notified via a lightweight
WebSocket broadcast (`target_sequence` + metadata only, never the full
snapshot) and is expected to refetch full state — see
`docs/architecture/websocket-protocol.md`.

---

## GET /api/whiteboards/\<public_id\>/history/

*(Phase 9.)* Load humanized history entries, newest-first.

### Query parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `before_sequence` | (current version) | Page backward from just before this sequence |
| `limit` | 50 | Max entries to return (max 200) |

### Response (200 OK)

```json
{
  "entries": [
    {
      "sequence": 15,
      "operation_type": "create_stroke",
      "text": "You added a drawing.",
      "count": 1,
      "created_at": "2026-09-04T12:00:00Z",
      "can_restore": false
    }
  ],
  "version": 15,
  "count": 1
}
```

`can_restore` is `false` only for the entry whose `sequence` equals the
board's current version — nothing to restore, already current. `sequence`
is the value to pass as `target_sequence` to the restore endpoint above.
Never exposes raw operation payloads, actor ids, or internal jargon — see
`docs/architecture/whiteboard-history.md`.

---

## POST /api/whiteboards/\<public_id\>/import/

*(Phase 9.)* Import a previously-exported JSON board.

### Request

```json
{
  "schema_version": 1,
  "objects": [
    {"object_id": "s1", "points": [{"x": 0, "y": 0}, {"x": 10, "y": 10}], "color": "#2563eb", "width": 3, "opacity": 1}
  ],
  "clear_first": false
}
```

Object ids are always regenerated server-side — client-supplied ids are
never trusted or reused. Every object is validated exactly like a live
`create_stroke` payload; the whole import is rejected atomically if any
single object fails validation. `clear_first: true` composes the existing
`clear_canvas` operation before importing, for a full replace instead of
adding on top.

### Response (200 OK)

```json
{"imported": 1, "version": 16}
```

### Errors

`INVALID_PAYLOAD` (400) for a missing/unsupported `schema_version`, a
malformed `objects` array, or any single invalid object. `OPERATION_TOO_LARGE`
(413) beyond `MAX_IMPORT_OBJECTS` (2,000). `WHITEBOARD_READ_ONLY` (409) on
an archived board. Rate-limited (5/hour — heavy, infrequent by nature).

---

## Idempotency

Submitting the same `operation_id` twice returns the existing ack with
`duplicate: true`. No new database row is created. This makes network retries
safe.

## Versioning

The `base_version` in the request must match the server's current version. If
the server has a newer version (e.g., partner submitted an operation), the
server returns `STALE_VERSION` with `current_version` and `client_version`.

## Authorization

Every endpoint verifies:
1. User is authenticated
2. User holds an ACTIVE membership in the partnership
3. Partnership is ACTIVE (not ended/pending)
4. Whiteboard belongs to the partnership
