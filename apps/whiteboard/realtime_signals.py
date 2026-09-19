"""Push a re-authorization check into an already-open whiteboard group.

The WebSocket consumer re-validates authorization on every message the client
sends (see ``consumers.py::_still_authorized``), which closes the *write* gap
for a revoked member. It does nothing for a member who is only passively
connected (never sends a message) — they would otherwise keep receiving
broadcasts of the board through a socket opened before their access was
revoked, for as long as they leave the tab open.

This module closes that gap: when something outside the WebSocket
request/response cycle changes a connection's authorization (today, only "the
partnership ended"), every connection in that whiteboard's group is asked to
re-check *itself*. A connection that is still authorized is unaffected; only
one that is no longer authorized closes. Call this via
``transaction.on_commit`` so it only fires once the authorizing change is
durably committed (see ``whiteboard-versioning.md`` / consumer docstring for
the same "broadcast only after commit" rule applied to ordinary operations).
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from . import realtime as rt
from .enums import WhiteboardOperationType

logger = logging.getLogger("apps.whiteboard.realtime")


def request_reauthorization(partnership_public_id: str) -> None:
    """Ask every connection on this partnership's whiteboard to re-check access."""
    layer = get_channel_layer()
    if layer is None:  # pragma: no cover - channel layer always configured
        return
    async_to_sync(layer.group_send)(
        f"whiteboard.{partnership_public_id}",
        {"type": "whiteboard.reauthorize"},
    )


def notify_restore(
    partnership_public_id: str,
    *,
    operation_id: str,
    sequence: int,
    version: int,
    target_sequence: int,
    actor: dict[str, Any],
) -> None:
    """Broadcast a completed restore to every connection on this board.

    Deliberately strips the full snapshot (``objects``) from what goes over
    the wire. A restore payload is bounded by
    ``limits.MAX_RESTORE_PAYLOAD_BYTES`` (5MB by default — real measured
    snapshot sizes run roughly 150 bytes/object, so even a moderately large
    board's snapshot routinely exceeds ``realtime.MAX_MESSAGE_BYTES``
    (250KB), the hard WebSocket frame ceiling; see
    ``docs/performance/phase-9-benchmarks.md``). Every client that receives
    this — live, or via ``sync.ops`` catch-up on reconnect — is expected to
    do a full state refetch (``GET /api/whiteboards/<id>/``) rather than try
    to apply the deliberately incomplete payload locally. See
    ``docs/architecture/whiteboard-history.md``.

    Called from the (synchronous) restore HTTP view, outside any WebSocket
    request/response cycle — same pattern as ``request_reauthorization``.
    """
    layer = get_channel_layer()
    if layer is None:  # pragma: no cover - channel layer always configured
        return
    async_to_sync(layer.group_send)(
        f"whiteboard.{partnership_public_id}",
        {
            "type": "whiteboard.op",
            "payload": rt.committed(
                operation_id=operation_id,
                sequence=sequence,
                version=version,
                operation_type=WhiteboardOperationType.RESTORE_VERSION,
                payload={"target_sequence": target_sequence},
                actor=actor,
            ),
        },
    )


def notify_operation_committed(
    partnership_public_id: str,
    *,
    operation_id: str,
    sequence: int,
    version: int,
    operation_type: str,
    payload: dict[str, Any],
    actor: dict[str, Any],
    duplicate: bool = False,
) -> None:
    """Broadcast a committed operation to every connection on this board.

    Used by the synchronous HTTP submit path so a partner connected over the
    live WebSocket sees writes that arrived over HTTP — e.g. while the author's
    own socket was reconnecting/not yet open, its writes fall back to HTTP
    (``pushHttp``) instead of the wire, and without this broadcast those writes
    would stay invisible to live partners until they next resync.

    Mirrors exactly the WebSocket consumer's post-commit ``group_send``
    (``consumers._on_operation_submit``): the same ``whiteboard.op`` gateway and
    the same ``rt.committed`` envelope, so every group member — including the
    author's own other tabs, which deduplicate by ``operation_id`` — parses the
    identical payload. Callers must invoke it only after the service's
    transaction has committed (same "broadcast only after commit" rule).
    """
    layer = get_channel_layer()
    if layer is None:  # pragma: no cover - channel layer always configured
        return
    async_to_sync(layer.group_send)(
        f"whiteboard.{partnership_public_id}",
        {
            "type": "whiteboard.op",
            "payload": rt.committed(
                operation_id=operation_id,
                sequence=sequence,
                version=version,
                operation_type=operation_type,
                payload=payload,
                actor=actor,
                duplicate=duplicate,
            ),
        },
    )
