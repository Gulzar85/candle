"""Whiteboard real-time consumer (Django Channels, WebSocket).

The WebSocket is a *transport*, not the source of truth. Every operation the
client sends is handed to the authoritative ``WhiteboardOperationService``
(sync, transactional, row-locked, idempotent); the consumer only parses the
protocol, enforces connection-level safety (origin, auth, authz, size, rate
limits) and broadcasts the *committed* result to the whiteboard group.

Guarantees:
  * broadcast happens only after the operation has been committed to Postgres
    (the sync service returns only once its ``transaction.atomic`` committed);
  * the actor is taken from the authenticated connection, never the client;
  * authorization is re-checked per message so a revoked member cannot keep
    writing through an already-open socket.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, cast

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from apps.accounts.models import User
from apps.partnerships.policies import safe_display_name
from apps.partnerships.selectors import get_partner
from apps.whiteboard.policies import can_view_whiteboard

from . import realtime as rt
from . import selectors
from .errors import (
    InvalidOperationError,
    WhiteboardAPIError,
)
from .models import Whiteboard
from .service import WhiteboardOperationService
from .validator import OperationValidator

logger = logging.getLogger("apps.whiteboard.realtime")

# Rate-limiter error code -> message.
_RATE_MSG = "Too many messages. Slow down and try again."
_SUBMIT_MSG = "Too many operations. Slow down and try again."

# Per-user connection cap within a single worker process.  Limits broadcast
# fan-out amplification from a single user opening many sockets.
MAX_CONNECTIONS_PER_USER = 10
_user_connection_counts: dict[int, int] = {}  # user.pk -> count


def _build_user_public(user: User) -> dict[str, Any]:
    """The minimal public identity exposed to the authorized group.

    Sync and DB-touching (``safe_display_name`` lazily fetches ``user.profile``),
    so it must only be called from a worker thread via ``database_sync_to_async``.
    """
    return {
        "public_id": str(user.public_id),
        "display_name": safe_display_name(user),
    }


async def _user_public(user: User) -> dict[str, Any]:
    pub = await database_sync_to_async(_build_user_public)(user)
    return cast(dict[str, Any], pub)


class WhiteboardConsumer(AsyncWebsocketConsumer):  # type: ignore[misc]
    group_prefix = "whiteboard"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.partnership = None
        self.whiteboard: Whiteboard | None = None
        self.group_name = ""
        self._msg_times: list[float] = []
        self._submit_times: list[float] = []

    # ------------------------------------------------------------------ utils
    def _origin_allowed(self) -> bool:
        origin = None
        for name, value in self.scope.get("headers", []):
            if name == b"origin":
                origin = value.decode("utf-8", "ignore")
                break
        allowed = set(getattr(settings, "WEBSOCKET_ALLOWED_ORIGINS", []) or [])
        # An empty allowlist, or an explicit "*", permits any origin. The
        # development default is "*" (see development.py) so device/LAN testing
        # is not silently degraded to HTTP-only; production requires an explicit
        # allowlist and never uses "*".
        if "*" in allowed or not allowed:
            return True
        # No Origin header (non-browser clients) is permitted only when the
        # origin allowlist is empty; otherwise it must match.
        if not origin:
            return False
        return origin in allowed

    def _rate_limited(self, window: int, limit: int, times: list[float], now: float) -> bool:
        cutoff = now - window
        times[:] = [t for t in times if t > cutoff]
        if len(times) >= limit:
            return True
        times.append(now)
        return False

    # ------------------------------------------------------------------ wire
    async def connect(self) -> None:
        if not self._origin_allowed():
            logger.warning("ws.rejected origin=present public_id=%s", self._url_public_id())
            await self.close(code=rt.CLOSE_POLICY_VIOLATION)
            return

        await self.accept()

        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            logger.info("ws.rejected reason=unauth public_id=%s", self._url_public_id())
            await self._send_error(rt.E_AUTH_REQUIRED, "Authentication required.")
            await self.close(code=rt.CLOSE_POLICY_VIOLATION)
            return

        ok, code, message = await self._authorize(user)
        if not ok:
            await self._send_error(code, message)
            await self.close(
                code=rt.CLOSE_POLICY_VIOLATION if code == rt.E_FORBIDDEN else rt.CLOSE_INVALID_DATA
            )
            return

        self.user: User = user
        self.group_name = f"{self.group_prefix}.{self._url_public_id()}"

        if not _reserve_connection_slot(user.pk):
            logger.warning("ws.connection_limit user=%s", user.public_id)
            await self._send_error(rt.E_RATE_LIMITED, "Too many connections.")
            await self.close(code=rt.CLOSE_POLICY_VIOLATION)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)

        logger.info(
            "ws.connected public_id=%s user=%s",
            self._url_public_id(),
            self.user.public_id,
        )

        # Broadcast my presence to everyone already connected.
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "whiteboard.presence",
                "payload": {
                    "type": rt.S_PRESENCE_JOINED,
                    "user": await _user_public(self.user),
                },
            },
        )

        # Tell me who I am, who my partner is, and the current server version so
        # the client can set its initial sync baseline and render the roster.
        partner = await database_sync_to_async(get_partner)(user, self.partnership)
        version = await database_sync_to_async(selectors.latest_version)(self.whiteboard)
        await self.send(
            text_data=json.dumps(
                {
                    "type": rt.S_CONNECTION_READY,
                    "protocol_version": rt.PROTOCOL_VERSION,
                    "connection_id": self.channel_name,
                    "version": version,
                    "user": await _user_public(self.user),
                    "partner": await _user_public(partner) if partner else None,
                }
            )
        )

    async def disconnect(self, code: int) -> None:
        if getattr(self, "user", None) is not None:
            _release_connection_slot(self.user.pk)
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            if getattr(self, "user", None) is not None:
                logger.info(
                    "ws.disconnected public_id=%s user=%s",
                    self._url_public_id(),
                    self.user.public_id,
                )
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        "type": "whiteboard.presence",
                        "payload": {
                            "type": rt.S_PRESENCE_LEFT,
                            "user": await _user_public(self.user),
                        },
                    },
                )

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        # Size guard before parsing.
        if text_data and len(text_data.encode("utf-8")) > rt.MAX_MESSAGE_BYTES:
            logger.info("ws.oversized public_id=%s", self._url_public_id())
            await self._send_error(rt.E_PAYLOAD_TOO_LARGE, "Message too large.")
            return

        now = time.time()
        if self._rate_limited(
            rt.RATE_MESSAGES_WINDOW, rt.RATE_MESSAGES_LIMIT, self._msg_times, now
        ):
            logger.warning("ws.rate_limited public_id=%s", self._url_public_id())
            await self._send_error(rt.E_RATE_LIMITED, _RATE_MSG)
            return

        try:
            message = json.loads(text_data or "")
        except (json.JSONDecodeError, TypeError):
            logger.info("ws.bad_json public_id=%s", self._url_public_id())
            await self._send_error(rt.E_INVALID_MESSAGE, "Invalid JSON.")
            return

        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            await self._send_error(
                rt.E_INVALID_MESSAGE, "Message must be a JSON object with a type."
            )
            return

        handler = getattr(self, f"_on_{message['type'].replace('.', '_')}", None)
        if handler is None:
            await self._send_error(
                rt.E_INVALID_MESSAGE, f"Unsupported message type: {message['type']}"
            )
            return

        # Re-authorize on every message so revoked access cannot keep writing.
        if not await self._enforce_still_authorized():
            return

        await handler(message)

    async def whiteboard_reauthorize(self, _event: dict[str, Any]) -> None:
        """Group broadcast: something (e.g. the partnership ending) may have
        changed this connection's access. Re-check now instead of waiting for
        this client's next outbound message — closes the gap where a purely
        passive, now-revoked listener would otherwise keep receiving
        broadcasts indefinitely. A still-authorized connection is unaffected.
        """
        await self._enforce_still_authorized()

    async def _enforce_still_authorized(self) -> bool:
        """Close the connection if it is no longer authorized. Returns whether
        it is still authorized (i.e. whether the caller should proceed)."""
        if await self._still_authorized():
            return True
        logger.warning("ws.access_revoked public_id=%s", self._url_public_id())
        await self._send_error(rt.E_FORBIDDEN, "Access has been revoked.")
        await self.close(code=rt.CLOSE_GOING_AWAY)
        return False

    # ------------------------------------------------------- protocol: ops
    async def _on_operation_submit(self, message: dict[str, Any]) -> None:
        now = time.time()
        if self._rate_limited(
            rt.RATE_SUBMITS_WINDOW, rt.RATE_SUBMITS_LIMIT, self._submit_times, now
        ):
            logger.warning("ws.submit_rate_limited public_id=%s", self._url_public_id())
            await self._send_error(rt.E_RATE_LIMITED, _SUBMIT_MSG)
            return

        try:
            svc_op = _protocol_to_service_op(message)
        except InvalidOperationError as exc:
            await self._send_rejected(rt.E_INVALID_OPERATION, exc.message)
            return

        whiteboard, actor, client_id = (
            self.whiteboard,
            self.user,
            message.get("client_id"),
        )

        try:
            result = await database_sync_to_async(self._submit_sync)(whiteboard, actor, svc_op)
        except WhiteboardAPIError as exc:
            await self._reject_submit_error(exc)
            return
        except Exception:  # pragma: no cover - defensive
            logger.exception("ws.submit_error public_id=%s", self._url_public_id())
            await self._send_rejected(rt.E_SERVER_ERROR, "Internal server error.")
            return

        ack = result.acks[0]
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "whiteboard.op",
                "payload": rt.committed(
                    operation_id=svc_op["operation_id"],
                    sequence=ack.sequence,
                    version=ack.resulting_version,
                    operation_type=svc_op["operation_type"],
                    payload=svc_op.get("payload", {}),
                    actor=await _user_public(actor),
                    duplicate=ack.duplicate,
                    client_id=client_id,
                ),
            },
        )
        logger.info(
            "ws.operation_committed public_id=%s op=%s seq=%s dup=%s",
            self._url_public_id(),
            svc_op["operation_id"],
            ack.sequence,
            ack.duplicate,
        )

    def _submit_sync(self, whiteboard: Whiteboard, actor: User, op: dict[str, Any]) -> Any:
        """Run the authoritative service (sync write path) in a thread."""
        service = WhiteboardOperationService(validator=OperationValidator())
        return service.submit(whiteboard, actor, op)

    async def _reject_submit_error(self, exc: WhiteboardAPIError) -> None:
        """Map the service's domain error to the WebSocket rejection envelope."""
        code = exc.code
        if code == "WHITEBOARD_READ_ONLY":
            await self._send_rejected(rt.E_WHITEBOARD_ARCHIVED, exc.message)
            await self.close(code=rt.CLOSE_GOING_AWAY)
            return
        if code == "STALE_VERSION":
            logger.info("ws.stale public_id=%s", self._url_public_id())
            await self._send_rejected(
                rt.E_STALE_VERSION,
                exc.message,
                current_version=exc.data.get("current_version"),
                client_version=exc.data.get("client_version"),
            )
            return
        if code == "OPERATION_TOO_LARGE":
            await self._send_rejected(rt.E_PAYLOAD_TOO_LARGE, exc.message)
            return
        if code in ("INVALID_OPERATION", "INVALID_PAYLOAD"):
            await self._send_rejected(rt.E_INVALID_OPERATION, exc.message)
            return
        # Unknown domain code: never leak internals.
        logger.warning("ws.submit_unmapped code=%s", code)
        await self._send_rejected(rt.E_SERVER_ERROR, "Internal server error.")

    async def _on_sync_request(self, message: dict[str, Any]) -> None:
        client_version = message.get("version")
        if not isinstance(client_version, int) or client_version < 0:
            await self._send_error(
                rt.E_INVALID_MESSAGE, "sync.request requires a non-negative integer version."
            )
            return

        board_version = await database_sync_to_async(selectors.latest_version)(self.whiteboard)

        if client_version >= board_version:
            # Nothing to catch up on; just confirm the current version.
            await self.send(
                text_data=json.dumps(
                    {
                        "type": rt.S_SYNC_OPS,
                        "version": board_version,
                        "operations": [],
                    }
                )
            )
            return

        # Client is behind: give it the missing operations. The client applies
        # them deterministically in sequence order. If it is so far behind that
        # streaming would be costly/redundant, the client may instead do a full
        # HTTP reload; we keep incremental sync simple and bounded (no paging in
        # Phase 5 — reuse the HTTP operation list for very large gaps).
        ops = await database_sync_to_async(_fetch_ops_after)(self.whiteboard, client_version)
        await self.send(
            text_data=json.dumps(
                {
                    "type": rt.S_SYNC_OPS,
                    "version": board_version,
                    "operations": ops,
                }
            )
        )
        logger.info(
            "ws.sync_completed public_id=%s from=%s to=%s",
            self._url_public_id(),
            client_version,
            board_version,
        )

    async def _on_presence_cursor(self, message: dict[str, Any]) -> None:
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "whiteboard.presence",
                "payload": {
                    "type": rt.S_PRESENCE_UPDATE,
                    "user": await _user_public(self.user),
                    "cursor": {"x": message.get("x"), "y": message.get("y")},
                },
            },
        )

    async def _on_ping(self, message: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps({"type": rt.S_PONG}))

    # ----------------------------------------------------- group relay methods
    async def whiteboard_op(self, event: dict[str, Any]) -> None:
        await self.send(text_data=json.dumps(event["payload"]))

    async def whiteboard_presence(self, event: dict[str, Any]) -> None:
        # Drop presence echoes addressed to the originating client itself so a
        # client never sees its own cursor/join/leave (it already knows it is
        # here). Other group members DO receive it.
        payload = event["payload"]
        if payload.get("user", {}).get("public_id") == str(self.user.public_id):
            return
        await self.send(text_data=json.dumps(payload))

    # ------------------------------------------------------------- auth/z
    async def _authorize(self, user: User) -> tuple[bool, str, str]:
        public_id = self._url_public_id()
        partnership = await database_sync_to_async(selectors.whiteboard_by_partnership_public_id)(
            public_id
        )
        if partnership is None:
            return False, rt.E_WHITEBOARD_NOT_FOUND, "Whiteboard not found."
        if partnership.is_active is False:
            return False, rt.E_FORBIDDEN, "This partnership is not active."
        if not await database_sync_to_async(_can_access)(user, partnership):
            return False, rt.E_FORBIDDEN, "You do not have access to this whiteboard."

        whiteboard = await database_sync_to_async(selectors.whiteboard_for_partnership)(
            partnership
        )
        if await database_sync_to_async(lambda: whiteboard.is_archived)():
            return False, rt.E_WHITEBOARD_ARCHIVED, "This whiteboard is archived and read-only."

        self.partnership = partnership
        self.whiteboard = whiteboard
        return True, "", ""

    async def _still_authorized(self) -> bool:
        user = self.user
        partnership = self.partnership
        if user is None or partnership is None:
            return False
        if await database_sync_to_async(_can_access)(user, partnership) is False:
            return False
        if await database_sync_to_async(lambda: partnership.is_active)() is False:
            return False
        whiteboard = self.whiteboard
        return not await database_sync_to_async(lambda: whiteboard.is_archived)()

    # ---------------------------------------------------------------- utils
    def _url_public_id(self) -> str:
        return str(self.scope["url_route"]["kwargs"].get("public_id", ""))

    async def _send_error(self, code: str, message: str, **extra: Any) -> None:
        await self.send(text_data=json.dumps(rt.error(code, message, **extra)))

    async def _send_rejected(self, code: str, message: str, **extra: Any) -> None:
        await self.send(text_data=json.dumps(rt.rejected(code, message, **extra)))


# ---------------------------------------------------------------------------
# Pure helpers (testable without a live socket)
# ---------------------------------------------------------------------------


def _can_access(user: User, partnership: Any) -> bool:
    return can_view_whiteboard(user, partnership)


# ---------------------------------------------------------------------------
# Per-user connection accounting (in-process).  A single user cannot open
# unbounded sockets to the same board; this bounds broadcast fan-out.  Limits
# are per-worker-process; with multiple ASGI workers a distributed cap would be
# needed, which is out of scope for a single-node deployment.
# ---------------------------------------------------------------------------


def _release_connection_slot(user_pk: int) -> None:
    count = _user_connection_counts.get(user_pk, 0)
    if count <= 1:
        _user_connection_counts.pop(user_pk, None)
    else:
        _user_connection_counts[user_pk] = count - 1


def _reserve_connection_slot(user_pk: int) -> bool:
    current = _user_connection_counts.get(user_pk, 0)
    if current >= MAX_CONNECTIONS_PER_USER:
        return False
    _user_connection_counts[user_pk] = current + 1
    return True


def _protocol_to_service_op(message: dict[str, Any]) -> dict[str, Any]:
    """Map the ``operation.submit`` protocol frame onto the service operation.

    The service owns validation, so we only do the lightest structural checks
    here (presence of required keys with the right types) and let
    ``OperationValidator`` handle the rest authoritatively.
    """
    op_id = message.get("operation_id")
    base_version = message.get("base_version")
    operation_obj = message.get("operation")

    if not isinstance(op_id, str):
        raise InvalidOperationError("operation_id must be a string.")
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise InvalidOperationError("base_version must be an integer.")
    if not isinstance(operation_obj, dict):
        raise InvalidOperationError("operation must be an object.")

    op_type = operation_obj.get("operation_type")
    payload = operation_obj.get("payload", {})

    if not isinstance(op_type, str):
        raise InvalidOperationError("operation.operation_type must be a string.")
    if not isinstance(payload, dict):
        raise InvalidOperationError("operation.payload must be an object.")

    return {
        "operation_id": op_id,
        "operation_type": op_type,
        "base_version": base_version,
        "payload": payload,
    }


def _fetch_ops_after(whiteboard: Any, after_sequence: int) -> list[dict[str, Any]]:
    """Fetch + serialize operations after ``after_sequence`` (sync, for a worker thread)."""
    qs = selectors.operations_for_whiteboard(whiteboard, after_sequence=after_sequence)
    return [_op_public(o) for o in qs]


def _op_public(op: Any) -> dict[str, Any]:
    """Serialize a WhiteboardOperation row for ``sync.ops``."""
    return {
        "operation_id": str(op.operation_id),
        "sequence": op.sequence,
        "operation_type": op.operation_type,
        "base_version": op.base_version,
        "resulting_version": op.resulting_version,
        "payload": op.payload,
    }
