"""Server-side whiteboard state reconstruction (deterministic replay).

The server stores an append-only operation ledger and reconstructs the board's
current object state by replaying operations in sequence order. Given the same
initial state and the same ordered operations, replays are identical — which is
exactly what synchronization, offline support, debugging, and auditing depend on
(see ``whiteboard-versioning.md``).

Replay semantics per operation:

* ``CREATE_STROKE``  -> upsert one object by ``object_id`` (moved to the end of
  z-order).
* ``DELETE_OBJECT``  -> remove that ``object_id``.
* ``CLEAR_CANVAS``   -> empty all objects (a logical operation, never a
  per-row delete of history).

Phase 4 keeps snapshots out (see ``whiteboard-persistence.md`` for the measured
threshold that would justify them). The service is written so a snapshot-start
basis can be added later without changing its API.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .enums import WhiteboardOperationType
from .errors import InvalidOperationError


@dataclass
class ReconstructedState:
    """Deterministic object-level state derived by replay.

    ``objects`` maps object_id -> a validated operation payload (the exact dict
    the client submitted, which already encodes the full object). ``order`` is
    the z-order (bottom-to-top) of object ids.
    """

    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


def _stroke_object(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_type": "stroke",
        "object_id": payload["object_id"],
        "points": payload["points"],
        "color": payload["color"],
        "width": payload["width"],
        "opacity": payload["opacity"],
        "creator_id": payload.get("creator_id"),
    }


class WhiteboardStateBuilder:
    """Replays operations (in sequence order) to build a board state."""

    @staticmethod
    def apply(state: ReconstructedState, op_type: str, payload: dict[str, Any]) -> None:
        """Apply one operation in place. Mutates ``state``."""
        if op_type == WhiteboardOperationType.CREATE_STROKE:
            obj = _stroke_object(payload)
            oid = obj["object_id"]
            if oid in state.objects:
                # Re-create: move to top of z-order and refresh.
                state.order.remove(oid)
            state.objects[oid] = obj
            state.order.append(oid)
        elif op_type == WhiteboardOperationType.DELETE_OBJECT:
            oid = payload["object_id"]
            state.objects.pop(oid, None)
            if oid in state.order:
                state.order.remove(oid)
        elif op_type == WhiteboardOperationType.CLEAR_CANVAS:
            state.objects.clear()
            state.order.clear()
        else:  # pragma: no cover - validator blocks unknown types
            raise InvalidOperationError(f"Unknown operation type: {op_type!r}")

    @classmethod
    def replay(
        cls, operations: Iterable[Any], start: ReconstructedState | None = None
    ) -> ReconstructedState:
        """Build state from an ordered iterable of operation records.

        Each record is either a ``WhiteboardOperation`` model instance or a dict
        with ``operation_type`` and ``payload`` keys. Returned state is a fresh
        structure; ``start`` is not mutated.
        """
        state = (
            ReconstructedState(objects=dict(start.objects), order=list(start.order))
            if start
            else ReconstructedState()
        )
        for op in operations:
            op_type = (
                op.operation_type if hasattr(op, "operation_type") else op.get("operation_type")
            )
            payload = op.payload if hasattr(op, "payload") else op.get("payload")
            cls.apply(state, op_type, payload)
        return state


def reconstruct_state(operations: Iterable[Any]) -> ReconstructedState:
    """Convenience: replay an ordered iterable of operations from scratch."""
    return WhiteboardStateBuilder.replay(operations)
