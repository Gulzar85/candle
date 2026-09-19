# Phase 8 — Production Audit

**Date:** 2026-09-02
**Status:** Complete (read-only, no changes made)
**Scope:** Full repository inspection for production readiness

This audit covers every subsystem of the Candle application as it exists today.
Findings are classified by severity and grouped by domain. Implementation does
not begin until this document is reviewed and the implementation plan is finalized.

---

## Severity legend

| Label | Meaning |
|---|---|
| **CRITICAL** | Must fix before any production deployment. Security vulnerability, data-loss risk, or total service failure. |
| **HIGH** | Must fix before production. Significant security gap, reliability risk, or correctness issue. |
| **MEDIUM** | Should fix before production. Hardening that reduces attack surface or operational risk. |
| **LOW** | Nice to fix. Cleanup, defense-in-depth, or minor hardening. |
| **INFORMATIONAL** | No action required now. Documented for awareness. |

---

## 1. Django Settings & Environment Configuration

### Findings

| # | Severity | Finding | Location |
|---|---|---|---|
| S-1 | **HIGH** | `SECRET_KEY` has a hardcoded insecure fallback `"change-me-in-production"` in base settings. Only `production.py` re-validates it — if `DJANGO_SETTINGS_MODULE` is misconfigured, the server runs with a known leaked key. | `config/settings/base.py:9`, `config/settings/production.py:9-12` |
| S-2 | **HIGH** | `config/asgi.py` and `config/wsgi.py` both default to `config.settings.development` if `DJANGO_SETTINGS_MODULE` is unset (`os.environ.setdefault`). A production server that forgets the env var silently boots with `DEBUG=True` and `ALLOWED_HOSTS=["*"]`. | `config/asgi.py:16`, `config/wsgi.py:7` |
| S-3 | **HIGH** | `CSRF_TRUSTED_ORIGINS` is parsed from env but **not enforced** in production. `production.py` validates `WEBSOCKET_ALLOWED_ORIGINS` (raises if empty) but not `CSRF_TRUSTED_ORIGINS`. Without `CSRF_TRUSTED_ORIGINS`, same-site CSRF checks use only `Referer` — which may fail behind certain proxies or be stripped. | `config/settings/base.py:19-21`, `config/settings/production.py` (absent) |
| S-4 | **MEDIUM** | No `config/settings/test.py` module. Tests run against `config.settings.development` (pyproject.toml:79), which sets `DEBUG=True`, `ALLOWED_HOSTS=["*"]`, and `SECURE_CSP=None`. Tests do not exercise production security settings, so regressions in production hardening go undetected. | `pyproject.toml:79` |
| S-5 | **MEDIUM** | Missing production security settings: `SECURE_REFERRER_POLICY` (defaults to `"no-referrer-when-downgrade"` — should be `strict-origin-when-cross-origin` or `no-referrer`), `SESSION_COOKIE_AGE` (defaults to 2 weeks), `SESSION_EXPIRE_AT_BROWSER_CLOSE` (defaults to `False`), `DATA_UPLOAD_MAX_MEMORY_SIZE` / `DATA_UPLOAD_MAX_NUMBER_FIELDS` (Django defaults 2.5 MB / 1000 fields). | `config/settings/production.py` (absent) |
| S-6 | **LOW** | `ALLOWED_HOSTS` in production parses to an empty list if `DJANGO_ALLOWED_HOSTS` is unset, which correctly prevents startup — but the error is a generic 500 (ImproperlyConfigured), not a clear startup message. Acceptable. | `config/settings/production.py:14-16` |
| S-7 | **INFORMATIONAL** | `PASSWORD_HASHERS` not explicitly set — uses Django defaults (PBKDF2). Argon2 is preferred for production but not required. | `config/settings/base.py` (absent) |

### Current production security settings (what IS set)

| Setting | Value | Source |
|---|---|---|
| `DEBUG` | `False` | `production.py:7` |
| `SECURE_SSL_REDIRECT` | `True` | `production.py:27` |
| `SECURE_HSTS_SECONDS` | `31536000` (1 year) | `production.py:28` |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | `production.py:29` |
| `SECURE_HSTS_PRELOAD` | `True` | `production.py:30` |
| `SESSION_COOKIE_SECURE` | `True` | `production.py:31` |
| `CSRF_COOKIE_SECURE` | `True` | `production.py:32` |
| `SESSION_COOKIE_HTTPONLY` | `True` | `base.py:182` |
| `CSRF_COOKIE_HTTPONLY` | `True` | `base.py:183` |
| `SESSION_COOKIE_SAMESITE` | `"Lax"` | `base.py:184` |
| `CSRF_COOKIE_SAMESITE` | `"Lax"` | `base.py:185` |
| `X_FRAME_OPTIONS` | `"DENY"` | `production.py:35` |
| `SECURE_CONTENT_TYPE_NOSNIFF` | `True` | `production.py:36` |
| `SECURE_BROWSER_XSS_FILTER` | `True` | `production.py:37` |
| `SECURE_PROXY_SSL_HEADER` | `("HTTP_X_FORWARDED_PROTO", "https")` | `production.py:33` |
| `WEBSOCKET_ALLOWED_ORIGINS` | env-required | `production.py:21-25` |
| `CONN_MAX_AGE` | `600` | `production.py:47` |
| CSP | Strong (see base.py:198-213) | `base.py:198-213` |

### What is NOT set (gaps requiring action)

| Setting | Recommended | Impact |
|---|---|---|
| `SECURE_REFERRER_POLICY` | `"strict-origin-when-cross-origin"` | Leaks full URL in Referer header to third parties |
| `SESSION_COOKIE_AGE` | `3600` (1 hour) or `86400` (1 day) | 2-week default session lifetime is excessive |
| `SESSION_EXPIRE_AT_BROWSER_CLOSE` | Consider `True` | Persistent sessions on shared devices |
| `CSRF_TRUSTED_ORIGINS` | Required env var (enforce in production.py) | Referer-based CSRF validation may fail behind proxies |
| `DATA_UPLOAD_MAX_MEMORY_SIZE` | `5242880` (5 MB) | Explicit cap (Django default is 2.5 MB — already adequate but should be explicit) |
| `DATA_UPLOAD_MAX_NUMBER_FIELDS` | `1000` (explicit) | Already adequate default; being explicit documents the intent |

---

## 2. Authentication Security

### Password & Token Security

| # | Severity | Finding | Location |
|---|---|---|---|
| A-1 | **MEDIUM** | Password reset does **not** invalidate existing sessions. After a password reset, previous sessions (including potentially compromised ones) remain valid. Only password *change* rotates the session hash (`update_session_auth_hash`). | `apps/accounts/views.py:210-235` (uses Django's built-in `PasswordResetView` which doesn't call `session.flush()`) |
| A-2 | **MEDIUM** | No "logout all devices" feature. Users have no way to revoke all active sessions for their account. | Not implemented |
| A-3 | **LOW** | `PasswordResetConfirmView` is **not rate-limited**. The initial `PasswordResetView.post` is rate-limited (3/900s), but the confirmation view (entering the token + new password) is not. An attacker with a valid token (unlikely given SHA-256 + time-limited tokens, but defense-in-depth) could brute-force the new password. | `apps/accounts/views.py` (no ratelimit on PasswordResetConfirmView) |
| A-4 | **INFORMATIONAL** | Verification tokens: single-use (consumed = deleted), time-limited (24h), SHA-256 digests stored (raw tokens never persisted). Password reset uses Django's signed tokenization (3-day default timeout). Both are well-implemented. | `apps/accounts/models.py:200-248`, `apps/accounts/models.py:143-161` |
| A-5 | **INFORMATIONAL** | Rate limiting is present for: login (6/300s, keyed IP+email), register (5/3600s, keyed IP), password_reset (3/900s, keyed IP+email), email resend (3/900s, keyed IP+email). Custom cache-backed sliding counter, not a third-party package. | `apps/accounts/ratelimit.py:35`, `apps/accounts/views.py:65-70` |
| A-6 | **INFORMATIONAL** | No password/token logging found. `core/emailing.py:27-28` explicitly documents "no token, password or message body is ever logged." WS consumer logs public_ids, not payloads/tokens. Partnership tokens stored as SHA-256 digests only. | `apps/core/emailing.py:27-28` |

### Session Security

| # | Severity | Finding | Location |
|---|---|---|---|
| A-7 | **MEDIUM** | Session cookie `AGE` not overridden — defaults to 2 weeks (1,209,600 seconds). For a private whiteboard app, shorter sessions (e.g., 1 day) are more appropriate. | `config/settings/base.py` (absent) |
| A-8 | **LOW** | `LOGOUT_REDIRECT_URL = "core:home"` — logout redirects to home page. Acceptable, but no confirmation message or "you have been logged out" page. | `config/settings/base.py:44` |

### Password Validation

Present and correct (base.py:126-131): `UserAttributeSimilarityValidator`, `MinimumLengthValidator`, `CommonPasswordValidator`, `NumericPasswordValidator`. All four Django defaults are active.

---

## 3. Partnership & Object-Level Authorization

### Summary: STRONG

Authorization is uniformly enforced across all access surfaces (page, HTTP API, WebSocket). Every protected resource checks `is_active_member(user, partnership)` AND `partnership.is_active`. Server-side checks are never bypassed by frontend routing or hidden fields.

| # | Severity | Finding | Location |
|---|---|---|---|
| P-1 | **INFORMATIONAL** | Authorization flow: URL carries partnership `public_id` → resolve partnership → verify `is_active` → verify membership (`can_view_whiteboard` = `is_active_member`) → resolve whiteboard. Applied consistently in all three surfaces. | `apps/whiteboard/api.py:59-73`, `apps/whiteboard/consumers.py:363-379`, `apps/whiteboard/views.py:33-49` |
| P-2 | **INFORMATIONAL** | Per-message re-authorization on WebSocket (`_still_authorized` at consumers.py:381-391) rechecks membership, `is_active`, and `is_archived` on every message. A revoked member can't keep writing through an open socket. | `apps/whiteboard/consumers.py:381-391` |
| P-3 | **INFORMATIONAL** | Partnership uniqueness enforced at DB level: partial unique indexes + PL/pgSQL trigger (`enforce_partnership_member_rules`). Concurrent accepts race-safe via `select_for_update`. | `apps/partnerships/migrations/0002_parternship_db_constraints.py`, `apps/partnerships/services.py:478-547` |
| P-4 | **INFORMATIONAL** | Invitation tokens stored as SHA-256 digests; raw tokens only in email. DB constraint prevents duplicate active invitations per (partnership, email) or (inviter, email). | `apps/partnerships/models.py:189-203` |
| P-5 | **INFORMATIONAL** | Whiteboard isolation: `public_id` is a 128-bit UUID (unguessable), and even with it, active membership is required. Non-members get 403. UUIDs are not a secret — authorization is the actual gate. | `apps/whiteboard/api.py:66`, `apps/whiteboard/consumers.py:370` |
| P-6 | **INFORMATIONAL** | No public profile endpoint exists. Cross-user profile data is limited to partner's display name (visible only through authorized membership). Invitation landing shows only `safe_display_name`. | `apps/accounts/views.py`, `apps/partnerships/policies.py:28-38` |

---

## 4. WebSocket Security

### Summary: STRONG with one gap

| # | Severity | Finding | Location |
|---|---|---|---|
| W-1 | **MEDIUM** | **No per-user or per-board connection limit.** An authenticated user can open unlimited simultaneous WebSocket connections to the same board group, each passing `_authorize`. This amplifies broadcast fan-out in `whiteboard_op`/`whiteboard_presence` and could be used for resource exhaustion. Per-connection rate limits (600 msgs/60s, 120 submits/60s) don't prevent this because each connection has its own counter. | `apps/whiteboard/consumers.py:67-124` (no connection count check) |
| W-2 | **INFORMATIONAL** | Origin validation: browser connections require `Origin` in `WEBSOCKET_ALLOWED_ORIGINS`; non-browser connections (no Origin) permitted only when the allowlist is empty. Production requires the allowlist to be set (raises `ImproperlyConfigured` if empty). | `apps/whiteboard/consumers.py:79-90`, `config/settings/production.py:21-25` |
| W-3 | **INFORMATIONAL** | Message size capped at 250 KB (`MAX_MESSAGE_BYTES`). Operation payload capped at 100 KB (`MAX_OPERATION_PAYLOAD_BYTES`). Unknown message types rejected. JSON shape validated. | `apps/whiteboard/realtime.py:72-75`, `apps/whiteboard/consumers.py:177-202` |
| W-4 | **INFORMATIONAL** | Per-connection rate limiting: 600 general messages / 60s, 120 operation submits / 60s. In-memory sliding window per connection. | `apps/whiteboard/consumers.py:182-186,216`, `apps/whiteboard/realtime.py:80-83` |
| W-5 | **INFORMATIONAL** | Cursor presence values (`x`, `y`) are broadcast without type validation beyond the per-connection rate limit. Impact is limited to partners on the same board. | `apps/whiteboard/consumers.py:333-344` |

---

## 5. Operation Security & Data Integrity

### Summary: STRONG

| # | Severity | Finding | Location |
|---|---|---|---|
| O-1 | **MEDIUM** | **HTTP operation submission has no request body size guard.** `limits.py:30` defines `MAX_REQUEST_BODY_BYTES = 250_000` but it is never enforced. `api.py:133` does `json.loads(request.body)` with no body-size check. A batch of 50 operations at 100 KB each = ~5 MB request body. The WebSocket path correctly enforces a total frame cap (250 KB). | `apps/whiteboard/limits.py:30` (dead constant), `apps/whiteboard/validator.py:65` (comment references non-existent `RequestSizeGuard`), `apps/whiteboard/api.py:133` |
| O-2 | **LOW** | Dead constant `MAX_PAYLOAD_BYTES = 200_000` in `models.py:25` is never referenced. The actual enforcement is `limits.MAX_OPERATION_PAYLOAD_BYTES = 100_000`. Confusing duplicate. | `apps/whiteboard/models.py:25` |
| O-3 | **INFORMATIONAL** | Server is authoritative for: sequence (`sequence = expected + 1`), version (`resulting_version = sequence`), actor (passed from auth, never from payload), timestamps (`timezone.now()`), idempotency (DB unique constraint on `(whiteboard, operation_id)`). Stale version → `409 STALE_VERSION`. Invalid types/payloads rejected before lock is taken. | `apps/whiteboard/service.py:106-192` |
| O-4 | **INFORMATIONAL** | Operation validation: type must be known enum, payload must be dict, forbidden server-authoritative fields blocked, `operation_id` parsed as UUID, `base_version` validated as non-negative int, per-type schema enforced (stroke points count/cap/geometry, color regex, width/opacity ranges). | `apps/whiteboard/validator.py:36-163` |
| O-5 | **INFORMATIONAL** | Batch atomicity: stale version in any operation rolls back the whole batch (transaction.atomic + select_for_update). | `apps/whiteboard/service.py:129`, `tests/whiteboard/test_concurrency.py:246` |

---

## 6. Input Validation

### Summary: GOOD with one gap (see O-1 above)

| Surface | Validation | Location |
|---|---|---|
| Operation payloads | Strict schema (type, UUID, int, dict, ranges, regex) | `apps/whiteboard/validator.py` |
| Board titles | Trimmed, non-blank, max_length from model field (120) | `apps/whiteboard/service.py:209-224` |
| Invitation emails | EmailField(max_length=254), lowercased/stripped | `apps/partnerships/forms.py:12-34` |
| Password forms | Django's built-in validation + 4 validators | `config/settings/base.py:126-131` |
| WS messages | JSON parse, type check, size cap, schema delegation | `apps/whiteboard/consumers.py:175-202` |
| HTTP POST body | **No explicit size guard** (gap — see O-1) | `apps/whiteboard/api.py:133` |

---

## 7. XSS / HTML Security

### Summary: STRONG

| # | Severity | Finding | Location |
|---|---|---|---|
| X-1 | **INFORMATIONAL** | Zero `innerHTML`, `outerHTML`, `insertAdjacentHTML`, or `document.write` calls in all TypeScript source. All DOM text set via `.textContent` or `.value`. Django autoescaping active on all templates. No `|safe` on user content. | All `frontend/src/**/*.ts`, all `templates/**/*.html` |
| X-2 | **INFORMATIONAL** | CSP policy: `script-src 'self' <nonce> 'unsafe-eval'` (unsafe-eval required by Alpine.js — documented). `style-src 'self' <nonce>` (no `unsafe-inline`). `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`, `base-uri 'self'`. Production CSP is strong. Development disables it entirely. | `config/settings/base.py:198-213`, `config/settings/development.py:8` |
| X-3 | **INFORMATIONAL** | Board titles flow: `{{ whiteboard.title }}` (Django autoescaped) → `data-wb-title` attribute → `dataset.wbTitle` (TS reads as string) → `.textContent` (safe DOM assignment). No injection vector. | `templates/whiteboard/whiteboard.html:52`, `frontend/src/whiteboard/index.ts:363-408` |

---

## 8. CSRF Review

### Summary: STRONG

| # | Severity | Finding | Location |
|---|---|---|---|
| C-1 | **INFORMATIONAL** | No `csrf_exempt` or `csrf_protect` decorators found anywhere. All state-changing HTTP endpoints go through Django's `CsrfViewMiddleware`. DRF `SessionAuthentication` enforces CSRF on API views. | Grep across entire codebase: zero matches for csrf_exempt |
| C-2 | **INFORMATIONAL** | CSRF token delivery: HTML meta tag (`base.html:9`), HTMX auto-attach (`hx-headers` on `<body>`, `base.html:32`), fetch API reads meta tag (`repository.ts:89-92`). Cookie is HttpOnly+Secure+Lax in production. | `templates/base.html:9,32`, `frontend/src/whiteboard/repository.ts:89-98` |
| C-3 | **INFORMATIONAL** | WebSocket authentication uses session cookie (via `AuthMiddlewareStack`), not CSRF tokens — correct, as CSRF doesn't apply to WebSocket upgrades. Origin allowlist prevents cross-origin WS connections. | `config/asgi.py:27`, `apps/whiteboard/consumers.py:79-90` |

---

## 9. Rate Limiting & Abuse Protection

### Summary: GOOD, well-distributed

| Surface | Rate Limit | Key | Location |
|---|---|---|---|
| Login | 6 attempts / 300s | IP + email | `apps/accounts/views.py:65` |
| Register | 5 / 3600s | IP | `apps/accounts/views.py:66` |
| Password reset | 3 / 900s | IP + email | `apps/accounts/views.py:67` |
| Email resend | 3 / 900s | IP + email | `apps/accounts/views.py:68` |
| Partnership invite | 5 / 3600s | user | `apps/partnerships/views.py:31-41` |
| Partnership invite resend | 5 / 3600s | user | same |
| Partnership invite accept | 10 / 900s | user | same |
| Partnership invite reject | 10 / 900s | user | same |
| Partnership invite revoke | 5 / 3600s | user | same |
| Whiteboard HTTP submit | 60 / 60s | user | `apps/whiteboard/api.py:46-49` |
| WS general messages | 600 / 60s | per-connection | `apps/whiteboard/consumers.py:183` |
| WS operation submits | 120 / 60s | per-connection | `apps/whiteboard/consumers.py:216` |

| # | Severity | Finding | Location |
|---|---|---|---|
| R-1 | **LOW** | No global API rate limit (only per-endpoint). Acceptable for current scope but would benefit from a general throttle in `REST_FRAMEWORK` settings for future API expansion. | `config/settings/base.py:187-196` (absent) |
| R-2 | **LOW** | WS rate limits are per-connection (in-memory), not per-user. A user opening multiple connections gets N × the rate limit. (Same root cause as W-1.) | `apps/whiteboard/consumers.py:92-98` |

---

## 10. Request & Payload Size Limits

| # | Severity | Finding | Location |
|---|---|---|---|
| SZ-1 | **MEDIUM** | `DATA_UPLOAD_MAX_MEMORY_SIZE` not set (Django default: 2.5 MB). Adequate but should be explicit. `DATA_UPLOAD_MAX_NUMBER_FIELDS` not set (default: 1000). Adequate but should be explicit. | `config/settings/base.py` (absent) |
| SZ-2 | **MEDIUM** | HTTP operation batch has no enforced total body size limit. Per-operation cap is 100 KB; max batch is 50; theoretical max request body ≈ 5 MB. WS path correctly caps at 250 KB. (Same as O-1.) | `apps/whiteboard/api.py:133`, `apps/whiteboard/limits.py:30` |
| SZ-3 | **INFORMATIONAL** | WS frame cap: 250 KB. WS operation payload: 100 KB. Avatar upload: 2 MB. All explicitly enforced. | `apps/whiteboard/realtime.py:72-75`, `apps/accounts/avatars.py:18` |

---

## 11. Database Security & Integrity

### PostgreSQL Configuration

| # | Severity | Finding | Location |
|---|---|---|---|
| DB-1 | **MEDIUM** | `POSTGRES_PASSWORD` in base settings defaults to `""` (empty string). If unset in any environment, Django connects without a password. Production should fail loudly if no password is set. (Currently `production.py` doesn't validate database credentials.) | `config/settings/base.py:86` |
| DB-2 | **INFORMATIONAL** | PostgreSQL is the only configured database engine (`django.db.backends.postgresql`). No SQLite fallback. `CONN_MAX_AGE=600` in production for connection pooling. | `config/settings/base.py:83`, `config/settings/production.py:47` |
| DB-3 | **INFORMATIONAL** | `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"` — consistent. | `config/settings/base.py:176` |

### Model Constraints & Indexes

| # | Severity | Finding | Location |
|---|---|---|---|
| DB-4 | **LOW** | Redundant index: `Whiteboard.status` has both `db_index=True` (field kwarg) AND an explicit `idx_wb_status` Meta.index. Two indexes on the same column — waste of write/storage. | `apps/whiteboard/models.py:64,76-78` |
| DB-5 | **LOW** | Redundant index: `WhiteboardOperation (whiteboard, sequence)` explicit Meta.index duplicates the backing unique index of `uniq_op_sequence_per_board` unique constraint. | `apps/whiteboard/models.py:140-143,151` |
| DB-6 | **INFORMATIONAL** | DB-level uniqueness enforced for: `Whiteboard.public_id` (unique), `WhiteboardOperation (whiteboard, operation_id)` (unique), `WhiteboardOperation (whiteboard, sequence)` (unique), `PartnershipMember (partnership, user)` (unique), `PartnershipMember user WHERE status=ACTIVE` (partial unique), `PartnershipInvitation (inviter, invitee_email) WHERE status=SENT` (partial unique), `PartnershipInvitation (partnership, invitee_email) WHERE status=SENT` (partial unique), `User.email` (unique), `User.public_id` (unique). | Various models.py + migrations |
| DB-7 | **INFORMATIONAL** | PL/pgSQL trigger `enforce_partnership_member_rules` enforces: max 2 active members per partnership, max 1 active membership per user. Second backstop beyond the partial unique index. | `apps/partnerships/migrations/0002_parternship_db_constraints.py:8-78` |

---

## 12. Database Performance

| # | Severity | Finding | Location |
|---|---|---|---|
| DP-1 | **MEDIUM** | State reconstruction replays ALL operations for a whiteboard in `(whiteboard, sequence)` order. No snapshot/compaction mechanism exists (`MAX_RETAINED_OPERATIONS = 0`). For boards with thousands of operations, initial load and replay will degrade linearly. | `apps/whiteboard/state_reconstruction.py:57-103`, `apps/whiteboard/limits.py:34` |
| DP-2 | **LOW** | Operation queries use `select_related("actor")` where needed. No obvious N+1 issues in the critical paths. | `apps/whiteboard/selectors.py` |
| DP-3 | **INFORMATIONAL** | Indexes on high-cardinality lookup paths: `(whiteboard, sequence)`, `(whiteboard, operation_type)`, `(whiteboard, created_at)`, `(partnership, user, status)`, `(invitee_email, status)`. All appropriate. | Various models.py Meta.indexes |

---

## 13. Whiteboard Scalability

| # | Severity | Finding | Location |
|---|---|---|---|
| SC-1 | **MEDIUM** | No cap on total objects per board. An authenticated partner can create arbitrarily many unique strokes, growing `state.objects` and the `WhiteboardOperation` table unboundedly. | `apps/whiteboard/limits.py` (no MAX_OBJECTS_PER_BOARD), `apps/whiteboard/validator.py` (no object count check) |
| SC-2 | **LOW** | `MAX_STROKE_POINTS = 4_000` per stroke is a good per-operation bound, but the aggregate is unbounded. | `apps/whiteboard/limits.py:24` |
| SC-3 | **INFORMATIONAL** | The operation model supports three types: `create_stroke`, `delete_object`, `clear_canvas`. No text/shape objects exist. Each operation is ~1-5 KB in the database (UUID + metadata + JSON payload). At 10,000 operations ≈ 10-50 MB of table data — manageable for PostgreSQL but replay time grows linearly. | `apps/whiteboard/state_reconstruction.py` |

---

## 14. Redis Review

| # | Severity | Finding | Location |
|---|---|---|---|
| RD-1 | **INFORMATIONAL** | Redis is used for: (1) Channels layer (`channels_redis.core.RedisChannelLayer`) for cross-process WebSocket broadcast, (2) `django.core.cache.backends.redis.RedisCache` for rate limiting + general caching. Both are transient/ephemeral. | `config/settings/base.py:99-106,119-124` |
| RD-2 | **INFORMATIONAL** | No critical permanent whiteboard data is stored in Redis. Authoritative data lives in PostgreSQL. Redis failure means: real-time collaboration stops working (WS messages can't cross processes), rate limiting falls back to in-memory, but confirmed operations are never lost. | Architecture confirmed via code inspection |
| RD-3 | **INFORMATIONAL** | Development overrides Redis with `LocMemCache` (cache) and `InMemoryChannelLayer` (channels), avoiding Redis dependency locally. | `config/settings/development.py:17-32` |

---

## 15. PWA / Service Worker / IndexedDB Security

| # | Severity | Finding | Location |
|---|---|---|---|
| PWA-1 | **MEDIUM** | IndexedDB uses a single shared database (`"candle-whiteboard"`) across all accounts. Isolation is by application-level `owner_key` filtering. `getWhiteboard()` does a direct key lookup without verifying `owner_key` — safe because whiteboard IDs are server-assigned UUIDs, but lacks defense-in-depth. | `frontend/src/whiteboard/idb.ts:13`, `frontend/src/whiteboard/local-store.ts:211-213` |
| PWA-2 | **MEDIUM** | No `clearOwnerPartition()` on logout. Previous user's whiteboard strokes remain in IndexedDB. The logout form is a plain POST with no JS hook. On a shared device, raw data persists in the DB (though not visible through the app's queries due to `owner_key` filtering). A DevTools user could inspect the `candle-whiteboard` database. | `templates/accounts/settings.html:65` (no JS hook), `templates/layouts/shell.html:266` (no JS hook) |
| PWA-3 | **LOW** | Service worker uses `skipWaiting()` + `clients.claim()` — immediate takeover on update. Can cause mid-session asset swap. Acceptable for PWA shell but can cause rare UX glitch. | `static/sw.js:37,53` |
| PWA-4 | **INFORMATIONAL** | SW does NOT cache authenticated API responses (`/api/*` → network-only with offline JSON fallback). Whiteboard data lives in IndexedDB only. No cross-account data leakage via SW cache. | `static/sw.js:65-68` |
| PWA-5 | **INFORMATIONAL** | SW cache versioning: `"candle-v1"` prefix. Old caches cleaned on activate. `update_via_cache: "none"` in manifest ensures browser always checks for SW updates. | `static/sw.js:17-55`, `static/manifest.json:9-13` |
| PWA-6 | **INFORMATIONAL** | `MAX_CACHED_STROKES_PER_BOARD = 20_000`, `MAX_CACHED_BOARDS = 20`, `MAX_PENDING_OPERATIONS = 5_000`. Crashed `SUBMITTING` ops reset to `PENDING` on next open. | `frontend/src/whiteboard/local-store.ts:128-130,444-455` |

---

## 16. Error Handling & Logging

| # | Severity | Finding | Location |
|---|---|---|---|
| E-1 | **MEDIUM** | Health check is superficial — returns `{"status":"ok"}` without checking PostgreSQL or Redis connectivity. A liveness probe doesn't differentiate between "process alive" and "dependencies reachable." | `apps/core/views.py:7-8` |
| E-2 | **MEDIUM** | Logging is console-only, non-structured (plain `StreamHandler` with `[{asctime}] {levelname} {name} {message}` formatter). No JSON structured logging, no correlation/request IDs, no persistent storage, no remote aggregation. Adequate for local dev but insufficient for production observability. | `config/settings/base.py:232-263` |
| E-3 | **LOW** | Error templates exist (400, 403, 404, 405, 429, 500) and are registered as handlers, but they use Django's default handler functions (not custom). The templates exist but their content/format wasn't inspected. | `config/urls.py:7-11`, `templates/errors/` |
| E-4 | **INFORMATIONAL** | No Sentry, structlog, or external error tracking configured. Production would need external error aggregation. | Not present |

---

## 17. Static & Media Files

| # | Severity | Finding | Location |
|---|---|---|---|
| SF-1 | **LOW** | Production `STORAGES["staticfiles"]` uses `CompressedManifestStaticFilesStorage` (WhiteNoise), but `STORAGES["default"]` (media) remains `FileSystemStorage`. Media is served by a reverse proxy in production (comment at `config/urls.py:21-24`), not by WhiteNoise. No validation of media file types beyond Pillow's handling. | `config/settings/production.py:39-45`, `config/settings/base.py:146-148` |
| SF-2 | **INFORMATIONAL** | Media serving is `DEBUG`-only (`config/urls.py:23-24`). Production relies on reverse proxy. `.gitignore` excludes `media/`. | `config/urls.py:23-24` |
| SF-3 | **INFORMATIONAL** | Avatar upload capped at 2 MiB (`apps/accounts/avatars.py:18`). Pillow processes uploaded images. | `apps/accounts/avatars.py:18` |

---

## 18. CI/CD, Docker & Deployment

| # | Severity | Finding | Location |
|---|---|---|---|
| CI-1 | **HIGH** | No CI/CD pipeline exists. No `.github/`, `.gitlab-ci.yml`, or equivalent. No automated tests run on push/PR. Production deployment is entirely manual and undocumented beyond README references. | Not present in repository |
| CI-2 | **HIGH** | No Dockerfile, docker-compose.yml, Procfile, or deployment manifests exist. The `gunicorn` dependency is listed in `pyproject.toml` but no start command is documented or scripted. | Not present in repository |
| CI-3 | **HIGH** | No deployment documentation. How to start the ASGI server in production, configure the reverse proxy, set up PostgreSQL/Redis, or perform zero-downtime deployment is not documented. | Not present |

---

## 19. Test Coverage

### Current state

| Area | Test Files | Tests | Coverage Assessment |
|---|---|---|---|
| Accounts (auth) | 13 files | ~90 | Good: registration, login, logout, password reset/change, verification, email change, profile, dashboard, security |
| Partnerships | 4 files | ~40 | Good: models, invitations, authorization, concurrency |
| Whiteboard | 6 files | ~80 | Good: operations, concurrency, replay, views, websocket, rename |
| Integration | 2 files | ~6 | Thin: security headers, shell rendering |
| Unit | 3 files | ~10 | Minimal: config, health check, URLs |
| **Total** | **28 files** | **~216** | **Reasonable baseline** |

### Frontend

| Area | Tests | Assessment |
|---|---|---|
| TypeScript | 85 (vitest) | Good: unit tests for engine, state, sync, renderer, input, conflict resolver |
| Build | tsc + vite build | Clean: no type errors, clean build |

### Gaps

| # | Severity | Finding | Location |
|---|---|---|---|
| T-1 | **MEDIUM** | No E2E tests (no Cypress/Playwright). The full user flow (signup → verify → invite → accept → draw → offline → sync) has no automated end-to-end test. | Not present |
| T-2 | **MEDIUM** | Security integration tests are thin: only 5 tests checking basic headers (X-Frame-Options, Content-Type) and CSRF cookie settings. No tests for: IDOR (accessing another user's whiteboard), unauthorized WS connections, expired invitations, replayed tokens, malformed operations. | `tests/integration/test_security.py` (23 lines) |
| T-3 | **LOW** | No failure-injection tests (database down, Redis down, network partition, duplicate operations). | Not present |
| T-4 | **LOW** | No load/performance tests. | Not present |
| T-5 | **INFORMATIONAL** | Tests run against `development.py` settings (InMemoryChannelLayer, LocMemCache, DEBUG=True). Production security settings are never exercised by the test suite. | `pyproject.toml:79` |

---

## 20. Documentation

### Existing docs

| Document | Status |
|---|---|
| `README.md` | Present: project overview, setup instructions, architecture tree |
| `ARCHITECTURE.md` | Present |
| `DEVELOPMENT.md` | Present |
| `docs/architecture/phase-*.md` | Present: phases 0-8 audits and completion reports |
| `docs/design/phase-7-design-system.md` | Present |
| `docs/product/` | Present: whiteboard-ux, mobile-ux, sync-user-experience, keyboard-shortcuts |
| `docs/security/invitation-security.md` | Present |
| `docs/testing/phase-7-test-matrix.md` | Present |

### Missing docs

| # | Severity | Finding |
|---|---|---|
| DOC-1 | **HIGH** | No `docs/deployment/` directory. No production architecture doc, database migrations guide, or deployment procedure. |
| DOC-2 | **HIGH** | No `docs/operations/` directory. No backup/restore, disaster recovery, health checks, monitoring, incident response, or production runbook. |
| DOC-3 | **HIGH** | No `docs/security/security-checklist.md` or `docs/security/rate-limits.md`. |
| DOC-4 | **HIGH** | No `docs/production-readiness-report.md`. |
| DOC-5 | **MEDIUM** | No `docs/testing/production-test-plan.md` or `docs/performance/whiteboard-benchmarks.md`. |
| DOC-6 | **MEDIUM** | `.env.example` is missing production-specific vars: `SESSION_COOKIE_AGE`, `SECURE_REFERRER_POLICY`, `PASSWORD_HASHERS`, and any monitoring/sentry config. |

---

## 21. Code Quality

| # | Severity | Finding | Location |
|---|---|---|---|
| Q-1 | **LOW** | Dead constants: `MAX_PAYLOAD_BYTES = 200_000` (models.py:25) and `MAX_REQUEST_BODY_BYTES = 250_000` (limits.py:30) are defined but never enforced. The validator.py comment references a non-existent `RequestSizeGuard`. | `apps/whiteboard/models.py:25`, `apps/whiteboard/limits.py:30`, `apps/whiteboard/validator.py:65` |
| Q-2 | **INFORMATIONAL** | No stray `console.log` in TypeScript (only `console.debug` for SW registration skip and `console.warn` for realtime errors — both intentional). | Grep confirmed clean |
| Q-3 | **INFORMATIONAL** | No TODO/FIXME/HACK comments found. No commented-out code blocks. | Grep confirmed clean |
| Q-4 | **INFORMATIONAL** | No hardcoded secrets, URLs (only `localhost` defaults), or API keys in source. | Grep confirmed clean |

---

## 22. Architecture Summary

### What IS working correctly

1. **Authorization model:** Uniform `is_active_member` check across page/API/WebSocket, including per-message re-auth. Server-side only. No IDOR possible.
2. **Operation integrity:** Server-authoritative sequence/version/actor/timestamps. DB-enforced idempotency and uniqueness. Atomic batch submission with stale-version rejection.
3. **Offline-first sync:** Durable IndexedDB queue, crash recovery (SUBMITTING→PENDING), conflict quarantine (not deletion), exponential backoff, server ack confirmation.
4. **Real-time collaboration:** Channels WebSocket with session auth, origin validation, per-connection rate limiting, message validation, per-message re-authorization.
5. **CSP:** Strong production policy with nonce-based script/style, `unsafe-eval` only for Alpine.js (documented), `object-src 'none'`, `frame-ancestors 'none'`.
6. **CSRF:** Comprehensive: meta tag + HTMX auto-attach + fetch API + cookie HttpOnly+Secure+Lax. No exempt endpoints.
7. **Data isolation:** Partnership uniqueness enforced at DB level (partial unique indexes + trigger). PartnershipMember max-2-active enforced at DB trigger level.
8. **Token security:** All tokens (email verification, partnership invitation) stored as SHA-256 digests. Single-use, time-limited. Raw tokens never persisted or logged.

### What needs attention (top priorities)

1. **CRITICAL-HIGH cluster:** Secret key fallback (S-1), ASGI/WSGI settings default (S-2), missing CSRF_TRUSTED_ORIGINS enforcement (S-3), no CI/CD (CI-1/2/3), no deployment docs (DOC-1/2/3/4).
2. **MEDIUM cluster:** HTTP body size limit (O-1/SZ-2), WS connection limit (W-1), health check depth (E-1), structured logging (E-2), session duration (A-1/A-7), IDB logout cleanup (PWA-2), production test settings (S-4), state reconstruction scalability (DP-1), E2E tests (T-1/T-2).
3. **LOW cluster:** Redundant DB indexes (DB-4/DB-5), dead constants (Q-1), SW skipWaiting behavior (PWA-3), media storage config (SF-1).

---

## 23. Recommended Implementation Order

Based on risk and dependency:

### Phase 8A — Security Hardening (CRITICAL/HIGH)
1. `config/settings/production.py` — enforce `CSRF_TRUSTED_ORIGINS`, add `SECURE_REFERRER_POLICY`, `SESSION_COOKIE_AGE`, `SESSION_EXPIRE_AT_BROWSER_CLOSE`, `DATA_UPLOAD_MAX_MEMORY_SIZE`, `DATA_UPLOAD_MAX_NUMBER_FIELDS`
2. `config/settings/base.py` — remove insecure `SECRET_KEY` fallback (require env var in all environments, or raise in production)
3. `config/asgi.py` + `config/wsgi.py` — fail loudly if `DJANGO_SETTINGS_MODULE` points to development in a production-like context (or at minimum, don't default to development)
4. Create `config/settings/test.py` with production-equivalent security settings so tests exercise the production configuration
5. Enforce HTTP operation request body size limit (implement `RequestSizeGuard` or use Django middleware)
6. Add per-user WebSocket connection limit in consumer
7. Enhance health check to probe PostgreSQL and Redis
8. Invalidate sessions on password reset
9. Run `python manage.py check --deploy` and fix all warnings

### Phase 8B — Reliability & Operations
1. Implement structured (JSON) logging with correlation IDs
2. Create Dockerfile and docker-compose.yml
3. Create `docs/deployment/production-architecture.md`
4. Create `docs/deployment/database-migrations.md`
5. Create `docs/operations/backup-and-restore.md`
6. Create `docs/operations/disaster-recovery.md`
7. Create `docs/operations/health-checks.md`
8. Create `docs/operations/monitoring.md`
9. Create `docs/operations/production-runbook.md`
10. Create `docs/operations/incident-response.md`
11. Update `.env.example` with all production variables

### Phase 8C — Testing & Quality
1. Add security integration tests (IDOR, unauthorized WS, expired invitations, malformed ops)
2. Add failure-injection tests (DB down, Redis down, network partition)
3. Create `docs/testing/production-test-plan.md`
4. Clean up dead constants (Q-1), redundant indexes (DB-4/DB-5)
5. Implement IDB cleanup on logout (PWA-2)

### Phase 8D — Documentation & Final Review
1. Create `docs/security/security-checklist.md`
2. Create `docs/security/rate-limits.md`
3. Create `docs/production-readiness-report.md`
4. Final architectural review
5. Performance/load testing (if needed for launch)

---

## 24. Definition of Done Checklist (tracking)

| # | Item | Status |
|---|---|---|
| 1 | Production audit complete | ✅ THIS DOCUMENT |
| 2 | Security hardening (S-1 through S-5, O-1, W-1, A-1) | ⬜ Pending |
| 3 | Production settings validated (`check --deploy`) | ⬜ Pending |
| 4 | Health checks enhanced (DB + Redis) | ⬜ Pending |
| 5 | CI/CD pipeline created | ⬜ Pending |
| 6 | Docker + deployment docs | ⬜ Pending |
| 7 | Backup/restore documented + tested | ⬜ Pending |
| 8 | Disaster recovery documented | ⬜ Pending |
| 9 | Security checklist complete | ⬜ Pending |
| 10 | Production runbook complete | ⬜ Pending |
| 11 | E2E test scenario documented | ⬜ Pending |
| 12 | Security integration tests expanded | ⬜ Pending |
| 13 | Service worker security reviewed (cross-account) | ⬜ Pending |
| 14 | Multi-tab/device testing | ⬜ Pending |
| 15 | Production readiness report | ⬜ Pending |

---

## Addendum — 2026-09-04 post-implementation re-audit

This document was written 2026-09-02 as a read-only audit before any Phase 8
implementation began. Work since then (both this pass and, for several
findings, work that had already landed in the codebase before this pass
started) has closed a number of the findings above. Per this project's
established convention (see `docs/architecture/phase-2-audit.md` §10.1 for
the same pattern), the original findings above are **left unedited** — this
addendum records what was re-verified against the current code, what's now
fixed, what's genuinely still open, and one new finding this pass discovered
that the original audit missed. Line numbers in the original findings above
have drifted as files changed; treat file:line citations in *this* addendum
as current.

### Confirmed fixed (already true before this pass started, re-verified directly)

| Finding | Verification |
|---|---|
| S-1 (hardcoded `SECRET_KEY` fallback) | `config/settings/base.py` — `os.environ.get("DJANGO_SECRET_KEY", "")`, empty-string fallback only, no insecure literal. `production.py` raises `ImproperlyConfigured` if unset. |
| S-2 (asgi/wsgi default to development) | `config/asgi.py:16`, `config/wsgi.py:7` — both `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")`. |
| S-3 (`CSRF_TRUSTED_ORIGINS` not enforced) | `production.py` raises if empty. |
| S-4 (no `config/settings/test.py`) | Exists, mirrors production security settings, wired via `pyproject.toml`. |
| S-5 (missing `SECURE_REFERRER_POLICY`/`SESSION_COOKIE_AGE`/`SESSION_EXPIRE_AT_BROWSER_CLOSE`/`DATA_UPLOAD_MAX_*`) | All present in `production.py`. |
| A-1 (no session invalidation on password reset) | `PasswordResetConfirmView.form_valid` calls `_invalidate_other_sessions(...)`. |
| A-2 (no logout-all-devices) | `logout_all_devices` view exists (`apps/accounts/views.py`). |
| A-7 (session age not overridden) | `SESSION_COOKIE_AGE = 86400` in `production.py` (same fact as S-5). |
| W-1 (no per-user WS connection cap) | `MAX_CONNECTIONS_PER_USER = 10`, enforced via `_reserve_connection_slot`/`_release_connection_slot` (`apps/whiteboard/consumers.py`). |
| E-1 (health check superficial) | `apps/core/views.py` `health_check` — `/health/ready/` does a real DB `SELECT 1` + cache `set`/`get` probe, 503 on failure. |
| E-2 (no structured logging) | Partially fixed: `apps/core/logging.py` has a real `JSONFormatter`, toggled by `LOG_JSON`. **Correlation/request IDs are still absent** — carried forward below, not fully closed. |
| DOC-6 (`.env.example` missing production vars) | Current `.env.example` documents `SESSION_COOKIE_AGE`, `SECURE_REFERRER_POLICY`, `DATA_UPLOAD_MAX_*`, `LOG_JSON`, `POSTGRES_SSLMODE`, and (this pass) `MAX_REQUEST_BODY_BYTES`. |
| PWA-2 (no `clearOwnerPartition()` on logout) | Re-verified via grep across `templates/`: still true that it's never called. **Not a bug** — see `docs/architecture/offline-architecture.md` §7, corrected during the Phase 6 pass of this project to document this as a deliberate "preserve, never destroy" choice (clearing on logout would risk deleting still-unsynced offline work). Downgraded from a defect to a documented, intentional limitation — no code change needed. |

### New findings this pass discovered (not in the original audit)

| Finding | Details | Fix applied |
|---|---|---|
| **Request-body-size setting mismatch** | `apps/whiteboard/limits.py:30` defined a whiteboard-specific `MAX_REQUEST_BODY_BYTES = 250_000` (250 KB) that `docs/api/whiteboard.md` and `docs/architecture/whiteboard-operations.md` both wrongly documented as the enforced HTTP body cap. The *actual* enforcing code, `apps/core/middleware.py`'s `RequestSizeGuard`, reads a differently-named global setting (`settings.MAX_REQUEST_BODY_BYTES`, no `WHITEBOARD_` prefix) that was never set anywhere, so it silently fell back to its own 5 MB default. The 250 KB constant was dead — zero references outside its own definition. | Removed the dead constant; corrected both docs to state the real, enforced 5 MB global limit; added `MAX_REQUEST_BODY_BYTES` as a documented, configurable `.env.example` entry. 5 MB was kept (not tightened to 250 KB) because it's the value consistent with a legitimate full-size batch: `MAX_OPERATION_PAYLOAD_BYTES` (100 KB) × `MAX_BATCH_OPERATIONS` (50) ≈ 5 MB — tightening to 250 KB would have started rejecting valid batches. |
| **`SECRET_KEY` minimum weaker than Django's own `check --deploy` threshold** | `production.py` only required ≥32 chars, but an actual `manage.py check --deploy` run (see below) flagged `security.W009` (Django recommends ≥50 chars + character diversity) even though 32-char keys satisfied the code's own guard. | Raised the enforced minimum to 50 chars, matching Django's threshold and the generation method `.env.example` already documented (`secrets.token_urlsafe(50)`). Verified: `check --deploy` is now clean on a properly-generated key (see below). |
| **No connection timeout on PostgreSQL or Redis** | Discovered while writing a failure-injection test for `/health/ready/` (see `docs/testing/production-test-plan.md`): pointing `POSTGRES_PORT`/`REDIS_URL` at a genuinely unreachable address showed neither connection had any configured timeout, so a real outage (not just a rejected connection) could hang a request for 14+ seconds instead of failing fast — defeating the purpose of a cheap, quick readiness probe. | Added `connect_timeout` (default 5s, env `POSTGRES_CONNECT_TIMEOUT`) to the PostgreSQL `OPTIONS` in `production.py`, and `socket_connect_timeout`/`socket_timeout` (default 5s each) to the Redis `CACHES["default"]["OPTIONS"]` in `base.py`. Each verified independently and effective (~5s DB-only, ~2-3s cache-only) by pointing exactly one dependency at an unreachable address at a time. Note: the Redis fix's first attempt used the wrong `OPTIONS` shape (`CONNECTION_POOL_KWARGS`, which is `django-redis`'s API, not Django's own native `RedisCache`) and failed loudly with a clear `TypeError` on the first real test — corrected by reading the installed Django source directly rather than continuing to guess. |

### `manage.py check --deploy` — actually run (item 3 of the Definition of Done above)

Run against `config.settings.production` with placeholder-but-valid required env vars. First run (before the `SECRET_KEY` fix, using a 43-char test key, no SMTP backend configured):

```
ERRORS:
?: (mail.E001) Your MAILERS setting uses a development-only email backend
   in the 'default' entry (django.core.mail.backends.console.EmailBackend).
WARNINGS:
?: (security.W009) Your SECRET_KEY has less than 50 characters...
```

After raising the `SECRET_KEY` minimum to 50 and generating a real key via
`secrets.token_urlsafe(50)`: `security.W009` gone. The remaining `mail.E001`
is not a code gap — it's Django correctly detecting that this particular test
invocation didn't set `DJANGO_EMAIL_BACKEND` to an SMTP backend, which
`.env.example` already documents as a required production override
(`# Development defaults to console. In production set the SMTP backend +
creds.`). Confirmed with all required production env vars *and* an SMTP
backend name set:

```
System check identified no issues (0 silenced).
```

`check --deploy` is clean when the documented environment variables are
actually set. This is a citable, reproducible result — not a claim.

### Confirmed still genuinely open (re-verified, no change made — reasons noted)

| Finding | Status |
|---|---|
| O-1 / SZ-2 (HTTP body has no size guard) | **Partially resolved, differently than the original finding described.** `RequestSizeGuard` middleware does exist and does enforce a global 5 MB cap (see "New findings" above) — the original claim that no guard exists at all is now false. What's still true: there's no *whiteboard-specific*, tighter limit distinct from the global one. Not fixed further this pass — 5 MB is judged an acceptable, already-enforced ceiling; a whiteboard-specific tier isn't clearly justified given it's consistent with the real per-batch maximum. |
| A-3 (no rate limit on `PasswordResetConfirmView`) | **Fixed this pass.** Added `ratelimit.check("password_reset_confirm", ...)` (6/900s, keyed by IP + uidb64) mirroring the existing pattern on `PasswordResetView`/`LoginView`. New test: `tests/accounts/test_password_reset.py::test_reset_confirm_rate_limited_after_six_attempts`. |
| DB-1 (`POSTGRES_PASSWORD` not validated) | **Fixed this pass.** `production.py` now raises `ImproperlyConfigured` if empty, mirroring the `SECRET_KEY` guard. Verified: raises with an empty password, loads cleanly with a real one. |
| DB-4 (redundant `Whiteboard.status` index) | **Fixed this pass.** Removed `db_index=True` from the field (kept the named `Meta.index`). Migration `0003_remove_whiteboardoperation_idx_op_board_seq_and_more`. |
| DB-5 (redundant `WhiteboardOperation (whiteboard, sequence)` index) | **Fixed this pass.** Removed the duplicate `Meta.index`; the `uniq_op_sequence_per_board` unique constraint already backs that exact query pattern. Same migration as DB-4. |
| Q-1 (dead constants) | **Fixed this pass**, in a corrected form. `MAX_PAYLOAD_BYTES` (`models.py`) removed — confirmed zero references. `limits.MAX_REQUEST_BODY_BYTES` removed too, but for the more specific reason described in "New findings" above (it wasn't just unenforced, it actively conflicted with the real 5 MB middleware default and two docs pages). The claim that "the validator.py comment references a non-existent `RequestSizeGuard`" is now false — the middleware exists — so that part of Q-1 needed no fix. |
| SF-1 (misleading media-serving comment) | **Fixed this pass.** `config/urls.py` comment corrected to accurately state WhiteNoise only serves `STATIC_URL`, never `MEDIA_URL`; production needs a reverse proxy for `/media/`. |
| DP-1, SC-1, SC-2 (no snapshotting/compaction, no per-board object cap) | **Still open**, not addressed this pass. Genuinely requires either a documented, justified threshold decision or actual implementation — deferred; see `docs/performance/whiteboard-benchmarks.md` (new, this pass) for the current local measurement and the trigger condition for revisiting. |
| E-2 (no correlation/request IDs in structured logs) | **Still open.** `JSONFormatter` exists and works but carries no per-request correlation id. Noted as a known gap in the final readiness report rather than silently dropped. |
| CI-1, CI-2, CI-3 (no CI/CD, no Docker, no deployment docs) | CI-1 addressed this pass (`.github/workflows/ci.yml` authored — see readiness report for the important caveat that it has never run against a real runner). CI-2 (Docker) intentionally **not** added — no evidence a container deployment target exists or was requested; documented as a deployment-architecture *choice point* in `docs/deployment/production-architecture.md` rather than assumed. CI-3 addressed via the new `docs/deployment/`/`docs/operations/` docs this pass. |
| T-1 through T-4 (no E2E, thin security integration tests, no failure-injection, no load tests) | **Still open**, and honestly so — see `docs/testing/production-test-plan.md` (new, this pass), which describes these as tests *not yet written*, not tests that exist. Failure-injection was partially exercised manually (not automated): mock-based fault injection on `django.db.connection.cursor()` and `cache.set()` confirmed `/health/ready/` degrades to 503 correctly, and a real connection-timeout bug was found and fixed along the way — see `docs/testing/production-test-plan.md` and the readiness report for the full account. |
| DOC-1 through DOC-5 | Addressed this pass — see the readiness report's document index. |
| PWA-1 (IndexedDB single shared DB, `getWhiteboard()` doesn't re-check `owner_key`) | **Still open, judged acceptable as-is.** Re-read `frontend/src/whiteboard/local-store.ts`: `getWhiteboard()` does look up by a key that already embeds the whiteboard's server-assigned UUID, not a guessable value, and every *listing* query (`getPendingOperations`, `getConflictsForBoard`, etc.) does filter by `owner_key`. A direct-key lookup lacking an *additional* owner check is defense-in-depth, not a live cross-account leak, since the UUID itself isn't attacker-controlled or guessable. Not changed this pass — flagged as a low-priority hardening item, not re-classified as higher severity. |

### Recommended Implementation Order (§23) — status update

- **Phase 8A** items 1, 2 (removed insecure fallback — already true), 4 (test.py — already existed), 8 (session invalidation — already true) were already done before this pass. Items 5 (body size guard — now correctly documented, not re-tightened), 6 (WS connection limit — already true), 7 (health check — already true), 9 (`check --deploy` — actually run this pass, documented above) are now all closed.
- **Phase 8B, 8C, 8D**: addressed this pass via the new docs listed in `docs/production-readiness-report.md`, with the explicit, honest exception of items that require a real deployment target to actually perform (Docker, a live CI run, a real backup/restore drill beyond a local smoke test, real load testing) — those are documented as procedure and marked deferred-to-deployment in the readiness report, not fabricated as completed.

### Definition of Done Checklist (§24) — status update

| # | Item | Status |
|---|---|---|
| 1 | Production audit complete | ✅ (this document + this addendum) |
| 2 | Security hardening (S-1 through S-5, O-1, W-1, A-1) | ✅ All confirmed fixed |
| 3 | Production settings validated (`check --deploy`) | ✅ Actually run, clean when properly configured (see above) |
| 4 | Health checks enhanced (DB + Redis) | ✅ Already true |
| 5 | CI/CD pipeline created | ✅ Authored (`.github/workflows/ci.yml`) — **never executed against a real runner**, see readiness report |
| 6 | Docker + deployment docs | Docker: not added (no evidence of a container target — documented as a choice point). Deployment docs: ✅ |
| 7 | Backup/restore documented + tested | ✅ Documented; **local smoke test only** (pg_dump/restore against local dev Postgres) — not a production drill, see readiness report |
| 8 | Disaster recovery documented | ✅ Procedure documented, not rehearsed against a real incident |
| 9 | Security checklist complete | ✅ |
| 10 | Production runbook complete | ✅ |
| 11 | E2E test scenario documented | ✅ Documented as a plan, tests not yet written |
| 12 | Security integration tests expanded | Not expanded this pass — documented as open in the test plan |
| 13 | Service worker security reviewed (cross-account) | ✅ Re-reviewed this pass (PWA-1/PWA-2, above) |
| 14 | Multi-tab/device testing | Not performed this pass — no real device lab; documented as deferred |
| 15 | Production readiness report | ✅ `docs/production-readiness-report.md` |
