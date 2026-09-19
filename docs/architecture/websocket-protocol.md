# WebSocket Protocol — Realtime Collaboration

**Phase 5 — Message vocabulary, safety limits, and transport contract**

Candle collaborates over a single WebSocket per whiteboard. The socket is a
*transport*, never the source of truth: every operation is validated and
persisted by the authoritative `WhiteboardOperationService`, and PostgreSQL
remains the canonical board state.

The protocol is **versionable**: every connection carries `protocol_version`.
Bump it (in `apps/whiteboard/realtime.py::PROTOCOL_VERSION`, mirrored by the
frontend in `realtime/types.ts`) whenever the message vocabulary or field
semantics change incompatibly. `protocol_version` is currently `1`.

---

## Endpoint

```
ws(s)://<host>/ws/whiteboards/<partnership_public_id>/
```

> The path is keyed by the **partnership** `public_id` (like the page URL and
> HTTP API), not the `Whiteboard.public_id`.

WebSocket connections authenticate via Django session cookies (through
`AuthMiddlewareStack`). Authorization is checked on connect *and* re-checked on
every inbound message, so a member whose access is revoked cannot keep writing
through an already-open socket.

## The Origin check

A server-side `WEBSOCKET_ALLOWED_ORIGINS` allowlist bounds which origins may
open sockets. Empty allowlist = any origin (dev). Browsers always send an
`Origin` header; non-browser clients must either be allowed by the allowlist or
the allowlist must be empty.

---

## Connection lifecycle

```
Client                          Server
  |  open socket                  |
  |------------------------------>|
  |                               | origin + auth + authz checks
  |                               | [fail] close(4403/4400) before/after accept
  |                               | group_add(whiteboard.<id>)
  |                               | group_send presence.joined{you?}
  |  <- connection.ready          |  (protocol_version, connection_id, version,
  |                               |   user, partner)
  |  -> sync.request {version}    |  (client confirms its baseline)
  |  <- sync.ops                  |  (any operations the client is missing)
```

Close codes (application-level, IANA-retired 4xxx range):
- `4403` policy violation — origin / authn / authz
- `4400` invalid data — malformed / oversized message
- `4401` going away — access revoked / board archived
- `4405` unsupported — protocol version mismatch

---

## Message types

### Client → Server

| `type` | Fields | Purpose |
|---|---|---|
| `sync.request` | `version: int >= 0` | Ask for operations after a version. |
| `operation.submit` | `operation_id: uuid`, `base_version: int`, `operation: {operation_type, payload}`, optional `client_id` | Submit one operation. |
| `presence.cursor` | `x`, `y` (normalized 0..1) | Broadcast the local cursor position. |
| `ping` | — | Heartbeat. |

### Server → Client

| `type` | Fields | Purpose |
|---|---|---|
| `connection.ready` | `protocol_version`, `connection_id`, `version`, `user`, `partner?` | On connect: baseline + roster. |
| `connection.error` | `code`, `message` | Fatal / protocol error. |
| `operation.committed` | `operation_id`, `sequence`, `version`, `duplicate`, `actor`, `operation`, optional `client_id` | A committed operation (mine or a partner's). |
| `operation.rejected` | `reason`, `message`, optional `current_version`/`client_version` | An operation was refused. |
| `sync.ops` | `version`, `operations: [ServerOperation]` | Catch-up replay, oldest → newest. |
| `sync.required` | — | Client is too far behind; a full resync is needed. |
| `presence.joined` / `presence.left` / `presence.update` | `user`, optional `cursor` | Presence changes. |
| `pong` | — | Heartbeat reply. |

`ServerOperation` = `{operation_id, sequence, operation_type, base_version,
resulting_version, payload}`.

### Phase 9: `restore_version` broadcasts a lightweight payload

A completed restore (submitted via the separate `POST .../restore/` HTTP
endpoint, never over this WebSocket) is announced to the group via the
same `operation.committed` envelope shape, but its `operation.payload` is
deliberately **stripped down to `{"target_sequence": N}`** — the full
snapshot (`objects`) is never put on the wire, since a real restore
snapshot can run into the hundreds of KB to multiple MB (see
`docs/performance/phase-9-benchmarks.md`), comfortably exceeding
`MAX_MESSAGE_BYTES` below. **Every client that receives an
`operation.committed` (or a `sync.ops` entry) with `operation_type ===
"restore_version"` must do a full `GET /api/whiteboards/<id>/` refetch
instead of trying to apply the payload** — see
`docs/architecture/whiteboard-history.md` for the full design and why this
is safe (operation rows are immutable, so "state as of sequence N" is
always correctly recomputable from scratch).

---

## Error codes

| Code | Meaning | Recovery |
|---|---|---|
| `AUTH_REQUIRED` | Not authenticated. | Reauth / full reload. |
| `FORBIDDEN` | Not an active member. | Terminal; document. |
| `WHITEBOARD_NOT_FOUND` | Bad / archived id. | Terminal. |
| `WHITEBOARD_ARCHIVED` | Board read-only (archived). | Terminal; read-only mode. |
| `INVALID_MESSAGE` | Bad frame / message type. | Drop frame. |
| `INVALID_OPERATION` | Operation failed domain validation. | Drop op; correct client. |
| `STALE_VERSION` | `base_version` behind server version. | **Resync** (`sync.request`). |
| `PAYLOAD_TOO_LARGE` | Operation over `MAX_OPERATION_PAYLOAD_BYTES`. | Drop op. |
| `RATE_LIMITED` | Too many frames/submits. | Back off. |
| `SYNC_REQUIRED` | Client state is too far apart. | Full reload / resync. |
| `SERVER_ERROR` | Internal error (never leaks details). | Resync. |

---

## Consensus & idempotency

- Each operation is atomic and row-locked (`SELECT ... FOR UPDATE`) inside one
  transaction; `Whiteboard.version` advances by exactly 1 per newly-applied
  operation.
- Operations are **idempotent by `operation_id`**. If a client retries an
  already-applied `operation_id`, the server replies `operation.committed` with
  `duplicate: true` rather than an error.
- Broadcast happens **only after** the transaction commits (`database_sync_to_async`
  returns after `transaction.atomic` exits), so group members never see
  uncommitted state.

## Same-connection ordering

The server processes a socket's messages sequentially, so a client's own
operations commit **in the order they were sent**. Within a single gesture that
expands to several operations (an eraser stroke → `remove` + N `add`s; a clear;
a multi-stroke add), a client must submit a **version chain**:

```
op A  base = N        → version N+1
op B  base = N+1      → version N+2
op C  base = N+2      → version N+3
```

The frontend engine emits these chains (see `whiteboard-engine.md` and
`conflict-strategy.md`).

---

## Safety limits (per connection)

| Limit | Value | Enforced |
|---|---|---|
| Max inbound text frame | `MAX_MESSAGE_BYTES = 250_000` bytes | Consumer, before parse |
| Max operation payload | `MAX_OPERATION_PAYLOAD_BYTES = 100_000` bytes | Validator / service |
| Message rate | 600 frames / 60 s | Consumer |
| Submit rate | 120 `operation.submit` / 60 s | Consumer |
| Presence cursor coalescing | ~20 Hz client-side | Client |

---

## Heartbeat

The client sends `ping` every `15_000 ms` and expects `pong`. If no `pong`
arrives within `20_000 ms` the transport closes the socket (code `4000`) and
reconnects with exponential backoff (`500 ms` base, `15 s` cap, 8 attempts).

## Reconnection & resync

On reconnect the client opens a fresh socket, receives `connection.ready`, then
sends `sync.request {version}` for its last confirmed version. The server
replies `sync.ops` with everything missing, and the client applies them
deterministically (idempotent by `operation_id`). If a client is too far behind,
the server emits `sync.required` and the client falls back to a full HTTP reload.

---

## Testing

Backend: `python -m pytest tests/whiteboard/test_websocket.py` (13 tests: auth,
authz, broadcast, dedupe, stale, presence, revalidation, sizes, sync, rate).
Frontend: `node node_modules/vitest/vitest.mjs run src/whiteboard/realtime`
(18 tests: transport reconnect/heartbeat/size + controller state machine,
chaining, dedupe, presence, stale recovery).