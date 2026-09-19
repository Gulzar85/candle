# Database Migrations — Production Safety

Reviewed every migration in the repository (8 total, across `accounts`,
`partnerships`, `whiteboard`) for reversibility and locking risk before
writing this doc — not a generic policy statement.

## Current migration inventory

| App | Migration | What it does | Locking risk |
|---|---|---|---|
| accounts | `0001_initial` | Creates `User`, `Profile`, `EmailVerificationToken`. | None — new tables. |
| accounts | `0002_user_public_id` | Adds `User.public_id` (unique UUID). | **Reviewed, safe by design** — see below. |
| accounts | `0003_profile_accent_color_profile_bio_profile_pronouns` | Adds three nullable/constant-default `Profile` fields. | Low — constant defaults, metadata-only on Postgres 11+. |
| partnerships | `0001_initial` | Creates `Partnership`, `PartnershipMember`, `PartnershipInvitation`, `PartnershipEvent`, `Notification`. | None — new tables. |
| partnerships | `0002_parternship_db_constraints` | Adds a partial unique index + installs a PL/pgSQL trigger (`enforce_partnership_member_rules`). | Low — `CREATE TRIGGER`/`CREATE FUNCTION` are metadata operations; briefly takes `ACCESS EXCLUSIVE` on the table but doesn't rewrite it. Idempotent (`DROP FUNCTION IF EXISTS ... CASCADE` before create). |
| whiteboard | `0001_initial` | Creates `Whiteboard`. | None — new table. |
| whiteboard | `0002_whiteboardoperation_whiteboard_archived_at_and_more` | Creates `WhiteboardOperation`; adds `Whiteboard.archived_at`/`last_operation_at`/`status`/`title`/`version`. | Low — every `AddField` uses a **constant** default (`"active"`, `0`, `"Shared board"`, `dict`, or `null=True`), which Postgres 11+ applies as a metadata-only change, not a full table rewrite. |
| whiteboard | `0003_remove_whiteboardoperation_idx_op_board_seq_and_more` | Drops two redundant indexes, removes a redundant `db_index=True`. | None — dropping an index/altering a field to remove a duplicate index declaration is metadata-only. |
| whiteboard | `0004_alter_whiteboardoperation_operation_type` *(Phase 9)* | Widens `WhiteboardOperationType.choices` to include `move_object`/`resize_object`/`restore_version`. | None — `choices=` on a plain `CharField` is Django-level metadata only, not a Postgres-enforced constraint; this migration touches zero table structure or data. Zero-lock, zero-downtime. |

## The one migration worth calling out: `accounts/0002_user_public_id`

This is the pattern to follow for any future "add a unique NOT NULL column to
a populated table" migration — a single-step `AddField(unique=True,
null=False)` would fail outright against existing rows, and Django's
callable `default=uuid.uuid4` doesn't retroactively backfill existing rows
with *distinct* values in one step. The actual migration does it in three
explicit phases:

1. `AddField(public_id, null=True)` — safe, metadata-only add.
2. `RunPython(_backfill_public_id, migrations.RunPython.noop)` — assigns a
   fresh UUID to every existing row via `.iterator()` + individual
   `.save(update_fields=["public_id"])` calls.
3. `AlterField(public_id, null=False, unique=True, db_index=True)` — now
   safe because every row already has a value.

The reverse migration is explicitly `RunPython.noop` (documented, not left
implicit) — backfilled UUIDs can't be meaningfully "un-backfilled," so the
migration honestly declares itself irreversible for the data step rather than
pretending otherwise.

**Scaling note for future migrations of this shape**: step 2's per-row
`.save()` loop is fine at the current user-table size; if a future backfill
targets a table large enough for this to matter, prefer batched
`bulk_update()` calls over a `.iterator()` chunk instead of one UPDATE per
row — not needed today, called out so it isn't forgotten later.

## General migration policy for this project

- **Never edit an already-applied migration.** New schema changes get a new
  migration; migration history is treated as an append-only log, matching
  how `WhiteboardOperation` itself treats domain history.
- **Prefer additive changes.** Adding a nullable column or one with a
  constant default is safe under Postgres 11+ without a table rewrite.
  Adding a `NOT NULL` column to a populated table always needs the
  three-phase pattern above (nullable add → backfill → tighten).
- **Renaming or dropping a column** in a live system needs a two-deploy
  sequence in practice (stop writing to/reading the old name in application
  code first, deploy, *then* migrate it away) — not because Django can't
  generate the migration, but because a single combined deploy+migrate has a
  window where old application code (if a rolling deploy is still mid-flight)
  could reference a column that no longer exists. No migration in this
  repo currently does this, but it's the rule to follow when one eventually
  does.
- **Before running against a real production database**: take a backup
  first (see `docs/operations/backup-and-restore.md`), and for any migration
  expected to touch a large table, test its actual runtime against a
  production-sized copy rather than assuming from the migration's Python
  code that it'll be fast — Postgres's exact locking behavior interacts with
  live table size and concurrent traffic in ways `makemigrations` output
  alone doesn't reveal.
- **Deploy step ordering**: run `python manage.py migrate` before restarting
  application workers onto new code that expects the new schema, and after
  static files are collected (migrations don't depend on static files, but
  keeping a fixed order avoids ambiguity in the deploy script — see
  `docs/operations/production-runbook.md`).

## What's deferred to an actual deployment

This review is static analysis of the migration files as they exist today —
genuinely useful and complete for that purpose, but it is not the same as
having run these migrations against a production-sized, production-shaped
database under concurrent load. That verification is deferred until a real
environment exists (see `docs/production-readiness-report.md`).
