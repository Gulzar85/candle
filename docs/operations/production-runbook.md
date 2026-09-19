# Production Runbook

**Status: documented procedure.** These are the operational commands and
steps for a deployment following `docs/deployment/production-architecture.md`
— written against this application's actual commands (not generic
placeholders), but not yet executed against a real production host.

## Deploy

```bash
# 1. Pull the release (exact mechanism depends on chosen deployment method —
#    git pull, container image pull, etc.)

# 2. Install/update dependencies
pip install -e .              # or `uv sync` if using uv
cd frontend && npm ci && cd ..

# 3. Validate environment — fails loudly if anything required is missing
#    (see config/settings/production.py's ImproperlyConfigured guards)
DJANGO_SETTINGS_MODULE=config.settings.production python manage.py check --deploy

# 4. Run migrations (see docs/deployment/database-migrations.md for the
#    backup-first policy on any migration touching a large table)
python manage.py migrate

# 5. Collect static files (WhiteNoise serves these directly from the ASGI
#    process — this step must run before the new process starts serving)
python manage.py collectstatic --noinput

# 6. Restart the ASGI process (exact command depends on the process manager
#    chosen — see production-architecture.md's daphne/gunicorn+uvicorn note).
#    Prefer a graceful/rolling restart over a hard kill so in-flight
#    requests and open WebSocket connections drain rather than drop.

# 7. Verify health
curl -f https://<host>/health/live/
curl -f https://<host>/health/ready/

# 8. Verify WebSocket connectivity — a plain HTTP health check does not
#    exercise the Channels/Redis path; open the whiteboard page in a real
#    browser and confirm the realtime status indicator reaches "Connected"
#    (see templates/whiteboard/whiteboard.html's status bar).

# 9. Smoke-test a real user flow — log in, open a whiteboard, draw a stroke,
#    confirm it persists on refresh.
```

## Rollback

```bash
# 1. Redeploy the previous known-good release (same deploy mechanism as above).
# 2. Do NOT reflexively reverse the last migration — per
#    docs/deployment/database-migrations.md, additive schema changes are
#    usually harmless to leave in place while running the previous code.
#    Only run `manage.py migrate <app> <previous_migration>` if the new
#    migration is confirmed to break the old code (e.g. it removed a column
#    the old code still reads).
# 3. Restart the ASGI process.
# 4. Repeat the "Verify health" + "Smoke-test" steps from Deploy above.
```

## Restart application

```bash
# Graceful restart (exact command depends on process manager) — prefer this
# over a hard kill so open WebSocket connections and in-flight requests
# drain cleanly rather than dropping mid-operation.
```

## Restart Redis

```bash
# Restarting Redis is safe at any time — no authoritative data lives there
# (see docs/operations/disaster-recovery.md's Redis-failure scenario).
# Real-time collaboration and rate limiting will briefly degrade and recover
# automatically as clients reconnect; no manual application restart needed.
```

## Database backup (ad hoc, outside the automated schedule)

```bash
pg_dump -h <host> -U <user> -d <database> -Fc -f candle_$(date +%Y%m%d_%H%M%S).dump
```

See `docs/operations/backup-and-restore.md` for the full procedure
(verified working against the local dev database) and the restore steps.

## Database restore

See `docs/operations/backup-and-restore.md` in full — do not restore
directly onto the live production database without first confirming the
target is what's intended; restoring into a fresh database and validating
before cutting traffic over is safer than an in-place restore.

## Check application health

```bash
curl -s https://<host>/health/live/ | python -m json.tool
curl -s https://<host>/health/ready/ | python -m json.tool
```

`/health/ready/` returning `{"status": "degraded", "checks": {...}}` names
exactly which dependency (`database` or `cache`) failed — start
investigation there, not with a broad "is everything down" sweep.

## Check WebSocket health

No dedicated endpoint exists for this (see
`docs/operations/health-checks.md`'s explicit note on this gap). Manual
check: open a whiteboard page in a browser, confirm the connection status
indicator reaches "Connected"; or check aggregated `ws.connected`/
`ws.rejected`/`ws.disconnected` log volume (see monitoring.md) for a sudden
drop in successful connections.

## Investigate sync failures

1. Check `/health/ready/` first — a sync failure is often a symptom of a
   DB/cache outage, not a sync-logic bug.
2. Check application logs for `STALE_VERSION`/`operation.rejected` rate —
   a baseline is expected (concurrent drawing); an abnormal spike relative
   to active-user count is the signal to investigate.
3. Ask the affected user whether the whiteboard eventually caught up after
   reconnecting — the offline sync engine is designed to converge
   automatically (`docs/architecture/offline-architecture.md`); a
   *permanent* divergence would indicate an actual bug worth a GitHub issue,
   not just an ops response.

## Investigate database issues

1. `/health/ready/` `checks.database` — confirms connectivity at minimum.
2. Check for long-running or blocked queries (`pg_stat_activity`) if
   requests are slow rather than failing outright.
3. Check disk usage — `WhiteboardOperation` grows append-only (see
   monitoring.md's note on this being the signal for when
   snapshotting/compaction needs to move from deferred to implemented).

## Investigate high memory usage

1. Check whether it correlates with WebSocket connection count (each open
   connection has some per-connection state — rate-limit counters,
   presence tracking — see `apps/whiteboard/consumers.py`) or with request
   volume generally.
2. `MAX_CONNECTIONS_PER_USER = 10` bounds per-user connection count, but
   total connections across *all* users isn't currently capped at the
   process level — worth checking if this is where memory pressure comes
   from at scale.

## Investigate high CPU

1. Check whether it correlates with operation submission rate (stroke
   validation/persistence) or WebSocket message rate.
2. `apps.core.middleware.RequestSizeGuard` rejects oversized bodies before
   they're parsed — confirm this isn't being bypassed somehow if CPU spikes
   correlate with large-payload requests.

## Disable problematic functionality safely

There's no feature-flag system in this codebase. If a specific capability
needs to be disabled in an emergency:
- **Whiteboard writes**: archiving a specific board (`WhiteboardMetadataService.archive()`,
  already wired to the UI) makes it read-only without affecting other boards
  — a targeted, per-board, reversible action, not a global kill switch.
- **New registrations**: no built-in toggle exists; would require a code
  change and redeploy. Not something this runbook can paper over — noted
  honestly as a gap rather than inventing a mechanism that doesn't exist.

## Recover from a failed deployment

1. If the new process never became healthy (`/health/live/` never
   responds), the deploy mechanism should have prevented traffic from
   routing to it in the first place (see "Deploy" step 6-7 above) — verify
   this actually happened before assuming user impact occurred.
2. Roll back per the "Rollback" section above.
3. Investigate the failure against the previous release's logs/environment
   before attempting the deploy again.
