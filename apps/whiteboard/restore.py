"""Restore-to-an-earlier-version (the "revert" feature).

A restore is a normal, forward-moving ``WhiteboardOperation`` —
``RESTORE_VERSION`` — never a deletion or rewrite of history. It goes
through the exact same locked, idempotent, versioned submit path as every
other operation (``WhiteboardOperationService.submit``), so it gets the same
concurrency safety net for free: a stale ``base_version`` is rejected
exactly like any other operation, and a retried submission is deduped by
``operation_id`` exactly like any other operation. No new locking or
idempotency code exists here.

The client only ever supplies ``target_sequence`` — the server computes and
embeds the full snapshot server-side (reusing ``WhiteboardStateBuilder`` via
``selectors.reconstruct_state_at``), which is the one deliberate exception
to "the client builds the payload" that every other operation type follows.
See ``docs/architecture/whiteboard-history.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from . import selectors
from .enums import WhiteboardOperationType
from .errors import InvalidOperationError
from .models import Whiteboard
from .service import SubmitResult, WhiteboardOperationService
from .validator import OperationValidator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.accounts.models import User


class RestoreService:
    """Builds and submits a ``RESTORE_VERSION`` operation."""

    def __init__(self, operation_service: WhiteboardOperationService | None = None) -> None:
        self.operation_service = operation_service or WhiteboardOperationService(
            validator=OperationValidator()
        )

    def restore(
        self,
        whiteboard: Whiteboard,
        actor: User,
        *,
        operation_id: str | UUID,
        base_version: int,
        target_sequence: int,
    ) -> SubmitResult:
        """Restore ``whiteboard`` to the state it had at ``target_sequence``.

        Raises ``InvalidOperationError`` for a structurally nonsensical
        target; raises ``StaleVersionError`` (via the underlying submit) if
        ``base_version`` no longer matches the board's current version —
        the caller should surface that as "board changed, please retry",
        exactly like any other stale-version conflict.
        """
        if target_sequence < 0:
            raise InvalidOperationError("target_sequence must be non-negative.")
        if target_sequence > whiteboard.version:
            raise InvalidOperationError("target_sequence is beyond the current version.")

        snapshot = selectors.reconstruct_state_at(whiteboard, target_sequence)
        objects = [snapshot.objects[object_id] for object_id in snapshot.order]

        operation: dict[str, Any] = {
            "operation_id": str(operation_id),
            "operation_type": WhiteboardOperationType.RESTORE_VERSION,
            "base_version": base_version,
            "payload": {"target_sequence": target_sequence, "objects": objects},
        }
        return self.operation_service.submit(whiteboard, actor, operation)
