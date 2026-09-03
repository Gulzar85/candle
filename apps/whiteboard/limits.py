"""Configurable limits for whiteboard operation handling.

All limits are tunable via Django settings (with sensible defaults) so hard
values can be raised/lowered per environment without code changes.

These protect the server from oversized/runaway payloads while staying well
above realistic drawing behaviour (an average freehand stroke is a few hundred
points at most).
"""

from __future__ import annotations

from django.conf import settings


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


# JSON payload per operation, after decoding, in bytes.
MAX_OPERATION_PAYLOAD_BYTES = _setting("WHITEBOARD_MAX_OPERATION_PAYLOAD_BYTES", 100_000)

# Maximum points allowed in one stroke.
MAX_STROKE_POINTS = _setting("WHITEBOARD_MAX_STROKE_POINTS", 4_000)

# Maximum number of operations in a single batch request.
MAX_BATCH_OPERATIONS = _setting("WHITEBOARD_MAX_BATCH_OPERATIONS", 50)

# Maximum request body size for the operations endpoint (bytes).
MAX_REQUEST_BODY_BYTES = _setting("WHITEBOARD_MAX_REQUEST_BODY_BYTES", 250_000)

# Retained server history is bounded at the DB level by this (see data-retention
# notes). 0 means unlimited.
MAX_RETAINED_OPERATIONS = _setting("WHITEBOARD_MAX_RETAINED_OPERATIONS", 0)
