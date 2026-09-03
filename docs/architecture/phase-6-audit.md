# Phase 6 Pre-Implementation Audit

## Phase 0 — PWA Foundation

| Item | Status | Issue | Affects Offline? | Correction | Risk | Required Now |
|------|--------|-------|-------------------|------------|------|--------------|
| Static assets | OK | WhiteNoise serves `/static/dist/` correctly. | No | — | Low | No |
| manifest.json | Partial | Exists at `/static/manifest.json` with name, icons, display mode. Missing `service_worker` field and `id` field. | Yes | Add `service_worker` field after SW implementation. | Low | Yes (part of Phase 6) |
| Service worker | Missing | No `sw.js` exists. No `navigator.serviceWorker` registration. | Yes | Implement service worker with app-shell caching and privacy-aware strategy. | High | Yes |
| Icons | OK | `icon-192.png`, `icon-512.png`, `favicon.svg` all exist. | No | — | Low | No |
| CSP | OK | Base template has CSP nonce support via `{% csp_nonce_attr %}`. `connect-src` includes `ws:` / `wss:`. | Partial | May need to add `worker-src` directive for SW scope. | Medium | Yes (if CSP blocks SW) |
| Environment config | OK | `.env.example` and settings hierarchy correct. | No | No new env vars needed. | Low | No |

## Phase 1 — Authentication

| Item | Status | Issue | Affects Offline? | Correction | Risk | Required Now |
|------|--------|-------|-------------------|------------|------|--------------|
| Session auth | OK | Session cookie authenticates WebSocket and HTTP. | Yes | Session must remain valid for reconnection. No token expiry issues for short offline periods. | Low | No |
| Logout | OK | POST `/accounts/logout/` clears session. | Yes | Must handle logout with pending offline ops (see §42). | Medium | Yes (policy doc) |
| Password change | OK | Rotates session auth hash. Old sessions invalidated. | Yes | If password changes while offline, reconnection will fail auth. Expected behavior. | Low | No |

## Phase 2 — Partnerships

| Item | Status | Issue | Affects Offline? | Correction | Risk | Required Now |
|------|--------|-------|-------------------|------------|------|--------------|
| Membership checks | OK | `can_view_whiteboard` / `can_access_whiteboard` check active membership. | Yes | Must reject offline operations from revoked members on reconnect. Server already does this. | Low | No |
| Revocation | OK | Revoke marks membership ENDED. | Yes | Client must stop syncing when it learns of revocation. New behavior needed. | Medium | Yes |
| Invitation flow | OK | No offline concerns. | No | — | Low | No |

## Phase 3 — Canvas Engine

| Item | Status | Issue | Affects Offline? | Correction | Risk | Required Now |
|------|--------|-------|-------------------|------------|------|--------------|
| Pure modules | OK | `state.ts`, `tools.ts`, `geometry.ts`, `viewport.ts`, `color.ts` are DOM-free. | No | — | Low | No |
| `Op` model | OK | `add`, `remove`, `clear` are reversible and serializable. | Yes | Good foundation for offline ops. `Op` carries enough data to reconstruct. | Low | No |
| Undo/redo | OK | Local only (past/future stacks). | Yes | Undo of an uncommitted offline op should cancel the pending submission. | Medium | Yes (consider) |
| Engine `onServerOperation` | OK | Clean seam between canvas and persistence. Engine never makes HTTP calls. | Yes | Offline queue plugs in at this seam. | Low | No |

## Phase 4 — Persistence

| Item | Status | Issue | Affects Offline? | Correction | Risk | Required Now |
|------|--------|-------|-------------------|------------|------|--------------|
| `WhiteboardOperation` model | OK | Has `operation_id` (UUID), `sequence`, `base_version`, `resulting_version`, `operation_type`, `payload`. | Yes | `operation_id` is the idempotency key that survives offline retries. | Low | No |
| Idempotency | OK | DB unique constraint `(whiteboard, operation_id)` + service layer check. | Yes | Critical for offline retry safety. Already solid. | Low | No |
| Version / sequence | OK | `whiteboard.version` is monotonic, incremented under row lock. | Yes | `base_version` validation prevents stale offline writes from corrupting state. | Low | No |
| `SyncManager` (in-memory) | Partial | Queue lives only in memory. Browser crash = lost pending ops. | Yes | Replace with IndexedDB-backed queue. | High | Yes |
| HTTP API endpoints | OK | POST operations, GET state, GET operations list. All use session auth. | Yes | HTTP fallback for sync when WebSocket is unavailable. | Low | No |
| `WhiteboardRepository` | OK | Clean HTTP adapter with CSRF handling. | Yes | Remains the HTTP transport for sync engine. | Low | No |

## Phase 5 — Real-time Collaboration

| Item | Status | Issue | Affects Offline? | Correction | Risk | Required Now |
|------|--------|-------|-------------------|------------|------|--------------|
| `WebSocketTransport` | OK | Auto-reconnect with exponential backoff, heartbeat, dead connection detection. | Yes | Must integrate with network state monitor. | Medium | Yes (integrate) |
| `RealtimeController` | OK | State machine, version chaining, optimistic submit, echo dedup. | Yes | Must coordinate with sync engine for pending ops. | High | Yes (integrate) |
| Presence | OK | Cursor broadcast, join/leave. | No | Presence naturally degrades offline. | Low | No |
| Reconnection | Partial | Client reconnects and requests `sync.ops` from `connection.ready`. | Yes | Must also submit pending offline ops after sync. | High | Yes |
| `STALE_VERSION` handling | OK | Clears pending, resyncs. | Yes | Must persist the pending ops to IndexedDB before discarding in-memory state. | High | Yes |
| Conflict strategy | Partial | Server-authoritative with STALE_VERSION rejection. No client-side rebase. | Yes | For additive `create_stroke` ops, a simple rebase strategy may be possible. | Medium | Yes (design) |

## Issues Discovered

### 1. No durable operation queue (Critical)
- **Problem**: `SyncManager._pendingOps` is an in-memory array.
- **Why it affects offline**: Browser crash or tab close = lost pending operations.
- **Correction**: Persist pending operations to IndexedDB before considering them queued.
- **Risk**: High — directly violates the core offline invariant.
- **Required now**: Yes.

### 2. No service worker (Critical)
- **Problem**: No SW exists. App cannot load offline.
- **Why it affects offline**: Opening the app without network shows a blank page.
- **Correction**: Implement SW with app-shell caching strategy.
- **Risk**: High — blocks the entire PWA offline shell.
- **Required now**: Yes.

### 3. No network state awareness (High)
- **Problem**: `navigator.onLine` not used. No connectivity detection.
- **Why it affects offline**: No way to know when to queue vs submit, or when to switch to offline mode.
- **Correction**: Implement network state monitor using WebSocket state + `navigator.onLine` + fetch failures.
- **Risk**: High — user cannot tell if they are offline.
- **Required now**: Yes.

### 4. No offline-to-online synchronization (High)
- **Problem**: When WebSocket reconnects, pending ops are not automatically submitted.
- **Why it affects offline**: Operations created offline remain stuck unless user manually retries.
- **Correction**: Sync engine must detect reconnection and flush pending queue.
- **Risk**: High — operations may never sync.
- **Required now**: Yes.

### 5. No undo/redo coordination with persistence (Medium)
- **Problem**: Undoing an offline op does not cancel the pending server submission.
- **Why it affects offline**: Server receives an op that was locally undone.
- **Correction**: Cancel pending ops on undo if they haven't been submitted yet.
- **Risk**: Medium — undo works locally but server state diverges.
- **Required now**: No (can document as known limitation).

### 6. `sync.test.ts` vitest 4 API mismatch (Low)
- **Problem**: `vi.fn<Args, Ret>` two-arg type syntax from vitest 3.
- **Why it affects offline**: Blocks `tsc --noEmit` and 4 test failures.
- **Correction**: Migrate to vitest 4 single-arg mock type.
- **Risk**: Low — no runtime impact on production.
- **Required now**: Yes (pre-existing fix).

## Governing Decisions for Phase 6

1. **Server remains authoritative** — all offline operations are revalidated on the server.
2. **No CRDT/OT** — simple additive rebase for `create_stroke`, rejection for destructive ops.
3. **IndexedDB as sole local store** — no localStorage, no canvas screenshots.
4. **Operations, not snapshots** — the logical operation model persists, not pixel data.
5. **Service worker caches app shell only** — private API responses are not cached.
6. **No new backend models** — Phase 6 is primarily a frontend persistence change.
7. **No new infrastructure** — no Celery, no Kafka, no separate services.
8. **Graceful degradation** — offline is a feature, not a fallback; the UX adapts.
