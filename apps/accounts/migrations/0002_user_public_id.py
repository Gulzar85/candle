"""Add a stable public_id to User.

A callable default (uuid.uuid4) cannot populate existing rows on its own, and a
unique NOT NULL column cannot be added in one step to a table that has rows, so
this migration:

1. adds `public_id` as nullable,
2. backfills every existing row with a fresh uuid4,
3. alters the column to be NOT NULL and unique.
"""

import uuid

from django.db import migrations, models


def _backfill_public_id(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.all().iterator():
        user.public_id = uuid.uuid4()
        user.save(update_fields=["public_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="public_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(_backfill_public_id, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="public_id",
            field=models.UUIDField(
                default=uuid.uuid4, editable=False, unique=True, db_index=True
            ),
        ),
    ]
