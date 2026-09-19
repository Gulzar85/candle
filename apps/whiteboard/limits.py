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

# NOTE: the request body size limit is enforced globally by
# apps.core.middleware.RequestSizeGuard (setting: MAX_REQUEST_BODY_BYTES,
# default 5 MB — see .env.example), not by a whiteboard-specific constant here.
# A prior whiteboard-only MAX_REQUEST_BODY_BYTES=250_000 constant lived in this
# module but was never wired to the actual enforcing middleware (which reads a
# differently-named global setting), so it silently did nothing while two docs
# wrongly described it as the enforced limit. Removed rather than wired to the
# tighter 250 KB, since 250 KB would incorrectly reject a legitimate full-size
# batch: MAX_OPERATION_PAYLOAD_BYTES (100 KB) x MAX_BATCH_OPERATIONS (50) is
# consistent with the middleware's real 5 MB default, not the removed 250 KB.

# Retained server history is bounded at the DB level by this (see data-retention
# notes). 0 means unlimited.
MAX_RETAINED_OPERATIONS = _setting("WHITEBOARD_MAX_RETAINED_OPERATIONS", 0)

# restore_version embeds a full server-computed snapshot in its payload, so
# it needs a much higher ceiling than an ordinary op — but still bounded,
# and never sent over the WebSocket wire (see
# docs/architecture/whiteboard-history.md), so this is a DB/JSONField-only
# ceiling, not a wire-format one, and can be sized generously.
#
# Real measured snapshot sizes (docs/performance/phase-9-benchmarks.md):
# ~150 bytes/object, so 1,000 objects -> ~150KB, 20,000 objects (this
# module's own MAX_RESTORE_OBJECTS ceiling) -> ~3MB worst case. 5MB matches
# the existing global MAX_REQUEST_BODY_BYTES default and comfortably covers
# a full MAX_RESTORE_OBJECTS snapshot with headroom to spare.
MAX_RESTORE_PAYLOAD_BYTES = _setting("WHITEBOARD_MAX_RESTORE_PAYLOAD_BYTES", 5_000_000)

# Cap on the number of objects a restore snapshot may contain, independent of
# the byte ceiling above (cheap insurance against many-tiny-objects payloads
# that stay under the byte cap but blow up iteration cost).
MAX_RESTORE_OBJECTS = _setting("WHITEBOARD_MAX_RESTORE_OBJECTS", 20_000)

# Cap on the number of objects a single JSON import may contain. Bounds
# import specifically as a novel amplification vector; it does not resolve
# the separate, still-open question of an unbounded per-board object count
# (see docs/architecture/phase-8-production-audit.md, finding SC-1).
MAX_IMPORT_OBJECTS = _setting("WHITEBOARD_MAX_IMPORT_OBJECTS", 2_000)
