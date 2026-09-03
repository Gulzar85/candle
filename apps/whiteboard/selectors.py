"""Read-side query layer for whiteboards (selector pattern).

Centralizes reads so complex authorization + data-shape queries live in one
place instead of being duplicated across views/API/WebSockets. A whiteboard is
keyed 1:1 to a partnership, so lookups start from the partnership's
``public_id`` (never a client-supplied whiteboard id), and authorization is
always the caller's concern via ``apps.whiteboard.policies``.

All operation reads are ordered by ``sequence`` — never ``created_at`` — because
sequence is the deterministic ordering key.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from django.db.models import QuerySet

from apps.partnerships.models import Partnership

from .models import Whiteboard, WhiteboardOperation
from .state_reconstruction import ReconstructedState, reconstruct_state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import WhiteboardOperation as WhiteboardOperationType


def whiteboard_for_partnership(partnership: Partnership) -> Whiteboard:
    """Return the whiteboard for ``partnership``, creating it lazily if absent."""
    whiteboard, _ = Whiteboard.objects.get_or_create(partnership=partnership)
    return whiteboard


def whiteboard_by_public_id(public_id: str) -> QuerySet[Whiteboard]:
    """Queryset for a whiteboard by its public UUID (used with get_object_or_404)."""
    return Whiteboard.objects.filter(public_id=public_id)


def whiteboard_by_partnership_public_id(public_id: str) -> Partnership | None:
    """Return the Partnership whose public_id maps to a whiteboard, or None."""
    return Partnership.objects.filter(public_id=public_id).first()


def operations_for_whiteboard(
    whiteboard: Whiteboard,
    *,
    after_sequence: int = 0,
    limit: int | None = None,
) -> QuerySet[WhiteboardOperationType]:
    """Operations for a board, in deterministic sequence order.

    ``after_sequence`` supports incremental sync (fetch ops with sequence >
    after_sequence); ``limit`` bounds a single page. This is the Phase 5/6
    incremental-sync seam.
    """
    qs = WhiteboardOperation.objects.filter(
        whiteboard=whiteboard, sequence__gt=after_sequence
    ).order_by("sequence")
    if limit is not None:
        qs = qs[:limit]
    return qs


def latest_version(whiteboard: Whiteboard) -> int:
    """Return the board's current server version (== last applied sequence)."""
    return whiteboard.version


def get_whiteboard_state(whiteboard: Whiteboard) -> ReconstructedState:
    """Reconstruct the current object state by full replay of all operations."""
    ops = WhiteboardOperation.objects.filter(whiteboard=whiteboard).order_by("sequence")
    return reconstruct_state(ops)


def operation_to_dict(op: WhiteboardOperationType) -> dict[str, Any]:
    """Serialize a single operation for API responses.

    Only safe, stable fields are included — no internal ids beyond the public
    operation_id, and payloads are returned as-is (they were validated on write).
    """
    return {
        "operation_id": str(op.operation_id),
        "sequence": op.sequence,
        "operation_type": op.operation_type,
        "base_version": op.base_version,
        "resulting_version": op.resulting_version,
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "actor_id": op.actor_id,
        "payload": op.payload,
    }


def iter_operations_by_id(
    whiteboard: Whiteboard, operation_ids: Iterable[Any]
) -> dict[str, WhiteboardOperation]:
    """Map operation_ids -> existing rows for a board (idempotency lookups)."""
    ids = set(operation_ids)
    return {
        str(op.operation_id): op
        for op in WhiteboardOperation.objects.filter(whiteboard=whiteboard, operation_id__in=ids)
    }
