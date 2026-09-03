# Phase 2 Audit — Phase 0 & Phase 1 Architecture Review

**Auditor:** Candle engineering (Senior Django Architect / Security review)
**Date:** 2026-09-03
**Applies to:** the codebase as of the start of Phase 2 (partnerships work).
**Purpose:** Record what Phases 0 and 1 implemented, what is correct, what is
risky, what must be fixed now, and what can be deferred. This doc also pins down
verified facts about the exact toolchain so future phases do not re-derive them.

> Verification note: every version and API claim below was checked against the
> installed packages in this environment on the audit date, not assumed.

---

## 1. Verified toolchain (from the environment)

- Python 3.13.4 (only supported Python in use).
- Django **6.1.1** — the current stable release at implementation time.
  Confirmed installed in `venv`.
- Django Channels **4.3.2**, channels-redis **4.3.0**, Daphne (ASGI).
- Django REST Framework **3.18.0**.
- psycopg **3.3.5** (psycopg3, PostgreSQL primary DB).
- WhiteNoise **6.12.0**, redis **8.1.0**, Pillow **11.3.0** (avatars).
- Dev tooling: pytest **9.1.1**, pytest-django **4.14.0**, ruff **0.16.5**,
  mypy **1.20.2**, django-stubs **5.2.9**.
- Node v22.19.0, npm; Vite dev-server; Tailwind v4; HTMX 2; Alpine 3; Lucide.

### 1.1 Verified Django 6.1 API facts that shape Phase 2

- Django's enum tooling lives in `django.db.models.enums`. The classes
  available in this build are: `Choices`, `TextChoices`, `IntegerChoices`,
  `IntEnum` (legacy), `StrEnum`. **`TextFieldChoices` does NOT exist in this
  Django 6.1.1 build** (it is not exported from `django.db.models.enums`). The
  task brief proposed `TextFieldChoices`; the correct, current, supported API
  is `django.db.models.TextChoices` (built on `StrEnum`), which is what Phase 2
  uses for lifecycle enums.
- `models.TextChoices` is the idiomatic way to attach both a Python enum and
  built-in `choices`/`clean` behaviour to a field with a human-readable label.
- `models.UniqueConstraint`, `models.CheckConstraint`, and
  `models.ExclusionConstraint` are available — used for the two-member rule and
  other DB-integrity guarantees.
- `QuerySet.select_for_update()` is available (PostgreSQL row locking) — the
  tool for concurrency-safe invitation acceptance and partnership activation.
- `ForeignKey.on_delete` supports the full set: `CASCADE`, `PROTECT`,
  `RESTRICT`, `SET_NULL`, `SET_DEFAULT`, `SET`, `DO_NOTHING`. There is **no
  new "database-level delete" option beyond these**; the correct choice of
  these six is the design decision to make. (`models.DeleteModel` does not
  exist.)
- Email is configured via the modern **`MAILERS`** setting (Django 6.1
  introduced `MAILERS` as the replacement for `EMAIL_BACKEND`, which is
  deprecated). `django.core.mail.backends.console.EmailBackend` is used in
  development; SMTP is used in production.
- `django.core.cache.backends.redis.RedisCache` is used as the cache backend
  (Redis). This is what the project's rate limiter builds on.
- Django does **not** ship a first-party background task framework. There is no
  Celery in the repo. The brief instructs not to introduce Celery just because
  it is popular; Phase 2 therefore evaluates simpler options (see §7 below).

---

## 2. What Phase 0 implemented

- Project foundation: `config/` package with split settings
  (`base`/`development`/`production`), `manage.py`, `pyproject.toml`.
- PostgreSQL + psycopg3 database wiring, Redis cache + Channel layer.
- ASGI/WSGI applications, Channels 4 for future WebSockets.
- Django REST Framework pre-wired for a future mobile/API surface.
- WhiteNoise static serving + hashed manifest storage in production.
- Security baseline: CSP (`SECURE_CSP`, nonce-based), clickjacking
  (`X-Frame-Options`, CSP `frame-ancestors`), secure cookies in production,
  HSTS, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`.
- Frontend: Vite + TypeScript + Tailwind v4 + HTMX + Alpine + Lucide, bundled
  locally (no external CDN), output to `static/dist/`.
- Base templates, error pages, shared components, PWA manifest + icons.
- A health-check endpoint and a small `apps/core` app.
- `MAILERS` email configuration.

## 3. What Phase 1 implemented

- Custom email-based `accounts.User` model (`AbstractBaseUser` +
  `PermissionsMixin`) with `email`, `email_verified`, `email_pending`,
  `first_name`, `last_name`, staff/superuser flags.
- `UserManager` with full-address email normalization (lowercases the local
  part too, not just the domain — a deliberate improvement over Django's
  default) and a case-insensitive `get_by_natural_key`.
- `Profile` (user → one-to-one) for product identity: display name, avatar,
  timezone, locale — intentionally separated from authentication identity.
- Registration + email verification via single-use, hashed, time-limited
  tokens (`EmailVerificationToken`), stored as a SHA-256 digest (raw token
  never persisted).
- Email verification / email-change / password reset / password change /
  login / logout flows (most built on Django's own auth views).
- Unverified-account gate on login (no session until verified).
- Cache-backed, focused rate limiting (`apps/accounts/ratelimit.py`) for
  login, register, password reset, resend.
- CSRF enforced; HttpOnly session + CSRF cookies; SameSite=Lax.
- HTML + plain-text transactional emails, `SITE_URL` for absolute links.
- 69 passing tests across `tests/accounts`, `tests/integration`, `tests/unit`.

---

## 4. What is correct (keep as-is)

- **Custom user model is actually used** (`AUTH_USER_MODEL = "accounts.User"`).
- **Email uniqueness is enforced at the database level**
  (`unique=True` on `User.email`); normalisation is applied consistently by the
  manager and `User.clean`.
- **No partnership information is stored on `User`.** The User is deliberately
  clean of business relationships, which is exactly the separation Phase 2
  needs. `Profile` holds product identity, not relationships.
- **Secure token pattern exists and is correct**: raw token in the email, only
  a SHA-256 digest persisted, single-use (consumed/deleted on use),
  time-limited, revocable in effect (delete the row). This is the exact pattern
  Phase 2's invitation tokens should mirror.
- **MAILERS is used** — not the deprecated `EMAIL_BACKEND` global.
- **Reusable rate limiter** (`apps/accounts/ratelimit.py`): small, focused,
  cache-backed, configurable. Reusable for partnership invitation endpoints.
- **CSP nonce** + locally bundled assets, no third-party CDN scripts.
- **Object/identity separation**: `User` (identity) vs `Profile` (product).
- **Email normalisation / verification** are solid and well-tested.
- **No secrets committed**; credentials only via environment variables.
- **Migrations**: `apps/accounts/migrations/0001_initial.py` is the only
  migration; it is clean and self-contained.

---

## 5. What is incomplete / risky / should be refactored

### 5.1 Email delivery blocks the HTTP request (Risk: HIGH — performance)

`apps/accounts/services.py` calls `message.send()` synchronously inside the
view path (registration, resend, password reset, email change). SMTP round
trips (especially with the 10s timeout in `MAILERS.OPTIONS.timeout`) can add
seconds of latency to user-facing requests in production.

**Fix now**: decouple delivery from the request path using a lightweight,
production-appropriate mechanism. Because this stack has Redis available and
Django has no first-party queue, but also because introducing a full broker
(Celery/RQ) is explicitly out of scope and heavy, the pragmatic Phase 2 choice
is a dedicated background email thread (`threading`) guarded so it never leaks
or blocks the request, **plus** structured logging of delivery failures and a
documented migration path to a real queue when volume demands it. An in-thread
`EmailMultiAlternatives` send keeps the current `MAILERS`/locmem/console
behaviour intact for tests (tests keep the locmem backend, still synchronous,
so assertions remain deterministic). See `docs/architecture/partnerships.md`
for the delivery strategy.

### 5.2 Password change does not rotate/invalidate the session (Risk: MEDIUM — security)

Django's `PasswordChangeView` **does not call `update_session_auth_hash` by
default**. The Phase 1 `PasswordChangeView` overrides the template/success URL
only. Result: after a user changes their password, their existing session (and
any sessions on other devices) is **not** invalidated against the new hash in
the way the docs recommend; a stolen pre-change session could survive a known
password change.

**Fix now**: override `PasswordChangeView.form_valid` to call
`update_session_auth_hash(request, user)` so the current session keeps the new
hash and there is no self-logout, while the old-hash sessions are invalidated
database-wide by Django's `SessionStore.cycle_key` semantics used in that
helper. Add a test that the auth hash changes after password change.

### 5.3 Account lifecycle is binary (Risk: MEDIUM — data/account policy)

`User` has only `is_active`. There is no coherent distinction between "email
unverified", "temporarily disabled", "permanently deactivated", and "deleted /
anonymized". Phase 1 does not define what happens to shared data (none exists
yet, but partner relationships will) when an account is disabled/deleted.

**Fix now**: implement a safe, non-destructive **deactivation** strategy and
document the future **deletion/anonymization** policy (see
`docs/security/invitation-security.md` and the shared-resource policy in
`docs/architecture/shared-resource-access.md`). Phase 2 introduces a
lifecycle-aware policy so that: an inactive user cannot authenticate; their
active partnership(s) are handled safely (documented end/suspend behaviour);
and audit/history records are preserved (never deleted).

### 5.4 No `SESSION_COOKIE_AGE` / mixed session-expiry policy (Risk: LOW)

`LoginView.form_valid` already sets a 2-week expiry when "remember" is checked
and a browser-session cookie otherwise. This is good. Gap: nothing documents
the intended server-side absolute session age. Not a blocker; document the
rationale and keep per-login choice.

### 5.5 `apps/core/context_processors.py::theme` returns hardcoded `"light"` (Risk: LOW — UX)

The theme context processor does not read any real preference; it always
returns `"light"`. It is a placeholder. Fine to keep for now but note it so a
real theme preference (from `Profile.locale`/`timezone` or a cookie) can be
wired later without surprise.

### 5.6 Mypy is not clean (Risk: MEDIUM — tooling debt)

Phase 1 files (`apps/accounts/views.py`, `forms.py`, `admin.py`,
`templatetags/form_helpers.py`) have ~59 mypy errors, mostly from Django
stubs typing `request.user` as `User | AnonymousUser`. The tests override
already relax strictness. Fixing is real work; Phase 2 will introduce a
`current_user()` cast helper and type-arg the forms so the *new* partnership
code is mypy-clean and the shared helpers are reused by accounts. Full Phase 1
mypy cleanup is deferred (it does not block this phase's functionality) but is
tracked.

### 5.7 Rate limiting is focused but not centralized (Risk: LOW)

Rate limits exist for auth endpoints. Phase 2 must add limits for invitation
create/resend/accept/reject and reuse the same `ratelimit` module. Configurable
limits (settings) is a Phase 2 goal.

---

## 6. Database issues found

- Schema is minimal and healthy; no destructive concerns. One migration exists.
- `User.email` is `unique + db_index` — correctness is good (index redundant
  with unique, harmless).
- `EmailVerificationToken` has a unique constraint on `(user, purpose)` which
  enforces at most one pending token per purpose — good for preventing
  unbounded token growth. However it is defined *without* a `condition`, so it
  allows only one pending token per purpose even after expiry (an expired row
  still blocks a new one until deleted). The working code deletes the old token
  before creating a new one (`issue_verification_token`), so this is not a bug,
  but the design leans on app-level deletes. Phase 2 invitation tokens will use
  partial (`condition=`) unique constraints to express "one active invitation
  per (scope, status-set)" more precisely at the DB level.
- No indexes exist beyond PKs/uniques. Phase 2 adds indexes only where queries
  justify them (invitation `status+expires`, partnership `status`, membership
  `user` active lookups).

## 7. Concurrency / race-condition posture (to build on)

The codebase currently has little concurrency-sensitive logic (login/register
are naturally idempotent from a data standpoint). Phase 2 introduces
mutual-invitation and two-member races, so it must adopt:

- `transaction.atomic()` around invitation acceptance / partnership activation.
- `select_for_update()` on the partnership + invitation rows so concurrent
  accepts serialise.
- Partial unique constraints that make "one active partnership per user",
  "one active invitation per inviter/address" and "no duplicate active
  invitation between a pair" database-enforced rather than only checked in app
  code.

---

## 8. Definition of the fixes made in Phase 2

The following Phase 0/1 weaknesses are fixed **as part of Phase 2**:

1. **Email delivery off the request path** (5.1) — background-thread send with
   structured failure logging; tests still use the synchronous locmem backend.
2. **Password-change session handling** (5.2) — call `update_session_auth_hash`.
3. **Account lifecycle policy + safe deactivation** (5.3) — deactivation flows
   and documentation; future deletion policy recorded.
4. **Mypy-clean shared helpers** (5.6) — introduce `current_user()` and reuse it
   in accounts + partnerships; new partnership code is mypy-clean. Full legacy
   cleanup deferred.
5. **Configurable rate limits** via settings for partnership sensitive actions.

Everything else is documented as deliberately deferred (see §10).

## 9. Everything intentionally NOT changed (avoid churn)

- Custom user model, email auth, token hashing — correct, keep.
- MAILERS (already the modern API) — keep; only delivery strategy changed.
- The `Theme` context processor placeholder — deferred.
- REST framework and Channels pre-wiring — keep (future API/WS).
- No rewrite of `0001_initial`; new migrations are additive.

## 10. Deferred technical debt (tracked, not blocking)

| Item | Why deferred |
|---|---|
| Full mypy cleanup of legacy accounts files | Real but non-blocking; shared helpers moved to clean code |
| Real theme preference wiring | No product requirement for this phase |
| Absolute `SESSION_COOKIE_AGE` policy | Per-login expiry already implemented; document only |
| Production background queue (Celery/RQ) | Explicitly out of scope; thread-based delivery is adequate now, migration path documented |

## 11. Testing gaps (Phase 2 addresses)

- No partnership / invitation / authorization / concurrency tests (domain is new).
- No explicit test that password change rotates the auth hash (fixed test added).
- No enumeration-resistance tests for invitation flows.
- Token hashing of *invitation* tokens has no test yet (Phase 2 adds it).
