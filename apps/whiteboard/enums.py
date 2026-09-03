"""Domain enums for the whiteboard app.

Follows the ``apps.partnerships.enums`` pattern: use ``TextChoices`` (built on
Python ``StrEnum``) so a typo is impossible and database values stay stable and
lowercase. New operation types are appended — never renamed — because persisted
rows and future sync clients rely on the string value.
"""

from __future__ import annotations

from django.db import models


class WhiteboardStatus(models.TextChoices):
    """Lifecycle of a whiteboard.

    ACTIVE:   the shared board is open for new operations.
    ARCHIVED: the board is read-only (no new operations), still readable.

    Archive/restore is a partnership-level action; see the versioning doc for
    the exact rules (who may archive, whether operations may be added to an
    archived board).
    """

    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class WhiteboardOperationType(models.TextChoices):
    """Typed operations the server understands and will validate.

    Only the operations Phase 3 actually produces are implemented, mapped to
    object-level mutations so any client can replay them deterministically:

    * ``CREATE_STROKE`` — create one stroke object.
    * ``DELETE_OBJECT`` — delete one object (an eraser gesture is expressed as a
      ``DELETE_OBJECT`` of the originals plus ``CREATE_STROKE`` of the kept
      clipped segments — the same two primitives everyone understands, which
      keeps erase auditable and composable).
    * ``CLEAR_CANVAS`` — clear the whole board (a logical operation, never
      "delete every row").

    The set is kept minimal and open-ended: future types (text/shape/undo,
    collaborative erase-as-undo) are **appended**, never renamed, because
    persisted rows and future sync clients depend on the stable string value.
    """

    CREATE_STROKE = "create_stroke", "Create stroke"
    DELETE_OBJECT = "delete_object", "Delete object"
    CLEAR_CANVAS = "clear_canvas", "Clear canvas"
