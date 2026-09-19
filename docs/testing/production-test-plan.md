# Production Test Plan

**Status: this document describes tests that should exist, and honestly
marks which already do vs. don't.** It is not a claim that all of the
following are implemented — see the "Existing coverage" section for what's
actually there today, verified by running the suites, not assumed.

## Existing coverage (verified by running the tests, not assumed)

| Area | Files | Status |
|---|---|---|
| Authentication | `tests/accounts/` (13 files) | Passing — registration, login, logout, password reset/change, verification, email change, profile, dashboard, security. |
| Authorization / partnerships | `tests/partnerships/` (4 files, 53 tests) | Passing — includes explicit IDOR tests (`test_mallory_cannot_view_alice_detail`, `test_mallory_cannot_accept_alice_invitation`, etc. in `test_authorization.py`) and concurrency tests using real threads against Postgres (`test_concurrency.py`). |
| Whiteboard operations | `tests/whiteboard/` (8 files) | Passing — `test_operations.py`, `test_replay.py`, `test_concurrency.py`, `test_views.py`, `test_websocket.py` (14 tests, including WebSocket-specific authorization: `test_connect_rejects_anonymous`, `test_connect_rejects_non_member`, `test_revoked_member_cannot_keep_writing`, `test_ended_partnership_disconnects_passive_listener`). |
| Frontend (TypeScript) | `frontend/src/**/*.test.ts` (11 files, 88 tests) | Passing — engine, state, sync engine, conflict resolver, geometry, viewport, tools, color, network monitor. |
| Integration | `tests/integration/` (2 files) | Present but thin — `test_security.py` is 23 lines, covers basic security headers and CSRF cookie settings only. |

## Genuine gaps (not implemented — listed honestly, not glossed over)

### End-to-end (E2E) browser tests

No Cypress/Playwright/Selenium anywhere in the repository. The full
cross-page user journey below is currently only exercised manually (see
`PHASE-7-FINAL-REPORT.md` and the various phase completion reports for
records of manual QA passes), not by an automated suite.

**Scenario to automate** (User A + User B, matching the original Phase 8
spec's acceptance criteria):

1. User A: sign up → verify email → log in → invite User B → open the
   (empty) whiteboard → draw a stroke → rename the board → log out → log
   back in.
2. User B: accept the invitation → open the board → see User A's stroke
   already there → draw a stroke → confirm User A sees it without
   refreshing (requires two concurrent browser contexts).
3. Offline: User B goes offline (network throttling/`navigator.onLine`
   simulation) → continues drawing → refreshes the browser while still
   offline → confirms the local work is still there (IndexedDB durability,
   `docs/architecture/offline-architecture.md`) → restores network →
   confirms the queued operations sync → confirms both users eventually see
   an identical final state.
4. Security, within the same E2E run: attempt to open User A's whiteboard
   URL as an unrelated third user → confirm 403; attempt to submit an
   operation with a forged `base_version` far ahead of reality → confirm
   `STALE_VERSION`; submit the same `operation_id` twice → confirm only one
   row persists.

### Deeper security integration tests

`tests/integration/test_security.py` covers headers/CSRF but not, as a
dedicated integration suite (some of this is covered piecemeal elsewhere,
listed for completeness of what a *consolidated* security suite would add):
- Malformed/oversized WebSocket operation payloads rejected cleanly (some
  coverage exists in `tests/whiteboard/test_websocket.py`, e.g.
  `test_oversized_message_rejected`, `test_submit_malformed_operation_rejected`
  — a dedicated `test_security.py` addition would consolidate these with the
  HTTP-side equivalents rather than leaving security-relevant tests spread
  across feature test files).
- Open-redirect attempts against every endpoint accepting a `next`/redirect
  parameter.
- Session fixation attempts across login/password-reset/password-change.

### Failure-injection tests

**Partially exercised manually this pass, not automated.** Rather than
stopping the actual local Postgres/Redis services (risking disruption to
other work using the same local instance), the DB- and cache-down code paths
in `health_check` were verified via targeted fault injection: forcing
`django.db.connection.cursor()` and `django.core.cache.cache.set()` to raise,
confirming `/health/ready/` correctly returns `503 {"status": "degraded",
"checks": {"database": false, "cache": ...}}` with the specific failing
dependency named, for each independently.

This investigation surfaced a **real bug, found and fixed**: pointing
`POSTGRES_PORT`/`REDIS_URL` at a genuinely unreachable (not just
connection-refused) address showed neither connection had any configured
timeout — a request could hang for 14+ seconds (compounded further by
Windows' dual-stack resolution of the literal hostname `localhost` trying
both IPv6 and IPv4 addresses in sequence) rather than failing fast, which
defeats the purpose of a readiness probe meant to be cheap and quick. Fixed:
`POSTGRES_CONNECT_TIMEOUT` (default 5s, `config/settings/production.py`) and
Redis `socket_connect_timeout`/`socket_timeout` (default 5s each,
`config/settings/base.py`'s `CACHES["default"]["OPTIONS"]` — verified
against Django's actual `RedisCache` source, since the first attempt used
`django-redis`'s different `CONNECTION_POOL_KWARGS` option shape by mistake
and failed loudly with a clear `TypeError`, which was corrected before
re-verifying). Each timeout was independently confirmed effective by
pointing only *one* dependency at an unreachable address at a time (DB
alone: fails in ~5s; cache alone: fails in ~2-3s, `ECONNREFUSED` on loopback
arriving before the 5s ceiling is even reached).

Not automated as a repeatable test suite, and the broader scenarios below
remain untested:
- Database connection pool exhaustion under concurrent load.
- Redis connection drop *mid-WebSocket-broadcast* (as opposed to Redis being
  unreachable before a connection is even attempted).
- Network partition between the ASGI process and PostgreSQL specifically
  (distinct from Postgres being fully down) — the localhost dual-stack
  timing quirk found above suggests real-network partition behavior is worth
  testing against an actual non-loopback network path before trusting the
  5s figure as a hard production ceiling.

### Load / performance tests

No load-testing code (Locust, k6, or similar) exists. See
`docs/performance/whiteboard-benchmarks.md` for what *was* actually measured
locally (single-process, local-machine operation replay time) versus what
requires a real deployed target to measure honestly (concurrent-user
throughput, WebSocket fan-out latency under real network conditions).

## Priority if implementing these next

1. **E2E scenario above** — highest value, since it exercises the
   cross-cutting behavior (auth → partnership → whiteboard → real-time →
   offline → sync) that unit/integration tests by design can't cover in one
   pass.
2. **Consolidated security integration suite** — most of the underlying
   behavior is already tested elsewhere; this is about assembling a single
   suite that documents "here is where IDOR/CSRF/injection-style attacks are
   proven to fail," which has audit/compliance value beyond the coverage
   itself.
3. **Load testing** — lowest priority until there's a real deployment target
   to run it against; a load test against local dev hardware measures local
   dev hardware, not production capacity, and risks producing a
   false-confidence number.
