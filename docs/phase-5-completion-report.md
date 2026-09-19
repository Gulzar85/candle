# Phase 5 — Completion Report

**Real-time collaborative whiteboard via Django Channels & WebSockets**

This report was written on a later audit pass against an already-implemented
Phase 5 (the feature has existed and been exercised across several later
sessions in this project's history). It records what the pre-implementation
audit (`docs/architecture/phase-5-audit.md`) flagged, what the shipped
implementation actually does, what this pass found and fixed, and what
Phase 6 can build on.

---

## 1. Phase 0–4 audit — outcome

`docs/architecture/phase-5-audit.md` is the pre-implementation audit and is
not repeated here. Summary of its findings and disposition:

| Issue | Severity | Fixed? |
|---|---|---|
| ASGI WebSocket branch was a commented stub | High | Yes — `config/asgi.py` routes `websocket` through `AuthMiddlewareStack(URLRouter(...))` |
| Dev-environment Redis (v3.2) rejects the `HELLO` handshake redis-py 8/channels-redis 4.3 send | Medium | Yes — development uses `channels.layers.InMemoryChannelLayer`; production requires Redis ≥ 6 (documented in `.env.example` and `docs/architecture/realtime-collaboration.md`) |
| `User` had no `public_id`, risking a raw PK leaking into presence/actor messages | Medium | Yes — `User.public_id` added; `_build_user_public` in `consumers.py` exposes only `public_id` + `safe_display_name(user)` |
| No push mechanism for mid-connection revocation — a revoked member's open WebSocket kept working until their next message | Medium | Partially, at the time of the original audit (write-path only) — **fully closed on this pass**, see §9 below |
| `Engine._emitServerOps` tagged every op in a multi-op gesture (erase, clear) with the *same* `base_version`, so only the first of a batch would ever commit — the rest got spurious `STALE_VERSION` | High | Yes — emits a version chain (`base_version = _serverVersion + envelopes.length`), verified in `engine.ts` |
| Frontend used `whiteboard.public_id` for the API base / WS URL, but routes resolve by **partnership** `public_id` | High (would have 404'd every request) | Yes — templates and `data-wb-*` attributes use `partnership.public_id` throughout |

## 2. Real-time architecture

```
Browser (engine.ts)
  → OperationEnvelope
  → RealtimeController (frontend/src/whiteboard/realtime/controller.ts)
  → WebSocketTransport → wss:// → Django Channels consumer (consumers.py)
       → OperationValidator (validator.py)                — same as HTTP path
       → WhiteboardOperationService.submit_operations()    — same as HTTP path
       → transaction.atomic() + select_for_update()
       → PostgreSQL (WhiteboardOperation, Whiteboard.version)
       → (transaction commits — synchronously, before the next line runs)
       → channel_layer.group_send("whiteboard.<partnership_public_id>", ...)
  → every connected consumer in the group
  → each browser's RealtimeController → Engine.applyRemoteOperation()
  → Canvas re-render
```

The consumer never re-implements persistence logic — it calls the exact same
`WhiteboardOperationService` the Phase 4 HTTP endpoint calls, via
`database_sync_to_async`, so there is one authoritative write path regardless
of transport (spec §11, §43).

## 3. WebSocket protocol

Fully documented in `docs/architecture/websocket-protocol.md`. Message
vocabulary lives in `apps/whiteboard/realtime.py` (kept separate from the
consumer so it's independently testable and reusable by a future mobile
client): `connection.ready`, `connection.error`, `operation.submit`,
`operation.committed`, `operation.rejected`, `sync.request`, `sync.ops`,
`presence.cursor`/`presence.joined`/`presence.left`/`presence.update`,
`ping`/`pong`. Protocol is versioned (`protocol_version` in
`connection.ready`). Error codes match spec §36 exactly: `AUTH_REQUIRED`,
`FORBIDDEN`, `WHITEBOARD_NOT_FOUND`, `WHITEBOARD_ARCHIVED`,
`INVALID_MESSAGE`, `INVALID_OPERATION`, `STALE_VERSION`, `RATE_LIMITED`,
`PAYLOAD_TOO_LARGE`, `SYNC_REQUIRED`, `SERVER_ERROR`.

## 4. Authentication / authorization

`AuthMiddlewareStack` inherits the Django session (no separate WS auth
scheme). Per-connection authorization at `connect()`:
authenticated → partnership resolved from the URL's partnership `public_id`
→ partnership active → `can_access_whiteboard(user, partnership)` (the same
centralized policy Phase 2/4 already use) → whiteboard not archived. Client
IDs are never trusted for authorization — the actor is always
`self.scope["user"]`.

**Revalidation, both paths:**
- *Write path*: `_still_authorized()` re-runs on every inbound message
  (`receive()`), so a revoked member cannot keep submitting operations
  through an already-open socket.
- *Read/passive path* (fixed on this pass — see §9): a member who never
  sends anything is now also disconnected proactively, not just left
  connected and silently receiving broadcasts.

## 5. Concurrency

Unchanged from Phase 4, reused as-is: `select_for_update()` on the
`Whiteboard` row inside `transaction.atomic()`, exact `base_version ==
whiteboard.version` check, `UniqueConstraint(whiteboard, sequence)` as a
database-level backstop against duplicate sequences even under a lost-lock
race. Two simultaneous WS submissions with the same `base_version`: one wins
the row lock and commits; the other's transaction sees the now-advanced
version and raises `StaleVersionError`, mapped to `operation.rejected` /
`STALE_VERSION` (never automatic conflict resolution, never OT/CRDT — exactly
as directed by spec §14).

## 6. Idempotency

`operation_id` (client UUID) is the retry key —
`UniqueConstraint(whiteboard, operation_id)`. A retried submission (lost
response, reconnect-and-resend) returns the original ack with
`duplicate: true` rather than creating a second row. Verified by
`test_submit_duplicate_id_is_idempotent_no_version_bump`.

## 7. Synchronization

- **Initial load**: HTTP `GET /api/whiteboards/<partnership_public_id>/`
  reconstructs state and returns `version`; the client then opens the
  WebSocket and the `connection.ready` frame reports the server's *current*
  version at connect time. If operations landed between the HTTP load and the
  WS becoming ready, the client's `sync.request` (carrying its last known
  version) retrieves anything missed — closing the race spec §44–45
  specifically calls out.
- **Reconnect**: `sync.request {version}` → server returns `sync.ops` with
  every operation after that version, ordered by `sequence` (never by
  timestamp). Verified by `test_sync_delivers_missed_operations`.
- **Gap detection**: the client tracks the last applied sequence; a
  non-contiguous jump triggers a fresh sync rather than silently skipping
  an operation.

## 8. Presence

Ephemeral only — `PresenceJoined`/`PresenceLeft`/`PresenceUpdate` are pure
channel-layer messages, never written to Postgres. Cursor updates are
throttled client-side (see `docs/architecture/presence.md`) so a raw
`pointermove` never becomes a WebSocket frame. Payload exposes only
`public_id` + display name — no email, no session id, no internal PK. Verified
by `test_presence_join_and_leave_broadcast`.

## 9. Fix made on this pass

**Passive-listener revocation gap** (spec §48, explicitly: *"Revoked users
must not retain real-time access indefinitely through an already-open
WebSocket"*). The write-path re-check already existed, but a connection that
only *listens* (never submits an operation) was never re-validated — it would
keep receiving the ex-partner's future strokes for as long as the tab stayed
open. Fixed with a minimal, targeted push:

- `apps/whiteboard/realtime_signals.py::request_reauthorization` — sends a
  `whiteboard.reauthorize` message to the whiteboard's channel group.
- `consumers.py::whiteboard_reauthorize` — group-message handler that
  re-runs the *existing* `_still_authorized()` check on receipt; a
  still-authorized connection is unaffected, only a revoked one closes.
- `apps/partnerships/services.py::end_partnership` — calls
  `transaction.on_commit(lambda: _notify_whiteboard_access_revoked(...))`
  after the partnership-ending transaction commits (never before — same
  "broadcast only after persistence" rule as ordinary operations, spec §12/
  §57/§95). A local import inside the callback keeps the normal
  partnerships → whiteboard dependency direction intact rather than adding a
  module-level coupling for one call site.
- New test: `test_ended_partnership_disconnects_passive_listener` — connects
  both partners, ends the partnership, and asserts the *passive* partner
  (who sent nothing) is pushed `FORBIDDEN` and closed.

`docs/architecture/phase-2-audit.md`'s addendum from that pass, and this
report, together close the loop that the original Phase 5 audit left as
"document access-revocation strategy."

## 10. Frontend architecture

- `RealtimeController` (`frontend/src/whiteboard/realtime/controller.ts`) —
  owns connection state (`connecting`/`connected`/`ready`/`reconnecting`/
  `closed`), reconnect backoff with jitter, and dispatches typed protocol
  messages.
- `WebSocketTransport` (`realtime/transport.ts`) — the concrete transport;
  `RealtimeTransport`-shaped so a future non-WS transport could substitute
  without touching the controller.
- `Engine.applyRemoteOperation()` — applies a committed remote op to local
  state without re-emitting it (idempotent for already-applied ids).
- Dual submission path from `index.ts`: every operation goes through the
  durable `SyncManager` (HTTP, retries, survives disconnects) **and**, when
  the socket is `ready`, also over the WebSocket directly for low-latency
  broadcast — the shared `operation_id` means whichever path lands first
  wins and the second is a idempotent no-op ack, not a duplicate.

## 11. Tests

- `tests/whiteboard/test_websocket.py` — **14/14 passing** (connect/auth/authz,
  broadcast, duplicate idempotency, stale version, malformed operation,
  revocation — both write-path and, as of this pass, passive-listener —
  sync-on-reconnect, presence, unknown message type, oversized message).
- `tests/partnerships/test_authorization.py` + `test_models.py` — **32/32
  passing** (unaffected by the `end_partnership` change; `on_commit` hooks
  are inert under the default `TestCase` atomic wrapper, which is expected
  and doesn't affect assertions that don't depend on them).
- `manage.py check`: clean. `ruff check`/`ruff format --check` on all changed
  files: clean. `mypy` on changed files: clean (pre-existing, unrelated debt
  only, tracked since the Phase 0/1 audits).

## 12. Security

- Origin validated against `WEBSOCKET_ALLOWED_ORIGINS` (env-driven, dev
  defaults to localhost, production requires explicit HTTPS origins — see
  `config/settings/production.py`).
- Per-connection and per-user connection caps (`MAX_CONNECTIONS_PER_USER`)
  limit broadcast fan-out amplification from one user opening many sockets.
- Message-size and per-window rate limits on both message volume and
  operation-submit volume, independent of the HTTP endpoint's own limits.
- No sensitive data logged — `logger.info`/`warning` calls carry only
  `public_id`s and operation metadata, never payload content or credentials.

## 13. Deployment

Documented in `docs/architecture/realtime-collaboration.md`: ASGI server
(Daphne) behind a reverse proxy with WebSocket upgrade support, Redis ≥ 6 for
the channel layer in any multi-process deployment (development's in-memory
layer is single-process only), HTTPS/WSS in production
(`SECURE_SSL_REDIRECT`, `WEBSOCKET_ALLOWED_ORIGINS` restricted to real
origins).

## 14. Known limitations

- No CRDT/OT — intentional (spec §14). Conflicts surface as `STALE_VERSION`
  and the client re-syncs; this is sufficient for a two-person, mostly
  non-overlapping drawing surface and documented as a deliberate simplicity
  choice in `docs/architecture/conflict-strategy.md`.
- Presence roster gap: a client that connects *after* a peer is already
  present learns about them only through their own `presence.joined`
  broadcast at connect time (not a retroactive roster snapshot) — see the
  Phase 3 audit's note on this same limitation for the on-canvas facepile.
- No horizontal-scale load testing performed; the architecture (Redis channel
  layer, stateless consumers) supports it, but it hasn't been measured beyond
  single-process development.

## 15. Phase 6 readiness

Phase 6 (offline/PWA) can build directly on this without touching the
real-time layer: `SyncManager`/`SyncEngine` already exist as the durable
HTTP-backed operation queue with retry/backoff and idempotent submission;
`RealtimeTransport` is already an abstraction the offline layer can sit
alongside rather than inside; and the server's `operation_id`-keyed
idempotency means a queued-while-offline operation replayed later is safe by
construction. What Phase 6 actually adds on top — IndexedDB persistence of
the queue, background sync registration, and PWA service-worker caching of
the app shell — is layered on top of, not a rewrite of, this phase's
architecture.
