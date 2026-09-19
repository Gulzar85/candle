# Backup & Restore

## Status

**The procedure below was actually run and verified against the local
development database on 2026-09-04** (real command output included). This
confirms the *mechanism* works — `pg_dump`/`pg_restore` correctly round-trip
this application's schema and data. It is **not** a verified production
backup pipeline: no production database exists to back up, and none of the
production-grade concerns (scheduled automation, off-site storage,
encryption at rest, retention enforcement, restore-time SLAs at real data
volume) have been provisioned or tested. Those are listed explicitly as
deferred below.

## What's backed up, and why

**PostgreSQL is the only thing that needs backing up.** Every piece of
durable, authoritative application state lives there — `WhiteboardOperation`
is the append-only source of truth for whiteboard content (see
`docs/architecture/whiteboard-operations.md`), and Redis holds nothing that
isn't safely reconstructable or ephemeral by design (see
`docs/architecture/phase-8-production-audit.md` §14). There is no S3/object
storage in use; the one file-upload surface (avatars) writes to local
`MEDIA_ROOT`, which would need its own backup if deployed on a host without
redundant storage — noted below, not otherwise covered by this doc's
database-focused procedure.

## Procedure (verified locally)

```bash
# Backup — custom format (-Fc) so pg_restore can do selective/parallel restore.
pg_dump -h <host> -U <user> -d <database> -Fc -f candle_backup.dump

# Restore — into a fresh/empty target database.
createdb -h <host> -U <user> <restore_target>
pg_restore -h <host> -U <user> -d <restore_target> --no-owner --no-privileges candle_backup.dump
```

### Actual local run (2026-09-04)

Row counts captured immediately before the backup, against the local dev
`candle` database:

```
accounts_user                  |  4
partnerships_partnership       |  4
whiteboard_whiteboard          |  4
whiteboard_whiteboardoperation | 93
```

`pg_dump -Fc` completed with exit code 0, producing a 146 KB dump file.
`createdb` created a scratch database (`candle_restore_smoketest`).
`pg_restore --no-owner --no-privileges` completed with exit code 0, no
errors. Row counts in the restored scratch database, queried immediately
after:

```
accounts_user                  |  4
partnerships_partnership       |  4
whiteboard_whiteboard          |  4
whiteboard_whiteboardoperation | 93
```

**Identical.** The scratch database and dump file were deleted immediately
after verification — this was a one-time smoke test, not a retained backup.

## Retention policy (recommendation, not yet enforced anywhere)

- **Daily** automated `pg_dump` (or continuous WAL archiving for
  point-in-time recovery, preferable for an RPO tighter than 24h), retained
  30 days.
- **Weekly** snapshot retained 90 days.
- Store off the primary database host — a local-disk-only backup doesn't
  survive the most common failure mode (losing the host entirely).

## Recovery targets (proposed, not yet validated at scale)

- **RPO (Recovery Point Objective)**: with daily `pg_dump`, up to 24h of
  data loss in the worst case. If that's unacceptable, WAL archiving +
  point-in-time recovery gets this to minutes — a real infrastructure
  decision to make at deployment time, not something this repo's code
  determines.
- **RTO (Recovery Time Objective)**: proposed target of under 1 hour from
  "backup identified as needed" to "application serving traffic against
  restored data," for a database at the current scale (hundreds of KB, not
  yet tested at realistic production volume — see
  `docs/performance/whiteboard-benchmarks.md`). This number is a starting
  target, not a measured guarantee — restore time scales with database size
  and hasn't been measured against anything larger than the tiny local dev
  dataset above.

## Media files (avatars)

Not covered by the PostgreSQL procedure above. If deployed on a single host
without redundant storage, `MEDIA_ROOT` (user avatars, re-encoded PNGs,
currently small — 2 MB cap per file, see `apps/accounts/avatars.py`) needs
its own backup or should be moved to redundant object storage. Not
addressed further here since no production media-storage architecture is
chosen yet (see `docs/deployment/production-architecture.md`).

## Deferred to actual deployment

- Scheduled backup automation (cron, managed database provider's built-in
  backups, or WAL archiving) — nothing is scheduled anywhere; the procedure
  above was run once, by hand, as a verification exercise.
- Off-site/redundant backup storage.
- Encryption at rest for backup files.
- A restore drill against a **production-sized** dataset, to get a real RTO
  number instead of the placeholder target above.
- Automated retention/expiry enforcement.
- A restore drill that also exercises the surrounding infrastructure (DNS
  cutover, ASGI process restart, cache warm-up) — the procedure above only
  verified the database layer.
