# Security Checklist

Current state as of 2026-09-04, verified against code (not assumed). Every
line is either checked with a citation, or marked **deferred to deployment**
for anything that genuinely requires a live production environment to
verify (this repo has no deployed staging/production target — see
`docs/production-readiness-report.md` for the full framing).

## Application

- [x] `DEBUG` disabled in production — `config/settings/production.py`: `DEBUG = False`.
- [x] HTTPS enforced — `SECURE_SSL_REDIRECT = True`, `SECURE_PROXY_SSL_HEADER` set (production.py).
- [x] Secure cookies — `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE = True` (production.py); `HTTPONLY`/`SAMESITE=Lax` on both, set globally in `base.py`.
- [x] CSRF enabled — `CsrfViewMiddleware` in `MIDDLEWARE` (base.py); zero `csrf_exempt` usages anywhere in the codebase (grep-confirmed).
- [x] CSP configured — nonce-based, `default-src 'self'`; the one `unsafe-eval` exception (Alpine.js) is documented inline in `base.py` with the reason. `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`, `base-uri 'self'`.
- [x] HSTS configured — `SECURE_HSTS_SECONDS = 31536000`, `INCLUDE_SUBDOMAINS`, `PRELOAD` all `True` (production.py). **Deferred to deployment**: HSTS preload submission to browser vendors is an operational step, not a code setting.
- [x] Clickjacking protection — `X_FRAME_OPTIONS = "DENY"` + CSP `frame-ancestors 'none'` (belt and suspenders).
- [x] Security headers — `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_BROWSER_XSS_FILTER`, `SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"` (production.py).
- [x] Rate limiting — see `docs/security/rate-limits.md` for the full table.
- [x] Request/payload size limits — `apps.core.middleware.RequestSizeGuard` (global 5 MB HTTP body cap), plus whiteboard-specific `MAX_OPERATION_PAYLOAD_BYTES`/`MAX_STROKE_POINTS`/`MAX_BATCH_OPERATIONS` and WebSocket `MAX_MESSAGE_BYTES` (`apps/whiteboard/limits.py`, `realtime.py`).
- [x] `manage.py check --deploy` run and clean — see `docs/architecture/phase-8-production-audit.md` addendum for the actual captured output (0 issues when required env vars are set correctly).
- [ ] **Deferred to deployment**: a real TLS certificate is provisioned, renewed, and terminates correctly at the reverse proxy — nothing in this repo can verify that without a live host.

## Authentication

- [x] Password hashing — Django's default hasher chain (PBKDF2), no custom hashing introduced.
- [x] Password validation — all 4 Django default validators active (`AUTH_PASSWORD_VALIDATORS`, base.py).
- [x] Password reset security — signed, time-limited, single-use tokens (Django's built-in `PasswordResetConfirmView`); **session invalidation on reset** (`_invalidate_other_sessions`, `apps/accounts/views.py`); rate-limited on both the request and confirm steps (`RATE_LIMITS["password_reset"]`/`["password_reset_confirm"]`).
- [x] Email verification — single-use, SHA-256-hashed-at-rest tokens, 24h expiry (`apps/accounts/models.py` `EmailVerificationToken`); unverified accounts cannot establish a session (`LoginView.form_valid`).
- [x] Session security — HttpOnly + Secure + SameSite=Lax cookies; `SESSION_COOKIE_AGE = 86400`; password *change* rotates the session auth hash (`update_session_auth_hash`); "sign out everywhere" exists (`logout_all_devices`).
- [x] Brute-force protection — login/register/reset/resend all rate-limited (see rate-limits.md); no user enumeration on login failure or password-reset request (generic responses either way).
- [x] Tokens/passwords never logged — `apps/core/emailing.py` docstring states this explicitly; verified no `logger.*` call anywhere in `apps/accounts` includes a password, token, or session key value (only IPs, user ids, and outcome strings — see the auth-event logging added in the Phase 1 audit pass of this project).

## Authorization

- [x] Object-level access control — every protected view/API/WebSocket path checks `is_active_member(user, partnership)` via the centralized `apps.partnerships.policies`/`apps.whiteboard.policies` predicates, not `@login_required` alone.
- [x] Partnership isolation — a user can only ever hold one active membership (DB partial unique index **and** a database trigger — PL/pgSQL on PostgreSQL in `apps/partnerships/migrations/0002_parternship_db_constraints.py`, native SQLite triggers in `0003_sqlite_member_triggers.py` — two independent enforcement layers, not just app-level checks).
- [x] Whiteboard isolation — resolved only via the owning partnership's membership; `public_id` UUIDs are not treated as a secret substitute for authorization (server always re-checks membership regardless of whether the UUID was guessed or leaked).
- [x] WebSocket authorization — re-checked on every inbound message (`_still_authorized`, `apps/whiteboard/consumers.py`), **and** proactively pushed to passive (non-sending) connections when a partnership ends (`whiteboard_reauthorize` handler + `apps/whiteboard/realtime_signals.py`, added during the Phase 5 audit pass of this project — closes the gap where a silent listener could otherwise keep receiving broadcasts after their access was revoked).
- [x] Operation authorization — actor is always the authenticated connection's user, never client-supplied; server rejects any operation payload attempting to set `actor`/`sequence`/`resulting_version`/`created_at` (`apps/whiteboard/validator.py` `_FORBIDDEN_FIELDS`).
- [x] IDOR test coverage exists — `tests/partnerships/test_authorization.py`, `tests/whiteboard/test_websocket.py` (`test_connect_rejects_non_member`, `test_revoked_member_cannot_keep_writing`, `test_ended_partnership_disconnects_passive_listener`). **Deferred to deployment / future work**: broader IDOR fuzz-testing beyond the specific scenarios already covered — see `docs/testing/production-test-plan.md`.

## Data

- [x] PostgreSQL is the default production database; SQLite is a supported fallback (`DJANGO_DATABASE_ENGINE=sqlite`) for single-file hosts like PythonAnywhere — its `OPTIONS` enable WAL journaling and immediate-mode transactions, and the membership-invariant trigger has a native SQLite equivalent (see `apps/partnerships/migrations/0003_sqlite_member_triggers.py`).
- [x] Least-privilege / credential guard — `DATABASES["default"]["PASSWORD"]` is validated non-empty in production (`ImproperlyConfigured` if unset, added this pass). **Deferred to deployment**: the actual database *user*'s grants (should be scoped to this app's schema only, not superuser) are a provisioning-time decision this repo can't enforce from code.
- [x] Backups documented — `docs/operations/backup-and-restore.md`. **Partially verified**: a local `pg_dump`/restore smoke test was actually run against the local dev database (see that doc for the real result) — this is not the same as a verified production backup pipeline, which is **deferred to deployment**.
- [ ] **Deferred to deployment**: production backup automation (scheduled `pg_dump`/WAL archiving, off-site storage, retention enforcement) is not provisioned anywhere — no such infrastructure exists to configure in this repo.
- [x] Migration strategy documented — `docs/deployment/database-migrations.md`; all existing migrations reviewed for reversibility/locking risk.
- [x] DB integrity — foreign keys enforced by Postgres/Django ORM throughout; unique constraints exist for every identity/idempotency-sensitive field (`Whiteboard.public_id`, `WhiteboardOperation (whiteboard, operation_id)` and `(whiteboard, sequence)`, `PartnershipMember (partnership, user)` + partial-unique active-membership, `PartnershipInvitation` partial-uniques, `User.email`/`public_id`).

## Client

- [x] Service worker reviewed — does not cache `/api/*` responses or authenticated HTML; offline API calls get a terse `OFFLINE` JSON response, never fabricated data (`static/sw.js`).
- [x] Cache isolation — service worker cache is origin-scoped by the browser itself (standard SW behavior); no cross-origin cache sharing possible.
- [x] IndexedDB account isolation — every record carries `owner_key` (the account's `public_id`); every *listing* query filters by it. A direct single-key lookup (`getWhiteboard`) doesn't additionally re-check `owner_key`, but the key itself is a server-assigned UUID, not attacker-controlled or guessable — documented as an accepted, low-priority hardening gap in the Phase 8 audit addendum, not a live leak.
- [x] No sensitive browser storage leakage — no passwords, session cookies, or auth tokens are ever written to `localStorage`/`sessionStorage`/IndexedDB by application code (grep-confirmed; the CSRF token is read from a meta tag, never stored client-side beyond the browser's own cookie jar).
- [x] Logout does not destroy unsynced offline work — deliberate policy, documented in `docs/architecture/offline-architecture.md` §7 (preserve, never auto-clear on logout; the account-scoped partition means the same account resumes correctly on next login on the same device).

## Infrastructure

- [ ] **Deferred to deployment**: TLS termination, real reverse-proxy configuration, and certificate lifecycle — no live infrastructure exists in this repo to configure.
- [x] Redis usage documented — ephemeral only (Channels layer + rate-limit/cache), never the authoritative store for confirmed whiteboard operations; failure behavior documented in `docs/architecture/phase-8-production-audit.md` §14 (RD-2).
- [x] ASGI — `config/asgi.py` correctly routes `http`/`websocket` via `AuthMiddlewareStack(URLRouter(...))`; `daphne`/`gunicorn` both present as dependencies.
- [x] Health checks — `/health/live/` (liveness) and `/health/ready/` (DB + cache dependency probe, 503 on failure) both exist and were exercised (see `docs/operations/health-checks.md`).
- [x] Structured logging — JSON formatter exists, toggled via `LOG_JSON`. **Known gap, not fixed this pass**: no per-request correlation ID in log output — noted honestly rather than silently omitted.
- [x] Monitoring targets defined — `docs/operations/monitoring.md`. **Deferred to deployment**: no monitoring stack (Prometheus/Grafana/Sentry/equivalent) is actually wired up anywhere; this repo only defines *what* to monitor.
- [ ] **Deferred to deployment**: CI pipeline execution — `.github/workflows/ci.yml` is authored and internally consistent with this project's actual toolchain, but has never run against a real GitHub Actions runner (no git remote is configured in this repo).
