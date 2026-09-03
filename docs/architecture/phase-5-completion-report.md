# Phase 5 — Completion Report

**Date:** 2026-09-03
**Goal:** Real-time collaboration via Django Channels WebSockets — authorized
partners see each other's committed operations at low latency while PostgreSQL
remains the source of truth.

---

## Delivered

### Backend (was completed earlier; verified this pass)
- `config/asgi.py` → `ProtocolTypeRouter` wiring HTTP + WebSocket
  (`AuthMiddlewareStack` + `URLRouter`).
- `apps/whiteboard/routing.py`, `realtime.py` (protocol v1, safety limits,
  builders), `consumers.py` (origin/auth/authz, per-message re-auth, size/rate
  limits, group relay, presence self-exclusion, `sync.ops` replay, error
  mapping).
- `apps/accounts/migrations/0002_user_public_id.py` — stable non-`pk` actor id.
- 13 WebSocket tests + a view test binding realtime data to `partnership.public_id`.
- Backend suite green: `python -m pytest tests/whiteboard -q` (79), `manage.py check` clean.

### Frontend (this pass)
- `realtime/types.ts` — wire contract (server/client frames, error codes,
  connection states).
- `realtime/transport.ts` — `RealtimeTransport` + `WebSocketTransport`:
  exponential-backoff reconnect (8 attempts), heartbeat (ping / pong-timeout),
  250 KB frame cap, frame dispatch, injectable WebSocket for tests.
- `realtime/controller.ts` — `RealtimeController`: state machine
  (`connecting→connected→syncing→ready`), authoritative confirmed version,
  dedupe by `operation_id`, **base-version chaining** (`confirmed + inFlight`),
  optimistic submit, remote apply relay, presence relay, STALE recovery.
- `engine.applyRemoteOperation` — map a server op to a reversible local `Op`
  and render **without** re-emitting; idempotent.
- `engine._emitServerOps` — **multi-op version-chain fix** (each sub-op gets an
  incrementing `base_version`), fixing the prior "one gesture, one base_version"
  bug that would reject every op after the first.
- `index.ts` — WS URL build, `submitLocal` routing (**WS primary**, HTTP
  fallback), connection indicator, presence roster + partner cursors.
- `whiteboard.html` — realtime status + presence elements; data already keyed to
  `partnership.public_id`.

### Tests (this pass)
- `realtime/transport.test.ts` (9): connect, dispatch, malformed/oversized
  ignore, send serialize, backoff reconnect, max-attempts, heartbeat, pong
  keepalive, explicit close.
- `realtime/controller.test.ts` (9): state machine, chaining, optimistic
  submit + own-echo dedupe, partner apply + dedupe, `sync.ops` idempotency,
  presence relay, connection errors, STALE recovery + resync, cursor throttle.
- `node node_modules/vitest/vitest.mjs run src/whiteboard/realtime` → **18 passed**.
- `tsc --noEmit` clean for all new/edited frontend files; `vite build` succeeds.

## Known pre-existing issues (not Phase 5, documented)

1. **Frontend `tsc`/test breakage in `sync.test.ts`** — written against the
   vitest 3 mock API (`vi.fn<Args, Ret>` with two type args); the installed
   vitest is v4.1.11 which takes one. This breaks `tsc --noEmit` and causes 4
   runtime failures in that HTTP-sync test file. It predates Phase 5 and is in
   the Phase 4 HTTP layer (`npm test` shows 4 failed / 59 passed overall, all 4
   in `sync.test.ts`). Recommended fix (separate task): migrate
   `sync.test.ts` to the vitest 4 mock API.
2. **Backend `apps/accounts/models.py` mypy errors** — pre-existing, out of scope.
3. **Test-ordering content-type collision** — running `tests/partnerships` before
   `tests/whiteboard/test_concurrency.py` yields an
   `IntegrityError: duplicate key (admin, logentry)` from serialized_rollback.
   `tests/whiteboard` passes standalone.

## Configuration / infrastructure notes

- **Channel layer:** in-memory for dev/tests. Production must use Redis ≥ 6
  (`channels_redis`); local Redis 3.2 is incompatible with redis-py 8
  (`HELLO` command unsupported) and is documented as unsupported.
- **`WEBSOCKET_ALLOWED_ORIGINS`:** origin allowlist for WebSocket connections.

## Documentation added
- `docs/architecture/websocket-protocol.md`
- `docs/architecture/realtime-collaboration.md`
- `docs/architecture/conflict-strategy.md`
- `docs/architecture/presence.md`
- `docs/architecture/whiteboard-engine.md` — remote apply + version chain updated.

## Deferred (per scope)
- Collaborative undo — Phase 6.
- Offline-first / IndexedDB / background sync — Phase 6.
- CRDT/OT — deliberately not chosen; operational log + row lock + replay (see
  `conflict-strategy.md`).
- Full eraser-path CRDT fidelity across heavily-diverged boards — covered by
  full HTTP resync.