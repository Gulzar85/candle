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
| Max request body | 250 KB | `WHITEBOARD_MAX_REQUEST_BODY_BYTES` |

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
