# Rate Limits — Reference

Consolidated, current reference for every rate-limited surface in the
application. Domain-specific rationale lives alongside the relevant feature
docs (`docs/security/invitation-security.md` for partnership actions,
`docs/architecture/websocket-protocol.md` for the WebSocket limits) — this
page exists so an operator or auditor can see every limit in one place
without hunting through source.

All HTTP/account limiters use `apps.accounts.ratelimit` — a small,
cache-backed sliding-window counter (Redis in production, LocMemCache in
dev/test), not a third-party package. `check(scope, key, limit, window,
cooldown)` allows up to `limit` requests within `window` seconds, then
rejects with a 429 for `cooldown` seconds. Verified current values by reading
the actual constants in code, not by trusting a prior description.

## Account / authentication (`apps/accounts/views.py::RATE_LIMITS`)

| Action | Limit | Window | Cooldown | Key |
|---|---|---|---|---|
| Login | 6 | 5 min | 5 min | IP + email |
| Register | 5 | 1 hour | 15 min | IP |
| Password reset request | 3 | 15 min | 15 min | IP + email |
| Password reset confirm | 6 | 15 min | 15 min | IP + uidb64 |
| Email verification resend | 3 | 15 min | 10 min | IP + email |

Login and password-reset-request are enumeration-resistant regardless of the
rate limit: both always return the same generic response whether or not the
address matches an account (see `docs/security/invitation-security.md` for
the same principle applied to invitations).

## Partnership actions (`PARTNERSHIP_RATE_LIMITS`, `config/settings/base.py`)

| Action | Limit | Window |
|---|---|---|
| Invite (create/resend) | 5 | 1 hour |
| Invite resend | 5 | 1 hour |
| Invite accept | 10 | 15 min |
| Invite reject | 10 | 15 min |
| Invite revoke | 5 | 1 hour |

Keyed per-user. See `docs/security/invitation-security.md` §6 for the
original source of this table and the threat it mitigates (token
brute-forcing is already infeasible given 256-bit tokens; this is
defense-in-depth against automated abuse).

## Whiteboard operations — HTTP (`apps/whiteboard/api.py`)

| Action | Limit | Window | Cooldown |
|---|---|---|---|
| Operation submission (POST) | 60 | 60 s | 10 s |

Keyed per authenticated user. Sized for legitimate fast freehand drawing —
one completed stroke is one operation, not one request per pointer sample
(see `docs/architecture/whiteboard-operations.md`), so 60/min comfortably
covers real usage while still bounding abuse.

## Whiteboard operations — WebSocket (`apps/whiteboard/realtime.py`)

| Action | Limit | Window | Scope |
|---|---|---|---|
| General messages (any frame) | 600 | 60 s | per connection |
| Operation submits | 120 | 60 s | per connection |
| Simultaneous connections | 10 | — | per user (`MAX_CONNECTIONS_PER_USER`, `apps/whiteboard/consumers.py`) |

The per-connection message/submit limits are enforced independently per
socket; the per-user connection cap is what actually bounds a single user's
total fan-out potential (10 connections × 120 submits/min, not unbounded).

## Configuring limits

Account and whiteboard-HTTP limits are hardcoded dict/constant values in
their respective modules (not currently settings-driven) except
`PARTNERSHIP_RATE_LIMITS`, which is a Django setting with the values above as
defaults and can be overridden per-environment. Changing an account or
whiteboard-HTTP limit today means editing the constant directly — a
deliberate simplicity choice (see `apps/accounts/ratelimit.py`'s own
docstring: "a small focused solution... rather than a heavyweight third-party
security framework") rather than an oversight; promoting them to settings is
a reasonable future increment if per-environment tuning becomes necessary.

## What is *not* rate-limited (by design, not oversight)

- Read-only page views (dashboard, profile, partnership home) — no
  state-changing action, no abuse surface beyond ordinary HTTP load, which
  `apps.core.middleware.RequestSizeGuard` and normal infra-level throttling
  (reverse proxy, WAF) are the appropriate layer for, not application code.
- `logout` / `logout_all_devices` — these reduce attacker capability rather
  than create it; rate-limiting them would only create a self-inflicted
  denial-of-service risk for the legitimate user.
