"""Whiteboard domain boundary.

Phase 3 deliberately keeps the drawing **local to the browser**. This model only
establishes that a whiteboard is a shared resource owned by a ``Partnership``
(the Phase 2 security boundary). Nothing in this model stores canvas pixels or a
drawing blob: the drawing is a *logical, operation-based* model that Phase 4 will
persist as a sequence of drawing operations (not a screenshot).

The whiteboard is created lazily the first time either partner opens it, so no
rows exist for partnerships that were never used. Authorizing access is entirely
the responsibility of the server through the partnership membership (see
``apps.whiteboard.policies`` / ``apps.partnerships.policies``).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.partnerships.models import Partnership

from .enums import WhiteboardOperationType, WhiteboardStatus


class Whiteboard(models.Model):
    """A shared drawing surface belonging to one, well-defined partnership.

    A whiteboard is a **security boundary**: every partner may access it, everyone
    else gets a 403. It scopes operation persistence and (later) real-time
    synchronization to a partnership, never to a single user.

    Phase 4 turns this from a pure "lazy row" into the revision root of the
    operation ledger:

    * ``version`` is the board's server-authoritative revision and equals the
      sequence of the most recently applied operation (monotonic, never re-used).
    * ``status`` is ``ACTIVE`` or ``ARCHIVED``; an archived board is **read-only**
      — the service refuses to append operations to it.
    * ``last_operation_at`` records when the most recent operation landed (for
      admin/UX; it is **not** used for ordering — ``sequence`` is).
    * ``title`` is a human label; the board belongs to the partnership regardless
      of who created it. Individual actions are attributed per-operation via
      ``WhiteboardOperation.actor``.

    No canvas pixels or a giant state blob are ever stored here — the board is
    reconstructed by replaying ``WhiteboardOperation`` rows.
    """

    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    partnership = models.OneToOneField(
        Partnership,
        on_delete=models.CASCADE,
        related_name="whiteboard",
    )
    title = models.CharField(max_length=120, blank=True, default="Shared board")
    status = models.CharField(
        max_length=16,
        choices=WhiteboardStatus.choices,
        default=WhiteboardStatus.ACTIVE,
    )
    version = models.PositiveBigIntegerField(default=0, editable=False)
    last_operation_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "whiteboard"
        verbose_name_plural = "whiteboards"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="idx_wb_status"),
        ]

    def __str__(self) -> str:
        return self.title or f"Whiteboard {self.public_id}"

    @property
    def is_archived(self) -> bool:
        return self.status == WhiteboardStatus.ARCHIVED


class WhiteboardOperation(models.Model):
    """One immutable, authoritatively-ordered mutation of a whiteboard.

    This is the **primary source of truth**. A whiteboard's state is reconstructed
    by replaying these rows in ``(whiteboard, sequence)`` order. Operations are
    append-only: deleting / editing history is never done (see the operations doc
    — including how erase and clear are represented as first-class operations).

    Idempotency: ``operation_id`` is supplied by the client (UUID string) and is
    unique within a whiteboard, so a retried submission returns the *existing*
    row instead of creating a duplicate. Concurrency is handled by the service
    layer (row-locked atomic commit), not by this model.

    Server-authoritative fields: ``actor``, ``created_at``, ``sequence``,
    ``base_version`` and ``resulting_version`` are all determined by the server
    and never taken from the client.
    """

    id = models.BigAutoField(primary_key=True)
    whiteboard = models.ForeignKey(
        Whiteboard,
        on_delete=models.CASCADE,
        related_name="operations",
    )
    # Client-supplied, unique within the board (idempotency key). Not used as the
    # ordering key — ``sequence`` is.
    operation_id = models.UUIDField(db_index=True)
    sequence = models.PositiveBigIntegerField(editable=False)
    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="whiteboard_operations",
    )
    operation_type = models.CharField(max_length=32, choices=WhiteboardOperationType.choices)
    # Compact, validated payload (stroke geometry, clear marker, ...). Stored
    # as JSONB on PostgreSQL, as TEXT on SQLite (Django's JSONField is
    # portable either way). Never stores the whole board state.
    payload = models.JSONField(default=dict, blank=True)
    # The board version the client observed when it made this op (used to detect
    # STALE_VERSION). Purely informational for Phase 4.
    base_version = models.PositiveBigIntegerField(default=0, editable=False)
    # The board version after this op was applied (server-authoritative; equals
    # ``whiteboard.version`` at the time of commit).
    resulting_version = models.PositiveBigIntegerField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "whiteboard operation"
        verbose_name_plural = "whiteboard operations"
        ordering = ("sequence",)
        constraints = [
            # Deterministic ordering + no duplicate sequence within a board.
            models.UniqueConstraint(
                fields=["whiteboard", "sequence"],
                name="uniq_op_sequence_per_board",
            ),
            # Idempotency: one row per client operation id per board.
            models.UniqueConstraint(
                fields=["whiteboard", "operation_id"],
                name="uniq_op_id_per_board",
            ),
        ]
        indexes = [
            # No explicit (whiteboard, sequence) index here: the
            # uniq_op_sequence_per_board UniqueConstraint above already
            # creates one covering exactly that query pattern (ORDER BY
            # sequence WHERE whiteboard_id = X for replay) — a second,
            # non-unique index on the same leading columns would only add
            # write overhead with zero read benefit.
            models.Index(fields=["whiteboard", "operation_type"], name="idx_op_board_type"),
            models.Index(fields=["whiteboard", "created_at"], name="idx_op_board_created"),
        ]

    def __str__(self) -> str:
        return f"Op {self.operation_type} #{self.sequence} on board {self.whiteboard_id}"
