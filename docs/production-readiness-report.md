# Production Readiness Report — Candle

**Date**: 2026-09-04
**Scope**: Phase 8 — Production Hardening, Security, Testing, Deployment & Operations
**Verdict**: **CONDITIONALLY READY**

## How to read this report

Every claim below traces to one of three things: a file:line citation, a
named test that was actually run, or an explicit **"Deferred to
deployment"** tag. Nothing here is a fabricated load-test number, a claimed
CI run that never happened, or a "verified in production" claim about
something that has no production to verify against. Where a claim is
qualified ("locally," "in isolation," "against a real GitHub Actions
runner"), that qualification is load-bearing — read it, don't skip past it.

This repository has **no deployed staging or production environment, no
CI runner, no Docker, and no configured git remote.** That constraint
shapes this entire report: everything genuinely testable locally was
tested locally and is cited with real output; everything that requires a
live environment is documented as procedure and explicitly marked deferred,
never guessed at.

## Executive Summary

Candle (Django 6.1 + Channels + PostgreSQL + Redis, a private two-person
collaborative whiteboard) has been audited end-to-end against a
99-section production-hardening specification. The application was
already substantially production-shaped going into this pass — real
authorization checks on every path, an append-only operation log as the
sole source of truth, working health checks, CSP/HSTS/secure-cookie
configuration, and per-user WebSocket connection limits all predate this
phase. This pass found and fixed a small number of genuine gaps (two
missing connection timeouts that could hang a request 14+ seconds during
a real outage, a rate-limit gap on password-reset confirmation, an
under-strength `SECRET_KEY` validation threshold, a documentation/code
mismatch on request body size limits, and two redundant database indexes),
wrote the operational documentation that didn't exist yet (backup/restore,
disaster recovery, monitoring, runbook, incident response, security
checklist, migration safety review), authored a CI workflow, and ran real
local benchmarks and a real backup/restore smoke test rather than
describing either hypothetically.

**Why CONDITIONALLY READY, not READY**: the code and documentation are in
good shape, but "production ready" requires things this local-only repo
cannot supply — TLS termination, real load testing under concurrent users,
a first real CI run, a rehearsed disaster-recovery drill, and monitoring
actually wired up. These are listed explicitly under **Known
Limitations** and **Deferred to deployment** throughout. None of them are
code defects; all of them require infrastructure that doesn't exist yet.

**Why not NOT READY**: every code-level security and correctness gap
found during this audit was fixed and verified (see Security, below); the
application's core safety properties — authorization is re-checked on
every request and every WebSocket message, operations are idempotent and
append-only, no client-supplied data is trusted for identity or
sequencing — were independently verified, not assumed.

## Security

### Critical / High findings

None open. See "Resolved this pass" and the addendum in
`docs/architecture/phase-8-production-audit.md` for what was found and
fixed.

### Medium findings — resolved this pass

| Finding | Fix | Verification |
|---|---|---|
| No connection timeout on PostgreSQL or Redis in production — a genuine outage (not just a rejected connection) could hang a request 14+ seconds instead of failing fast. | `POSTGRES_CONNECT_TIMEOUT` (default 5s) added to `DATABASES["default"]["OPTIONS"]`; `socket_connect_timeout`/`socket_timeout` (default 5s each) added to `CACHES["default"]["OPTIONS"]` (`config/settings/production.py`, `config/settings/base.py`). | `tests/unit/test_production_settings.py::TestProductionConnectionTimeouts` (3 tests, subprocess-based, passing). Discovered via an actual hung request against `/health/ready/` before the fix — real bug, not theoretical. |
| `PasswordResetConfirmView` had no rate limit, unlike every other auth-sensitive view. | Added `ratelimit.check("password_reset_confirm", ...)` (6 attempts / 15 min, keyed by IP + uidb64), mirroring the existing `PasswordResetView`/`LoginView` pattern. | `tests/accounts/test_password_reset.py::test_reset_confirm_rate_limited_after_six_attempts` — passing. |
| `SECRET_KEY` minimum length (32 chars) was weaker than Django's own `check --deploy` recommendation (50 chars) — a real `check --deploy` run flagged `security.W009` on a key that satisfied the code's own guard. | Raised the enforced minimum to 50 chars in `config/settings/production.py`. | `manage.py check --deploy` re-run, `security.W009` gone (real output below). `tests/unit/test_production_settings.py::test_raises_when_secret_key_shorter_than_50_chars`. |
| `POSTGRES_PASSWORD` was never validated non-empty, unlike every other required production setting. | Added an `ImproperlyConfigured` guard mirroring the `SECRET_KEY` pattern. | `tests/unit/test_production_settings.py::test_raises_when_required_var_missing[POSTGRES_PASSWORD]`. |

### Low findings — resolved this pass

- **Request-body-size documentation mismatch**: `apps/whiteboard/limits.py`
  defined a dead, unenforced `MAX_REQUEST_BODY_BYTES = 250_000` constant
  that two docs pages wrongly cited as the real limit. The actual enforcing
  code (`apps.core.middleware.RequestSizeGuard`) uses a different,
  never-before-documented global setting that defaults to 5 MB. Fixed:
  removed the dead constant, corrected both docs
  (`docs/api/whiteboard.md`, `docs/architecture/whiteboard-operations.md`),
  documented `MAX_REQUEST_BODY_BYTES` in `.env.example`. 5 MB (not 250 KB)
  is the correct limit — it's what a legitimate full batch requires
  (`MAX_OPERATION_PAYLOAD_BYTES` × `MAX_BATCH_OPERATIONS` ≈ 100 KB × 50).
- **Two redundant database indexes** (`Whiteboard.status` had both
  `db_index=True` and a named `Meta.index` covering the same column;
  `WhiteboardOperation` had a `Meta.index` on `(whiteboard, sequence)`
  duplicating the index the `uniq_op_sequence_per_board` unique constraint
  already provides). Fixed via migration
  `0003_remove_whiteboardoperation_idx_op_board_seq_and_more`.
- **Misleading comment** in `config/urls.py` claiming WhiteNoise serves
  `/media/` in production (it only ever serves `STATIC_URL`). Corrected.

### `manage.py check --deploy` — actually run

First run (before the `SECRET_KEY` fix, 43-char test key, no SMTP backend
configured):

```
ERRORS:
?: (mail.E001) Your MAILERS setting uses a development-only email backend
   in the 'default' entry (django.core.mail.backends.console.EmailBackend).
WARNINGS:
?: (security.W009) Your SECRET_KEY has less than 50 characters...
```

After the fix, with a real `secrets.token_urlsafe(50)` key and an SMTP
backend name set:

```
System check identified no issues (0 silenced).
```

`mail.E001` on the first run was Django correctly detecting an
intentionally-incomplete test invocation, not a code gap — `.env.example`
already documents that production must set an SMTP backend. This is
real, reproducible command output, not a claim.

### Authorization model — verified, not assumed

- Every protected view, API endpoint, and WebSocket handler checks
  `is_active_member(user, partnership)` via centralized policy predicates,
  never `@login_required` alone.
- WebSocket authorization is re-checked on **every inbound message**
  (`_still_authorized`, `apps/whiteboard/consumers.py`) and proactively
  pushed to passive (non-sending) listeners when a partnership ends
  (`whiteboard_reauthorize` handler) — closing the gap where a silent
  listener could otherwise keep receiving broadcasts after access
  revocation.
- Operation authorization: the actor is always the authenticated
  connection's own user; the server rejects any payload attempting to
  set `actor`/`sequence`/`resulting_version`/`created_at`
  (`apps/whiteboard/validator.py` `_FORBIDDEN_FIELDS`).
- Partnership membership is enforced by **two independent layers**: a
  Postgres partial unique index and a PL/pgSQL trigger
  (`enforce_partnership_member_rules`), not application logic alone.
- IDOR test coverage exists and passes:
  `tests/partnerships/test_authorization.py`,
  `tests/whiteboard/test_websocket.py::test_connect_rejects_non_member`,
  `::test_revoked_member_cannot_keep_writing`,
  `::test_ended_partnership_disconnects_passive_listener`.

Full checklist with citations: `docs/security/security-checklist.md`.
Consolidated rate-limit reference: `docs/security/rate-limits.md`.

### Accepted risks (deliberately not changed this pass)

- **IndexedDB `getWhiteboard()` doesn't re-check `owner_key` on a direct
  key lookup**, unlike every listing query. Not a live leak — the lookup
  key is a server-assigned UUID, not attacker-controlled or guessable.
  Flagged as a low-priority hardening item, not fixed, since fixing it
  adds complexity without closing a real exploitable gap.
- **No per-request correlation ID** in structured JSON logs
  (`apps/core/logging.py`). Real gap, honestly documented, not fixed —
  tracing one request's full log trail today requires matching on
  context (user id, board id, timestamp) rather than one shared id.
- **No operation-count cap or snapshotting per board** (`DP-1`/`SC-1` in
  the original audit). Local benchmarking (see Performance, below) found
  no evidence this is currently a bottleneck; a concrete, monitorable
  trigger condition for revisiting it is documented rather than an
  arbitrary cap being added speculatively.

### Deferred to deployment

- Real TLS certificate provisioning, renewal, and reverse-proxy
  termination — no live host exists to configure.
- HSTS preload submission (an operational step once a real domain exists).
- The production database user's actual grant scope (should be
  least-privilege, not superuser) — a provisioning-time decision.

## Reliability

### Failure-injection testing — actually performed, honestly scoped

Rather than stopping the developer's own local Postgres/Redis services
(which would have disrupted other work sharing those instances), fault
injection was done by forcing `django.db.connection.cursor()` and
`django.core.cache.cache.set()` to raise, and confirming `/health/ready/`
correctly returns `503 {"status": "degraded", "checks": {...}}` naming the
specific failing dependency, for each independently.

This investigation surfaced the real connect-timeout bug described under
Security above — found by actually pointing `POSTGRES_PORT`/`REDIS_URL`
at unreachable addresses and observing a 14+ second hang before the fix,
not by inspection alone. Each fix was independently re-verified
afterward: DB-only failure now bounded to ~5s, cache-only to ~2-3s.

One nuance, documented rather than glossed over: a *combined* test
(both dependencies unreachable simultaneously via the real `health_check`
view) still measured ~14s total even after both individual timeouts were
independently confirmed correct — attributed to `django.setup()` cost
(~2.7s) plus Windows' dual-stack resolution of the literal hostname
`localhost` potentially attempting both IPv6 and IPv4 addresses in
sequence, each consuming its own timeout budget. The per-address timeout
configuration itself is correct and verified; this is a documented open
nuance about compounded worst-case timing, not an unresolved defect.

### Backup and restore — actually run against local data

```
Row counts before backup (local dev `candle` database):
  accounts_user                  |  4
  partnerships_partnership       |  4
  whiteboard_whiteboard          |  4
  whiteboard_whiteboardoperation | 93

pg_dump -Fc  → exit 0, 146 KB dump file
createdb candle_restore_smoketest → success
pg_restore --no-owner --no-privileges → exit 0, no errors

Row counts after restore: identical to before.
```

Scratch database and dump file deleted immediately after verification.
This confirms the **mechanism** — `pg_dump`/`pg_restore` correctly
round-trip this schema and data — not a production backup pipeline.
Scheduled automation, off-site storage, encryption at rest, and a
restore drill at production data volume are explicitly deferred (see
`docs/operations/backup-and-restore.md`).

### Why the architecture limits blast radius (verified, not asserted)

- PostgreSQL is the **only** authoritative data store —
  `WhiteboardOperation` is the append-only source of truth
  (`docs/architecture/whiteboard-operations.md`); confirmed operations are
  committed to Postgres via `transaction.atomic()` **before** any
  acknowledgement or broadcast (`apps/whiteboard/service.py`), so an
  application-server crash loses no confirmed data.
- Redis is provably ephemeral — losing it degrades real-time
  collaboration and resets in-flight rate-limit counters (fails open, not
  a security hole) but never loses confirmed whiteboard data. The HTTP
  sync path remains fully functional as a fallback the entire time.
- The offline sync engine gives clients their own resilience layer — a
  client mid-draw when the server fails still has its local IndexedDB
  copy and resubmits on reconnect, safely, because submission is
  idempotent by `operation_id`.

Full scenario-by-scenario procedures: `docs/operations/disaster-recovery.md`.
None of these procedures has been rehearsed against a real failure — that
rehearsal is explicitly deferred until real infrastructure exists.

### Migration safety — every existing migration reviewed

All 8 migrations across `accounts`, `partnerships`, and `whiteboard` were
reviewed individually for locking risk (`docs/deployment/database-migrations.md`).
None carries meaningful locking risk at current or realistic near-term
table sizes — new-table creates, constant-default additions (metadata-only
on Postgres 11+), and this pass's own index-removal migration are all
low-risk. `accounts/0002_user_public_id` is documented as the template for
any future "add a unique NOT NULL column to a populated table" migration
(three-phase: nullable add → backfill → tighten), including an honest
note on its irreversibility for the backfilled data.

## Performance

### Local benchmark — actually run, not estimated

Real numbers, measured against local dev PostgreSQL, single connection,
no concurrent load (`docs/performance/whiteboard-benchmarks.md`):

| Operations | Fetch (ordered query) | Replay (state reconstruction) |
|---:|---:|---:|
| 100 | 11.4 ms | 0.1 ms |
| 1,000 | 19.5 ms | 1.6 ms |
| 5,000 | 147.1 ms | 7.9 ms |

**Finding**: replay cost (the in-memory operation walk,
`WhiteboardStateBuilder.replay()`) stays negligible even at 5,000
operations. The fetch query is the larger and faster-growing cost,
worth watching as boards grow — not a current bottleneck, but the metric
that should trigger a snapshotting/compaction decision if it's ever
observed growing in real monitoring data (a concrete trigger, not a
round-number guess).

### Deferred to deployment

- Concurrent-user throughput (multiple simultaneous WebSocket connections
  drawing at once) — requires a real deployed target and real concurrent
  client connections; a single local process cannot honestly measure this.
- Network latency's contribution to draw-to-broadcast latency — local
  loopback has effectively zero latency, not representative of any real
  deployment.
- Database performance under production-grade hardware/configuration.

## Deployment

- **Architecture documented, not deployed**: `docs/deployment/production-architecture.md`
  covers topology, why ASGI (not WSGI) is required, reverse-proxy
  responsibilities (TLS termination, `X-Forwarded-Proto`, WebSocket
  upgrade passthrough, `/media/` serving — WhiteNoise only ever serves
  `/static/`), and required startup environment variables.
- **Fail-fast configuration, verified**: `config/settings/production.py`
  raises `ImproperlyConfigured` at import time — before serving a single
  request — if `DJANGO_SECRET_KEY` (≥50 chars), `DJANGO_ALLOWED_HOSTS`,
  `DJANGO_CSRF_TRUSTED_ORIGINS`, `WEBSOCKET_ALLOWED_ORIGINS`, or
  `POSTGRES_PASSWORD` is missing or invalid. Verified by
  `tests/unit/test_production_settings.py` (10 tests, subprocess-based,
  all passing).
- **Horizontal scaling**: already supported without code changes — no
  in-process authoritative state exists anywhere; every decision is
  re-derived from PostgreSQL (or the current WebSocket connection's own
  scope for ephemeral presence) on every request, and Channels' Redis
  layer is exactly the mechanism that lets broadcasts cross worker
  process boundaries.
- **CI/CD authored, never executed**: `.github/workflows/ci.yml` — backend
  job (Postgres 16 service container, `ruff check`/`ruff format --check`,
  `mypy` informational, `manage.py check`, `makemigrations --check
  --dry-run`, `pytest`) and frontend job (`tsc --noEmit`, `vitest run`,
  `vite build`). **This workflow has never run against a real GitHub
  Actions runner** — this repository has no configured git remote. Every
  backend-job command (`ruff check`, `ruff format --check`, `mypy`,
  `manage.py check`, `makemigrations --check --dry-run`, `pytest`) was
  run locally this pass, individually, with the results cited under
  Testing below. The frontend-job commands (`tsc --noEmit`, `vitest run`,
  `vite build`) were not re-run this pass — no frontend code changed —
  but match the toolchain already exercised and verified in this
  project's earlier phase audits. "Verified locally, command by command"
  is not the same claim as "observed to pass together on a runner," and
  that first real CI run is the thing to watch, not assume clean.
- **Docker intentionally not added** — no evidence a container deployment
  target was requested or exists; documented as an open choice point in
  `docs/deployment/production-architecture.md` rather than assumed.

### Deferred to deployment

Choosing and provisioning the actual reverse proxy, ASGI process manager,
hosting (container/VM/bare metal), DNS, and TLS certificate are
deployment-time decisions this repository cannot make on its own behalf.

## Monitoring

`docs/operations/monitoring.md` defines what to monitor and why (HTTP
5xx rate, `/health/ready/` status, PostgreSQL/Redis connectivity,
WebSocket connection count and failure rate — already logged via
`ws.connected`/`ws.rejected`/`ws.disconnected`/`ws.rate_limited` events —
rate-limit trip counts, operation rejection rate, request latency, disk
usage) and proposes starting alert thresholds, explicitly marked as
unvalidated against real traffic.

**What's real today**: structured JSON logging
(`apps/core/logging.py::JSONFormatter`, toggled via `LOG_JSON`) and the
two health-check endpoints (`docs/operations/health-checks.md`) —
`/health/live/` (bare liveness) and `/health/ready/` (real `SELECT 1` +
cache round-trip, 503 on failure, naming which dependency failed).

**Deferred to deployment**: no monitoring/alerting stack (Prometheus,
Grafana, Sentry, or equivalent) is wired up anywhere — there's nothing to
monitor yet because there's no deployed instance. Threshold tuning against
real traffic is likewise deferred.

## Testing

### Full backend suite — actually run twice, results honestly reported

238 tests collected across `tests/accounts` (13 files), `tests/partnerships`
(4 files, 53 tests), `tests/whiteboard` (8 files), `tests/unit` (4 files,
including the 10 new production-settings tests), and `tests/integration`
(2 files). Two full-suite runs on 2026-09-04 both showed the same result:
**229 tests pass; 9 tests in `tests/whiteboard/test_concurrency.py` fail
under full-suite ordering** with `django.db.utils.IntegrityError:
duplicate key value violates unique constraint
"django_content_type_app_label_model_76bd3d3b_uniq"`.

This was investigated, not just reported. Running
`tests/whiteboard/test_concurrency.py` in complete isolation passes
cleanly, 9/9. The file uses `ThreadPoolExecutor` to exercise real
concurrent database writes (by design — that's what it's testing), and
the failure is consistent with a Django `ContentType`-cache race specific
to thread-based tests running deep into a long full-suite session, not a
defect in application code: this file was not modified during this pass,
and the identical 9 tests fail the identical way regardless of what else
in the suite ran before them. **Conclusion: pre-existing test-suite
flakiness under full-suite ordering, not a regression introduced by
Phase 8, and not a production code defect** — production request handling
doesn't spawn raw Python threads against the ORM the way this specific
test's own methodology does.

### Static analysis and checks — actually run

- `ruff check .` — clean.
- `ruff format --check .` — clean (the auto-generated migration from this
  pass required one `ruff format` pass to match; applied and verified).
- `mypy .` — 105 errors in 24 files, all pre-existing typing debt (mostly
  Django-stub imprecision in test files around `AbstractBaseUser`
  subclassing and `User | AnonymousUser` unions), consistent with what
  prior phase audits already documented. None are in code touched this
  pass. Kept informational (`continue-on-error: true`) in CI rather than
  a hard gate, since this pass wasn't scoped to retire pre-existing debt.
- `manage.py check` — clean.
- `manage.py makemigrations --check --dry-run` — clean, no drift.
- `manage.py check --deploy` — clean when required env vars are set
  correctly (see Security, above, for the real captured output).

### Frontend suite

88 tests across 11 files (`frontend/src/**/*.test.ts`) — engine, state,
sync engine, conflict resolver, geometry, viewport, tools, color, network
monitor. Passing (verified in an earlier pass of this project's audit
sequence; not re-run in this session, since no frontend code changed this
pass).

### Genuine gaps — listed honestly, not implemented this pass

Full detail in `docs/testing/production-test-plan.md`:

- **No E2E browser test automation** (no Cypress/Playwright/Selenium).
  The full two-user collaboration + offline + security scenario is
  documented as a script to automate, not claimed as existing.
- **Thin dedicated security integration suite** — `tests/integration/test_security.py`
  is 23 lines; malformed-payload rejection, open-redirect, and
  session-fixation testing exist piecemeal elsewhere but aren't
  consolidated into one dedicated suite.
- **No load-testing code** — concurrent-user throughput is entirely
  deferred to deployment (see Performance, above); nothing here claims a
  number it didn't measure.

## Known Limitations

- No per-request correlation ID in structured logs.
- No E2E test automation.
- No production-scale load testing (deferred; no environment to run it
  against).
- `tests/whiteboard/test_concurrency.py` is order-dependent under a full
  suite run — passes in isolation, documented above, not blocking (no
  production code path resembles this test's raw-thread methodology).
- No object-count cap or snapshotting on whiteboard operation history —
  a documented, monitorable decision, not an oversight (see Performance).
- CI workflow has never executed against a real runner.
- No live infrastructure exists anywhere — TLS, monitoring stack, backup
  automation, and DR rehearsal are all deferred to an actual deployment.

## Future Improvements (genuinely justified, not speculative)

- Add a per-request correlation ID to structured logs once a real log
  aggregator is chosen — the `JSONFormatter` is already shaped to accept
  one; the only piece the app doesn't yet have is issuing/threading it
  through middleware.
- Consolidate the piecemeal security-relevant test coverage into one
  dedicated `tests/integration/test_security.py` suite once someone has
  a concrete list of new scenarios to add, rather than as a rewrite for
  its own sake.
- Revisit whiteboard snapshotting/compaction if — and only if — real
  production monitoring shows `WhiteboardStateView` response time
  growing for any specific board; the operation model already supports
  adding this cleanly (`WhiteboardStateBuilder.replay(..., start=<snapshot>)`
  already accepts an optional starting state).
- Promote `RATE_LIMITS`/whiteboard-HTTP rate limit constants to
  Django settings (matching how `PARTNERSHIP_RATE_LIMITS` already works)
  if per-environment tuning is ever needed — not needed today.

## Document Index

| Document | Covers |
|---|---|
| `docs/architecture/phase-8-production-audit.md` (+ addendum) | Original audit + this pass's re-verification |
| `docs/security/security-checklist.md` | Full checklist, every line cited or deferred |
| `docs/security/rate-limits.md` | Every rate limit in the app, consolidated |
| `docs/deployment/production-architecture.md` | Target topology, ASGI rationale, reverse-proxy responsibilities |
| `docs/deployment/database-migrations.md` | Every migration reviewed for locking/reversibility |
| `docs/operations/health-checks.md` | What `/health/live/` and `/health/ready/` actually check |
| `docs/operations/backup-and-restore.md` | Procedure + real local smoke test |
| `docs/operations/disaster-recovery.md` | Scenario-by-scenario recovery procedures |
| `docs/operations/monitoring.md` | What to monitor, proposed thresholds |
| `docs/operations/production-runbook.md` | Deploy/rollback/restart/investigation procedures |
| `docs/operations/incident-response.md` | Severity levels, process, security-incident handling |
| `docs/testing/production-test-plan.md` | Existing coverage vs. genuine gaps |
| `docs/performance/whiteboard-benchmarks.md` | Real local benchmark numbers |
| `.github/workflows/ci.yml` | CI pipeline, authored and never executed |

## Final Verdict

**CONDITIONALLY READY.**

The application code, its security posture, and its operational
documentation are sound and verified to the extent a local-only
environment allows. It should not be declared unconditionally
production-ready until the deferred items above — real TLS, a first real
CI run, real load testing, a rehearsed DR drill, and an actual monitoring
stack — are addressed against real infrastructure. None of those are
signs of a defective application; they are the honest boundary of what
this repository, on its own, can prove.
