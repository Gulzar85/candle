# Phase 5 — Pre-Implementation Audit (Phases 0–4)

**Status:** Readiness review for real-time collaboration (Django Channels + WebSockets).

Audit lens: *"Does the existing Phase 0–4 design support low-latency, server-authoritative,
concurrent, recoverable collaboration — and what must change to close the gaps?"*

---

## 1. Phase 0 — Foundation

| Area | Findings | Phase 5 relevance | Action |
|---|---|---|---|
| Settings architecture | `config/settings/{base,development,production}.py`; `DJANGO_SETTINGS_MODULE` injected via env and defaulted in `asgi.py` to `development`. `base.py` holds all shared config. | Sound; add WebSocket settings in `base`, override per-env. | Add |
| ASGI | `config/asgi.py` uses `ProtocolTypeRouter` with only `"http"`; WebSocket branch is a **commented stub**. `daphne` is first in `INSTALLED_APPS`; `ASGI_APPLICATION` set. | Must wire `websocket` → `AuthMiddlewareStack` → `URLRouter`. | Fix now |
| Redis | `base.py` sets `CHANNEL_LAYERS` to `channels_redis.core.RedisChannelLayer`, `CACHES` to `RedisCache` at `REDIS_URL`. | **Environment defect:** the local Redis server is v3.2 (Windows) which rejects the `HELLO` command redis-py 8.x / channels-redis 4.3 send. `RedisChannelLayer.group_send` raises `ResponseError: unknown command 'HELLO'`. | Dev: use **in-memory** channel layer; production: require Redis ≥ 6 and document. |
| Security / CSP | `SECURE_CSP` sets `connect-src 'self' ws: wss:` and cross-origin relaxed in dev. `SESSION_COOKIE_HTTPONLY`, `CSRF_COOKIE_HTTPONLY`, SameSite Lax baseline. | `connect-src` already permits WebSockets. | Keep |
| Environment | `.env.example` has `REDIS_URL` but no WebSocket origin setting; no `DJANGO_DEBUG` default issue (defaults False). | Add `WEBSOCKET_ALLOWED_ORIGINS`. | Add |
| Logging | `LOGGING` has `apps` logger at DEBUG in dev. | Reuse for structured collaboration logs. | Extend |

## 2. Phase 1 — Accounts

| Area | Findings | Phase 5 relevance | Action |
|---|---|---|---|
| Auth | Email/password custom `User`, session-based, `verify_email` gating, `login_required`, CSRF meta-tag in `base.html`. | WebSockets must inherit the session. | Reuse |
| User identity | **`User` has NO `public_id` UUID** while `Partnership`, `Whiteboard`, `PartnershipInvitation` all expose one. Presence/actor messages need a stable non-`pk` public identifier. | Exposing `pk` in presence/actor messages risks leaking internal ids in the (authorized) group. | **Add `User.public_id`** (justified, matches domain convention) |
| Session invalidation | `SESSION_COOKIE_HTTPONLY`, session-based auth. Logout rotates session. | Channels `AuthMiddlewareStack` must read sessions (database-backed). | Verify |

## 3. Phase 2 — Partnerships (the security boundary)

| Area | Findings | Phase 5 relevance | Action |
|---|---|---|---|
| `Partnership` | ACTIVE/PENDING/ENDED; `is_active` property. | WebSocket connect must require an active partnership. | Reuse |
| `PartnershipMember` | role/status; DB-unique "one active membership per user" + "≤ 2 active". | Membership + ACTIVE status gates access. | Reuse |
| `can_access_whiteboard(user, partnership)` | Centralized predicate → `is_active_member`. | Single authorization source for consumers; do **not** redefine. | Reuse (per Phase 4) |
| Revocation | No push mechanism today; a revoked member's *open HTTP pages* keep working until they act. | **Open WebSockets would retain access after revocation.** Requirement: revoked users must not keep live access. | Consumer re-authorizes per-message; plus document access-revocation strategy. |
| `safe_display_name` | Return display-safe name (never leaks email). | Presence payloads must use this. | Reuse |

## 4. Phase 3 — Canvas Engine (frontend)

| Area | Findings | Phase 5 relevance | Action |
|---|---|---|---|
| State model | `state.ts` — ordered `strokes`, commit/undo/redo via `Op` (`add|remove|clear`), no DOM. | Reusable; need a **remote-op apply** that does not pollute the local undo stack deterministically. | Extend `state.ts` |
| Engine | `engine.ts` implements `Host`, commits local ops, emits server envelopes via `onServerOperation`. Has `loadServerState`, `setServerVersion`, `serverVersion`. | Reusable. Missing: apply *remote committed* ops; reconcile optimistic vs committed. | Extend |
| **Multi-op base_version bug** | `_emitServerOps` emits several ops (erase → `remove` + N `add`s; `clear`; multi-stroke) all tagged with the **same** `base_version = _serverVersion` and fresh `operation_id`s. Server persists one op per version, so only the **first** op in such a group succeeds; the rest get `STALE_VERSION`. | Breaks collaboration for erase/clear/multi strokes over any transport. | **Fix now** (server remains correct; client must submit a version chain) |
| Transport seam | `WhiteboardRepository` (HTTP only) + `SyncManager` (queue/retry). | Keep; add `RealtimeTransport` abstraction. | Extend |
| Remote cursors | `#wb-hover-avatar` element exists but is static (no live pointer). | Optional presence polish. | Lightweight |

## 5. Phase 4 — Persistence

| Area | Findings | Phase 5 relevance | Action |
|---|---|---|---|
| `Whiteboard` | lazy, `version`, `status`, `last_operation_at`, `archived_at`, `public_id`. | Version/status are the realtime baseline and gates. | Reuse |
| `WhiteboardOperation` | immutable ledger; `(whiteboard, sequence)` and `(whiteboard, operation_id)` unique; `indexes` on `(wb, sequence)`, `(wb, operation_type)`, `(wb, created_at)`. | Sequence-indexed sync queries already efficient. | Reuse |
| `WhiteboardOperationService` | Sync, transactional, `select_for_update`, validates, versions, sequences, idempotent by `operation_id`. | **The single write path.** WebSockets must route through it (never duplicate persistence in consumer). | Reuse |
| HTTP API | `OperationSubmitView`, `WhiteboardStateView`, `OperationListView` (keyed by partnership `public_id`); `SessionAuthentication` + rate limiting via `apps.accounts.ratelimit`. | Keep as fallback sync; WebSocket reuses the same service. | Reuse |
| **public_id mismatch bug** | Page URL / API resolve by **partnership** `public_id`, but `whiteboard.html` sets `data-wb-api-base` / `data-wb-public-id` to **`whiteboard.public_id`** (a *different* UUID). At runtime the frontend would hit a "not found" endpoint. | Blocks realtime + HTTP persistence in the browser. | **Fix now** (use `partnership.public_id`) |
| Errors | `errors.py` → stable codes + HTTP status; `StaleVersionError` carries `current_version`/`client_version`; duplicates return ack with `duplicate: true` (not an error). | Map to a WebSocket error-code vocabulary; add codes for `INVALID_MESSAGE`, `SYNC_REQUIRED`, `AUTH_REQUIRED`, `WHITEBOARD_ARCHIVED`, `OPERATION_ID_REUSE`, `RATE_LIMITED`, `PAYLOAD_TOO_LARGE`. | Extend |
| Limits | `limits.py` (`MAX_STROKE_POINTS`, `MAX_OPERATION_PAYLOAD_BYTES`, `MAX_BATCH_OPERATIONS`, `MAX_REQUEST_BODY_BYTES`). Read at import time (module-level) → `override_settings` does not affect them (was flagged in Phase 4). | Keep as-is for validation; add per-message WebSocket size/rate limits in consumer. | Add WS-layer limits |

## Cross-cutting problems to fix in Phase 5

1. **Redis 3.2 / redis-py 8 `HELLO` incompatibility** → in-memory channel layer for dev+tests; production requires modern Redis.
2. **ASGI WebSocket routing stub** → wire `websocket` protocol.
3. **`User.public_id` missing** → add stable public actor identity for presence/actor messages.
4. **`whiteboard.html` public_id mismatch** → key everything by partnership `public_id`.
5. **Client multi-op base_version chain** → client must sequence base versions across sub-operations of a single gesture.
6. **Revocation of open WebSockets** → re-authorize per message + document access-revocation; no silent indefinite retention.
7. **Remote-op application seam** → add deterministic apply for committed remote ops; reconcile optimistic vs committed by `operation_id`.

## Explicitly deferred (per Phase 4/5 scope)

- CRDT/OT/Yjs — deferred intentionally; operation ledger + deterministic replay + re-sync chosen.
- IndexedDB / offline-first / background sync — Phase 6.
- Native mobile apps — later.
- Collaborative undo semantics — Phase 5 uses "undo as a new operation"; full conflict-safe undo deferred and documented.
