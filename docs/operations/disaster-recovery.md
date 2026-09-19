# Disaster Recovery

**Status: documented procedure, not a rehearsed drill.** No real
infrastructure exists to rehearse against — this describes the intended
response for each failure scenario, informed by how the application is
actually built (what's stateful, what's ephemeral, what the server-
authoritative model guarantees), not a generic template.

## Why the application's own architecture limits blast radius

Before the scenario-by-scenario procedures: the reason several of these
recoveries are simpler than they might otherwise be is architectural, not
incidental. PostgreSQL is the *only* store of confirmed, authoritative data
(`docs/architecture/whiteboard-operations.md`); Redis is provably ephemeral
(`docs/architecture/phase-8-production-audit.md` §14); and every client
independently re-derives its view from the server rather than trusting
cached state (`docs/architecture/offline-architecture.md`). This means most
of these failure modes degrade functionality rather than lose data.

## Scenario: application server failure (ASGI process/host down)

- **Detection**: `/health/live/` stops responding; load balancer/monitoring
  alerts (see `docs/operations/monitoring.md`).
- **Recovery**: restart the process (`docs/operations/production-runbook.md`
  has the exact commands once a process manager is chosen at deployment
  time); if the host itself is lost, redeploy to a new host from the last
  known-good release.
- **Data impact**: none. No application-server-local state is authoritative
  — all confirmed operations are already in PostgreSQL by the time a client
  receives an ack (`transaction.atomic()` commits before any acknowledgement
  or broadcast, see `apps/whiteboard/service.py`).

## Scenario: PostgreSQL failure

- **Detection**: `/health/ready/` returns 503 with `checks.database=false`.
- **Recovery**: restore from the most recent backup (`docs/operations/
  backup-and-restore.md`) onto a new/repaired instance; point
  `POSTGRES_HOST` at it; restart application workers.
- **Data impact**: any operation committed after the last backup is lost —
  this is exactly what a tighter RPO (WAL archiving vs. daily `pg_dump`)
  exists to bound. Operations already acknowledged to clients before the
  outage are safe *if* they were part of the restored backup; the offline
  sync engine (`docs/architecture/offline-architecture.md`) means a client
  that was mid-draw when the failure hit still has its local IndexedDB copy
  and will resubmit once the server is back — idempotent by `operation_id`,
  so this resubmission is safe even if the operation had *also* made it into
  the pre-failure database state.

## Scenario: Redis failure

- **Detection**: `/health/ready/` returns 503 with `checks.cache=false`;
  real-time collaboration stops working (Channels can't broadcast across
  processes) while ordinary HTTP page loads/operation submission keep
  working.
- **Recovery**: restart/repair Redis; no data restore needed — nothing
  authoritative lives there.
- **Data impact**: **none for confirmed operations.** Rate-limit counters
  reset (fails open, not closed — a temporary reduction in abuse protection,
  not a security hole, since authorization checks are independent of rate
  limiting). WebSocket clients fall back to reconnect/retry behavior; the
  HTTP sync path (`docs/architecture/whiteboard-sync-contract.md`) remains
  fully functional as a fallback the whole time.

## Scenario: lost host / corrupted deployment

- **Recovery**: redeploy from the git repository's last known-good commit to
  a fresh host, following `docs/deployment/production-architecture.md`;
  restore PostgreSQL from the latest backup; reconfigure environment
  variables from the deployment's secret store (never from a local copy of
  `.env`, which should never exist off the original host — see the
  `.gitignore` entry and `.env.example`'s own warning).

## Scenario: failed migration

- **Detection**: `manage.py migrate` exits non-zero, or the application
  fails to start against the new schema.
- **Recovery**: per `docs/deployment/database-migrations.md`'s policy —
  take a backup *before* any migration expected to touch a large table.
  If a migration fails partway, Django wraps each migration in a
  transaction on PostgreSQL by default (DDL is transactional on Postgres,
  unlike MySQL), so a failed migration rolls back cleanly in the common
  case; the runbook (`docs/operations/production-runbook.md`) covers
  verifying this and restoring from the pre-migration backup if it doesn't.

## Scenario: bad release / needs rollback

- **Recovery**: redeploy the previous known-good release; if the bad
  release included a forward-only migration, that migration generally
  should **not** simply be reversed blindly (see the migration policy's note
  on additive-first changes) — assess whether the new columns/constraints
  are harmless to leave in place while running the old code, which is
  usually true for purely additive schema changes and is exactly why
  additive-first is the recommended pattern.

## Scenario: expired TLS certificate

- **Detection**: browser/client TLS errors; external monitoring (see
  `docs/operations/monitoring.md`) alerting on certificate expiry *before*
  it happens, ideally 30/14/7 days out.
- **Recovery**: renew via whatever ACME/certificate provider is chosen at
  deployment time; this is entirely a reverse-proxy/infrastructure concern,
  nothing in the Django application itself handles certificates.

## Scenario: lost environment variables

- **Detection**: the application **fails to start** — `config/settings/
  production.py` raises `ImproperlyConfigured` immediately for any of
  `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`,
  `WEBSOCKET_ALLOWED_ORIGINS`, `POSTGRES_PASSWORD` being unset — this is a
  deliberate fail-fast design, not a bug, precisely so this scenario is loud
  and immediate rather than a silent insecure fallback.
- **Recovery**: restore from the deployment's secret manager/vault. This is
  exactly why environment variables must live in infrastructure-managed
  secret storage, never only on one host's local disk.

## Communication

For any incident affecting user-visible availability or data, the minimum
bar: (1) acknowledge internally as soon as detected, (2) once root cause is
understood, communicate expected impact and ETA to affected users if the
outage is ongoing, (3) after resolution, a brief postmortem — see
`docs/operations/incident-response.md` for the fuller process.

## Deferred to actual deployment

None of the above has been rehearsed against a real failure — this is a
considered procedure based on the architecture, not a drilled runbook. A
genuine DR rehearsal (deliberately killing a real production-like Postgres
instance and timing the actual recovery) is deferred until real
infrastructure exists.
