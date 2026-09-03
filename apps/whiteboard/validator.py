"""Server-side operation validation.

The server never trusts client JSON just because it came from our own
frontend. This validator checks, per operation:

* the top-level envelope: ``operation_id`` (UUID), ``base_version`` (non-negative
  int), ``operation_type`` (a known type);
* the payload schema, ranges, and limits for that type;
* point count and coordinate structure;
* color format, width, and opacity ranges;
* and that the client has not supplied any server-authoritative field
  (``actor``, ``created_at``, ``sequence``, ``resulting_version``, ``user_id``).

It raises the structured errors from ``errors.py``; it does not write to the
database. The service layer decides authorization/versioning/persistence.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, cast

from . import limits
from .enums import WhiteboardOperationType
from .errors import (
    InvalidOperationError,
    InvalidPayloadError,
    OperationTooLargeError,
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Fields the client must never be able to set — the server owns these.
_FORBIDDEN_FIELDS = frozenset(
    {"actor", "created_at", "sequence", "resulting_version", "user_id", "whiteboard", "public_id"}
)


def _raise_invalid(reason: str) -> None:
    raise InvalidPayloadError(f"Invalid operation payload: {reason}")


class OperationValidator:
    """Validates a single operation dictionary."""

    def __init__(self) -> None:
        self._limits = {
            "point_count": limits.MAX_STROKE_POINTS,
            "payload_bytes": limits.MAX_OPERATION_PAYLOAD_BYTES,
        }

    def validate(self, operation: Any, *, byte_length: int | None = None) -> None:
        """Validate an operation, raising a structured error on failure.

        ``byte_length`` is the raw wire size (JSON bytes) of this operation; pass
        it so we can enforce a hard payload ceiling independently of the size of
        the parsed structure.
        """
        if not isinstance(operation, dict):
            raise InvalidOperationError("Operation must be a JSON object.")

        if byte_length is not None and byte_length > self._limits["payload_bytes"]:
            # Checked against the pre-parse estimate too (see RequestSizeGuard).
            raise OperationTooLargeError()

        self._check_no_forbidden_fields(operation)
        self._check_operation_id(operation)
        self._check_base_version(operation)

        op_type = operation.get("operation_type")
        if op_type not in WhiteboardOperationType.values:
            raise InvalidOperationError(f"Unknown operation_type: {op_type!r}")

        payload = operation.get("payload", {})
        if not isinstance(payload, dict):
            _raise_invalid("payload must be an object")

        self._check_payload_size(payload)
        validator = getattr(self, f"_validate_{op_type.replace('-', '_')}", None)
        if validator is None:  # pragma: no cover - defensive; future types
            raise InvalidOperationError(f"Operation type not implemented: {op_type}")
        validator(payload)

    # ---------------------------------------------------------------- helpers
    def _check_no_forbidden_fields(self, op: dict[str, Any]) -> None:
        present = _FORBIDDEN_FIELDS.intersection(op)
        if present:
            _raise_invalid(f"forbidden server-authoritative field(s): {sorted(present)}")

    def _check_operation_id(self, op: dict[str, Any]) -> None:
        raw = op.get("operation_id")
        if not isinstance(raw, str):
            _raise_invalid("operation_id must be a string UUID")
        try:
            uuid.UUID(raw)
        except ValueError:
            _raise_invalid("operation_id must be a valid UUID")

    def _check_base_version(self, op: dict[str, Any]) -> None:
        base = op.get("base_version")
        if not isinstance(base, int) or isinstance(base, bool) or base < 0:
            _raise_invalid("base_version must be a non-negative integer")

    def _check_payload_size(self, payload: dict[str, Any]) -> None:
        try:
            size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            _raise_invalid("payload must be JSON-serializable")
        if size > self._limits["payload_bytes"]:
            raise OperationTooLargeError()

    # ------------------------------------------------------------ per type
    def _validate_create_stroke(self, payload: dict[str, Any]) -> None:
        object_id = payload.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            _raise_invalid("create_stroke requires a non-empty object_id")

        points = cast(list[dict[str, Any]], payload.get("points"))
        if not isinstance(points, list):
            _raise_invalid("create_stroke requires a non-empty points array")
        if not points:
            _raise_invalid("create_stroke requires a non-empty points array")
        if len(points) < 2:
            _raise_invalid("create_stroke requires at least 2 points")
        if len(points) > self._limits["point_count"]:
            raise OperationTooLargeError(f"Stroke exceeds {self._limits['point_count']} points.")
        for i, p in enumerate(points):
            if not isinstance(p, dict) or not self._is_coord(p):
                _raise_invalid(f"point[{i}] must be {{x, y}} with finite numbers")
            if i and p.get("x") is not None and not isinstance(p.get("x"), (int, float)):
                _raise_invalid(f"point[{i}].x must be a number")

        color = payload.get("color")
        if not isinstance(color, str) or not _HEX_COLOR.match(color):
            _raise_invalid("color must be a 6-digit hex string (e.g. #2563eb)")

        width = payload.get("width")
        if not isinstance(width, (int, float)) or isinstance(width, bool) or not 1 <= width <= 64:
            _raise_invalid("width must be a number in [1, 64]")

        opacity = payload.get("opacity")
        if (
            not isinstance(opacity, (int, float))
            or isinstance(opacity, bool)
            or not 0 <= opacity <= 1
        ):
            _raise_invalid("opacity must be a number in [0, 1]")

        creator_id = payload.get("creator_id")
        if creator_id is not None and not isinstance(creator_id, str):
            _raise_invalid("creator_id must be a string")

    def _validate_delete_object(self, payload: dict[str, Any]) -> None:
        object_id = payload.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            _raise_invalid("delete_object requires a non-empty object_id")

    def _validate_clear_canvas(self, payload: dict[str, Any]) -> None:
        # Clear takes no payload beyond being present; ignore any extra keys to
        # stay forward-compatible, but reject oversized input (checked above).
        pass

    def _is_coord(self, value: dict[str, Any]) -> bool:
        x, y = value.get("x"), value.get("y")
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            return False
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            return False
        return True
