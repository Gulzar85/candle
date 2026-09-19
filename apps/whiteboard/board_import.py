"""Whiteboard JSON import (Phase 9).

Named ``board_import`` (not ``import``) to avoid shadowing the reserved
word.

Imported data is never inserted into Postgres directly — it is converted
into a real, version-chained sequence of ``create_stroke`` operations and
submitted through the exact same ``WhiteboardOperationService.submit_batch``
every other operation uses, so it gets the same structural validation,
locking, and idempotency guarantees, not a parallel path. Import is
additive by default: it adds objects on top of whatever currently exists,
mirroring how a human drawing the same content one stroke at a time would
behave — an explicit ``clear_first`` flag composes the existing
``clear_canvas`` primitive for callers that want a replace instead, rather
than inventing new server-side "replace" semantics.

Object ids are always regenerated server-side — client-supplied ids in the
import file are never trusted or reused, avoiding both identity collisions
with existing content and letting imported data dictate identity.

Import bounds (``limits.MAX_IMPORT_OBJECTS``) prevent import specifically
from being a *novel* amplification vector for board size. This does not
resolve the separate, still-open question of an unbounded per-board object
count in general (see ``docs/architecture/phase-8-production-audit.md``,
finding SC-1) — a distinction worth keeping straight, not something to
assume was closed as a side effect.

Accepted tradeoff, not hidden: a multi-chunk import (more than
``limits.MAX_BATCH_OPERATIONS`` objects) is validated atomically upfront,
but each chunk commits independently. A concurrent write from the other
partner between chunks can make a later chunk's assumed base version stale,
raising ``StaleVersionError`` and leaving the import partially applied —
the same class of outcome a human drawing the same content one stroke at a
time would also leave behind on an interruption. Retrying is safe (already
-imported strokes are ordinary board content, re-importing on top only adds
more); this module does not attempt automatic retry.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import limits
from .enums import WhiteboardOperationType
from .errors import InvalidOperationError, InvalidPayloadError, OperationTooLargeError
from .models import Whiteboard
from .service import WhiteboardOperationService
from .validator import OperationValidator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.accounts.models import User

SUPPORTED_SCHEMA_VERSION = 1


@dataclass
class ImportResult:
    imported: int
    version: int


def _clear_op() -> dict[str, Any]:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.CLEAR_CANVAS,
        "base_version": 0,  # placeholder; chained in import_board
        "payload": {},
    }


def _create_stroke_op(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.CREATE_STROKE,
        "base_version": 0,  # placeholder; chained in import_board
        "payload": {
            "object_id": str(uuid.uuid4()),  # never trust the imported id
            "points": obj["points"],
            "color": obj["color"],
            "width": obj["width"],
            "opacity": obj["opacity"],
        },
    }


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class ImportService:
    """Validates and applies a JSON board export (see ``export-json.ts``)."""

    def __init__(self, operation_service: WhiteboardOperationService | None = None) -> None:
        self.operation_service = operation_service or WhiteboardOperationService(
            validator=OperationValidator()
        )
        self._validator = OperationValidator()

    def import_board(
        self,
        whiteboard: Whiteboard,
        actor: User,
        raw: Any,
        *,
        clear_first: bool = False,
    ) -> ImportResult:
        objects = self._validate_envelope(raw)

        operations: list[dict[str, Any]] = []
        if clear_first:
            operations.append(_clear_op())
        operations.extend(_create_stroke_op(obj) for obj in objects)

        # `imported` counts objects, not raw operations applied -- the
        # optional leading clear_canvas op (from clear_first) is bookkeeping,
        # not something a user would count as "an imported drawing". Fresh
        # UUIDs are generated for every op above, so duplicates cannot occur
        # within a single import_board call; `len(objects)` is exact for any
        # call that completes without raising (a raised StaleVersionError
        # aborts before returning, so a partial-chunk failure never reports
        # a misleadingly-full count).
        version = whiteboard.version
        for chunk in _chunks(operations, limits.MAX_BATCH_OPERATIONS):
            for i, op in enumerate(chunk):
                op["base_version"] = version + i
            result = self.operation_service.submit_batch(whiteboard, actor, chunk)
            version = result.version

        return ImportResult(imported=len(objects), version=version)

    def _validate_envelope(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, dict):
            raise InvalidOperationError("Import file must be a JSON object.")

        schema_version = raw.get("schema_version")
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            # Unknown/future versions are rejected outright, never guessed at
            # -- mirrors enums.py's "append, never reinterpret" philosophy.
            raise InvalidPayloadError(
                f"Unsupported schema_version: {schema_version!r}. "
                f"Only {SUPPORTED_SCHEMA_VERSION} is currently supported."
            )

        objects = raw.get("objects")
        if not isinstance(objects, list):
            raise InvalidPayloadError("Import file requires an `objects` array.")
        if len(objects) > limits.MAX_IMPORT_OBJECTS:
            raise OperationTooLargeError(f"Import exceeds {limits.MAX_IMPORT_OBJECTS} objects.")

        for i, obj in enumerate(objects):
            if not isinstance(obj, dict):
                raise InvalidPayloadError(f"objects[{i}] must be an object.")
            # Reuses the exact same per-object validation as create_stroke and
            # restore_version (validator.py) -- reject the whole import
            # atomically on any bad object; nothing is partially applied at
            # this validation stage.
            self._validator._validate_stroke_object_fields(obj)

        return objects
