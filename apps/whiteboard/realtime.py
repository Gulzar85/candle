"""Realtime collaboration protocol constants and message builders.

This module holds the WebSocket message vocabulary (see
`docs/architecture/websocket-protocol.md`) and the per-connection safety limits.
Keeping it separate from the consumer makes the protocol easy to test and lets
future mobile clients share the exact same contract.

Protocol layout: every client->server and server->client frame is a JSON object
with a ``type`` field. The protocol is versionable via ``protocol_version``.
"""

from __future__ import annotations

from typing import Any

# The protocol is versionable. Bump this when the message vocabulary or field
# semantics change incompatibly.
PROTOCOL_VERSION = 1

# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------

# Client -> server
C_SYNC_REQUEST = "sync.request"
C_OPERATION_SUBMIT = "operation.submit"
C_PRESENCE_CURSOR = "presence.cursor"
C_PING = "ping"

# Server -> client
S_CONNECTION_READY = "connection.ready"
S_CONNECTION_ERROR = "connection.error"
S_OPERATION_COMMITTED = "operation.committed"
S_OPERATION_REJECTED = "operation.rejected"
S_SYNC_OPS = "sync.ops"
S_SYNC_REQUIRED = "sync.required"
S_PRESENCE_JOINED = "presence.joined"
S_PRESENCE_LEFT = "presence.left"
S_PRESENCE_UPDATE = "presence.update"
S_PONG = "pong"

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

E_AUTH_REQUIRED = "AUTH_REQUIRED"
E_FORBIDDEN = "FORBIDDEN"
E_WHITEBOARD_NOT_FOUND = "WHITEBOARD_NOT_FOUND"
E_WHITEBOARD_ARCHIVED = "WHITEBOARD_ARCHIVED"
E_INVALID_MESSAGE = "INVALID_MESSAGE"
E_INVALID_OPERATION = "INVALID_OPERATION"
E_STALE_VERSION = "STALE_VERSION"
E_RATE_LIMITED = "RATE_LIMITED"
E_PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
E_SYNC_REQUIRED = "SYNC_REQUIRED"
E_SERVER_ERROR = "SERVER_ERROR"

# WebSocket close codes we emit for rejections (application-level, IANA-registered
# 4xxx range). Clients can use these to distinguish terminal from retryable
# close reasons.
CLOSE_POLICY_VIOLATION = 4403  # authn / authz / origin rejection
CLOSE_INVALID_DATA = 4400  # malformed / oversized message
CLOSE_GOING_AWAY = 4401  # access revoked / board archived
CLOSE_UNSUPPORTED = 4405  # protocol version mismatch

# ---------------------------------------------------------------------------
# Per-connection safety limits
# ---------------------------------------------------------------------------

# Maximum size of a single received text frame (bytes). A freehand stroke is
# typically a few KB; this is generous but prevents memory exhaustion.
MAX_MESSAGE_BYTES = 250_000

# Maximum operation payload size (matches limits.MAX_OPERATION_PAYLOAD_BYTES).
MAX_OPERATION_PAYLOAD_BYTES = 100_000

# Rate limiting (per connection): messages and operation submits within a
# sliding window. Generous so normal stylus/mouse drawing is unaffected (one
# completed stroke == one submit), while still blocking flooding.
RATE_MESSAGES_WINDOW = 60  # seconds
RATE_MESSAGES_LIMIT = 600  # max text frames per window
RATE_SUBMITS_WINDOW = 60  # seconds
RATE_SUBMITS_LIMIT = 120  # max operation.submit messages per window

# Presence cursor coalescing target (updates/sec) is enforced client-side; the
# server only bounds total messages via the general rate limit above.

# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def error(code: str, message: str = "", **extra: Any) -> dict[str, Any]:
    """Return a structured ``connection.error`` / ``operation.rejected`` body."""
    return {"type": S_CONNECTION_ERROR, "code": code, "message": message, **extra}


def rejected(code: str, message: str = "", **extra: Any) -> dict[str, Any]:
    """Return an ``operation.rejected`` envelope (used for op-level failures)."""
    return {"type": S_OPERATION_REJECTED, "reason": code, "message": message, **extra}


def committed(
    *,
    operation_id: str,
    sequence: int,
    version: int,
    operation_type: str,
    payload: dict[str, Any],
    actor: dict[str, Any],
    duplicate: bool = False,
    client_id: str | None = None,
    operation_id_echo: str | None = None,
) -> dict[str, Any]:
    """Return an ``operation.committed`` envelope broadcast to the group."""
    body: dict[str, Any] = {
        "type": S_OPERATION_COMMITTED,
        "operation_id": operation_id,
        "sequence": sequence,
        "version": version,
        "duplicate": duplicate,
        "actor": actor,
        "operation": {"operation_type": operation_type, "payload": payload},
    }
    if client_id is not None:
        body["client_id"] = client_id
    if operation_id_echo is not None:
        body["operation_id_echo"] = operation_id_echo
    return body
