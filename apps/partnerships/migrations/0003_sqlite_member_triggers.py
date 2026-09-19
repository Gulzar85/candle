from __future__ import annotations

from typing import Any

from django.db import migrations

TRIGGER_NAMES = (
    "partnership_member_rule2_insert",
    "partnership_member_rule2_update",
    "partnership_member_rule1_insert",
    "partnership_member_rule1_update",
)


def install_sqlite_member_triggers(apps: Any, schema_editor: Any) -> None:
    """SQLite equivalent of the PostgreSQL triggers from migration 0002.

    SQLite has no stored procedures, so the same two rules (at most one
    active membership per user; at most two active members per partnership)
    are expressed as four small BEFORE triggers instead of one function. The
    WHEN clause on each only fires the check when the row is becoming (or
    staying) active, mirroring the short-circuit in the PostgreSQL function.
    """
    if schema_editor.connection.vendor != "sqlite":
        return

    for name in TRIGGER_NAMES:
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}")

    # Rule 2: at most one active membership per user.
    schema_editor.execute(
        """
        CREATE TRIGGER partnership_member_rule2_insert
        BEFORE INSERT ON partnerships_partnershipmember
        FOR EACH ROW
        WHEN NEW.status = 'active' AND EXISTS (
            SELECT 1 FROM partnerships_partnershipmember
            WHERE user_id = NEW.user_id AND status = 'active'
        )
        BEGIN
            SELECT RAISE(ABORT, 'user already has an active membership');
        END
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER partnership_member_rule2_update
        BEFORE UPDATE OF status, user_id, partnership_id
        ON partnerships_partnershipmember
        FOR EACH ROW
        WHEN NEW.status = 'active' AND EXISTS (
            SELECT 1 FROM partnerships_partnershipmember
            WHERE user_id = NEW.user_id AND status = 'active' AND id <> NEW.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'user already has an active membership');
        END
        """
    )

    # Rule 1: at most two active members per partnership.
    schema_editor.execute(
        """
        CREATE TRIGGER partnership_member_rule1_insert
        BEFORE INSERT ON partnerships_partnershipmember
        FOR EACH ROW
        WHEN NEW.status = 'active' AND (
            SELECT count(*) FROM partnerships_partnershipmember
            WHERE partnership_id = NEW.partnership_id AND status = 'active'
        ) >= 2
        BEGIN
            SELECT RAISE(ABORT, 'partnership already has two active members');
        END
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER partnership_member_rule1_update
        BEFORE UPDATE OF status, user_id, partnership_id
        ON partnerships_partnershipmember
        FOR EACH ROW
        WHEN NEW.status = 'active' AND (
            SELECT count(*) FROM partnerships_partnershipmember
            WHERE partnership_id = NEW.partnership_id AND status = 'active' AND id <> NEW.id
        ) >= 2
        BEGIN
            SELECT RAISE(ABORT, 'partnership already has two active members');
        END
        """
    )


def uninstall_sqlite_member_triggers(apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "sqlite":
        return

    for name in TRIGGER_NAMES:
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {name}")


class Migration(migrations.Migration):
    dependencies = [
        ("partnerships", "0002_parternship_db_constraints"),
    ]

    operations = [
        migrations.RunPython(
            install_sqlite_member_triggers,
            reverse_code=uninstall_sqlite_member_triggers,
        ),
    ]
