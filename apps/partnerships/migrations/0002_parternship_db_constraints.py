from __future__ import annotations

from typing import Any

from django.db import migrations, models


def install_member_triggers(apps: Any, schema_editor: Any) -> None:
    """Install PostgreSQL triggers enforcing partnership membership invariants.

    Two rules are enforced at the database level so concurrent requests cannot
    bypass application logic:

    1. A partnership can hold **at most two ACTIVE members** (a partnership is
       a two-person boundary).
    2. A user can have **at most one ACTIVE membership** (one active partnership
       per person). This complements the partial unique index added by the
       ``PartnershipMember.Meta`` constraint.

    These are enforced with a BEFORE trigger on ``partnerships_partnershipmember``
    that runs for INSERT and UPDATE.
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    # Guard against double-application by dropping first (idempotent).
    schema_editor.execute("DROP FUNCTION IF EXISTS enforce_partnership_member_rules() CASCADE")
    schema_editor.execute(
        """
        CREATE FUNCTION enforce_partnership_member_rules()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            active_count integer;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.status = NEW.status AND NEW.status <> 'active' THEN
                RETURN NEW;
            END IF;

            IF NEW.status = 'active' THEN
                -- Rule 2: at most one active membership per user.
                PERFORM 1
                FROM partnerships_partnershipmember AS m
                WHERE m.user_id = NEW.user_id
                  AND m.status = 'active'
                  AND m.id <> COALESCE(NEW.id, 0)
                LIMIT 1;

                IF FOUND THEN
                    RAISE EXCEPTION 'user already has an active membership'
                        USING ERRCODE = '23505';
                END IF;

                -- Rule 1: at most two active members per partnership.
                SELECT count(*) INTO active_count
                FROM partnerships_partnershipmember AS m
                WHERE m.partnership_id = NEW.partnership_id
                  AND m.status = 'active'
                  AND m.id <> COALESCE(NEW.id, 0);

                IF active_count >= 2 THEN
                    RAISE EXCEPTION 'partnership already has two active members'
                        USING ERRCODE = '23505';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    schema_editor.execute(
        """
        CREATE TRIGGER partnership_member_rules_trigger
        BEFORE INSERT OR UPDATE OF status, user_id, partnership_id
        ON partnerships_partnershipmember
        FOR EACH ROW
        EXECUTE FUNCTION enforce_partnership_member_rules()
        """
    )


def uninstall_member_triggers(apps: Any, schema_editor: Any) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        "DROP TRIGGER IF EXISTS partnership_member_rules_trigger ON partnerships_partnershipmember"
    )
    schema_editor.execute("DROP FUNCTION IF EXISTS enforce_partnership_member_rules() CASCADE")


class Migration(migrations.Migration):
    dependencies = [
        ("partnerships", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="partnershipmember",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "active")),
                fields=("user",),
                name="uniq_one_active_membership_per_user",
            ),
        ),
        migrations.RunPython(
            install_member_triggers,
            reverse_code=uninstall_member_triggers,
        ),
    ]
