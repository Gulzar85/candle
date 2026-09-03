"""Backend tests for the whiteboard WebSocket protocol.

These drive the real consumer through Django Channels' `WebsocketCommunicator`
wrapped in the same `AuthMiddlewareStack(URLRouter(...))` stack production uses.
Authentication is faked with a genuine logged-in Django test session cookie so
the consumer's session auth path is exercised for real.

Tests are *async* and run under pytest-asyncio's single event loop (which is what
the consumer's ``database_sync_to_async`` worker threads need to interoperate
with correctly). The tiny amount of synchronous ORM fixture setup is wrapped in
``sync_to_async`` so it is never called from an async context, and
``transaction=True`` lets the consumer's thread-local writes commit + roll back.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client

from apps.whiteboard import realtime as rt
from apps.whiteboard import routing
from tests.whiteboard.base import make_active_partnership, make_user

APPLICATION = AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns))
ORIGIN = b"http://localhost:8000"

OP_1 = str(uuid4())
OP_2 = str(uuid4())


def _scope_headers_sync(user: Any) -> list[tuple[bytes, bytes]]:
    """Build logged-in WS scope headers; must run on a worker thread (sync ORM)."""
    client = Client()
    client.force_login(user)
    cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    return [
        (b"origin", ORIGIN),
        (b"host", b"localhost"),
        (b"cookie", f"{settings.SESSION_COOKIE_NAME}={cookie.value}".encode()),
    ]


async def _authenticated_headers(user: Any) -> list[tuple[bytes, bytes]]:
    return await sync_to_async(_scope_headers_sync)(user)


# All fixture data is created through these sync wrapper functions so the heavy
# ORM writes run on a worker thread (never from the async test body).


def _make_world_sync() -> SimpleNamespace:
    alice = make_user(f"alice{uuid4().hex[:8]}@test.com")
    bob = make_user(f"bob{uuid4().hex[:8]}@test.com")
    partnership = make_active_partnership(alice, bob)
    return SimpleNamespace(alice=alice, bob=bob, partnership=partnership)


async def _make_world() -> SimpleNamespace:
    return await sync_to_async(_make_world_sync)()


async def _make_world_with_intruder() -> SimpleNamespace:
    world = await _make_world()
    intruder = await sync_to_async(make_user)(f"eve{uuid4().hex[:8]}@test.com")
    world.intruder = intruder
    return world


async def _connect(user: Any, public_id: Any) -> WebsocketCommunicator:
    comm = WebsocketCommunicator(
        APPLICATION,
        f"/ws/whiteboards/{public_id}/",
        headers=await _authenticated_headers(user),
    )
    connected, _ = await comm.connect()
    assert connected is True
    return comm


async def _connect_headers(public_id: Any, headers: list[tuple[bytes, bytes]]) -> WebsocketCommunicator:
    comm = WebsocketCommunicator(APPLICATION, f"/ws/whiteboards/{public_id}/", headers=headers)
    connected, _ = await comm.connect()
    assert connected is True
    return comm


async def _recv_op(comm: WebsocketCommunicator) -> dict[str, Any]:
    """Read the next operation/error/sync message, skipping presence frames.

    Presence events are orthogonal to the assertions we make throughout; they are
    interleaved whenever a peer connects/disconnects, so we skip them everywhere
    except the dedicated presence tests.
    """
    while True:
        msg = await comm.receive_json_from()
        if msg["type"] in (rt.S_PRESENCE_JOINED, rt.S_PRESENCE_LEFT, rt.S_PRESENCE_UPDATE):
            continue
        return msg


async def _ready(comm: WebsocketCommunicator) -> dict[str, Any]:
    return await _recv_op(comm)


async def _send(comm: WebsocketCommunicator, payload: dict[str, Any]) -> None:
    await comm.send_json_to(payload)


def _submit(
    operation_id: str = OP_1,
    base_version: int = 0,
    op_type: str = "create_stroke",
    object_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "points": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
        "color": "#2563eb",
        "width": 4,
        "opacity": 1.0,
    }
    if op_type == "delete_object":
        payload = {"object_id": object_id or str(uuid4())}
    elif op_type == "clear_canvas":
        payload = {}
    else:
        payload["object_id"] = object_id or str(uuid4())
    return {
        "type": rt.C_OPERATION_SUBMIT,
        "operation_id": operation_id,
        "base_version": base_version,
        "operation": {"operation_type": op_type, "payload": payload},
    }


# --------------------------------------------------------------------------- connect


@pytest.mark.django_db(transaction=True)
async def test_connect_ready_carries_identity_and_version() -> None:
    w = await _make_world()
    comm = await _connect(w.alice, w.partnership.public_id)
    ready = await _ready(comm)
    assert ready["protocol_version"] == rt.PROTOCOL_VERSION
    assert ready["user"]["public_id"] == str(w.alice.public_id)
    assert ready["partner"]["public_id"] == str(w.bob.public_id)
    assert ready["version"] == 0
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_connect_rejects_anonymous() -> None:
    w = await _make_world()
    comm = await _connect_headers(w.partnership.public_id, [(b"origin", ORIGIN), (b"host", b"localhost")])
    err = await _ready(comm)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_AUTH_REQUIRED
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_connect_rejects_non_member() -> None:
    w = await _make_world_with_intruder()
    comm = await _connect(w.intruder, w.partnership.public_id)
    err = await _ready(comm)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_FORBIDDEN
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_connect_rejects_unknown_whiteboard() -> None:
    w = await _make_world()
    comm = await _connect(w.alice, uuid4())
    err = await _ready(comm)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_WHITEBOARD_NOT_FOUND
    await comm.disconnect()


# --------------------------------------------------------------------------- operations


@pytest.mark.django_db(transaction=True)
async def test_submit_broadcasts_committed_to_all() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)
    b = await _connect(w.bob, w.partnership.public_id)
    await _ready(b)

    await _send(a, _submit())

    for comm in (a, b):
        msg = await _ready(comm)
        assert msg["type"] == rt.S_OPERATION_COMMITTED
        assert msg["operation_id"] == OP_1
        assert msg["sequence"] == 1
        assert msg["version"] == 1
        assert msg["duplicate"] is False
        assert msg["operation"]["operation_type"] == "create_stroke"
        assert msg["operation"]["payload"]["points"][0]["x"] == 1
        assert msg["actor"]["public_id"] == str(w.alice.public_id)

    await a.disconnect()
    await b.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_submit_duplicate_id_is_idempotent_no_version_bump() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)

    await _send(a, _submit())
    first = await _ready(a)
    assert first["type"] == rt.S_OPERATION_COMMITTED
    assert first["version"] == 1

    await _send(a, _submit())  # same operation_id
    second = await _ready(a)
    assert second["type"] == rt.S_OPERATION_COMMITTED
    assert second["duplicate"] is True
    assert second["version"] == 1  # unchanged

    await a.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_submit_stale_version_rejected() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)

    await _send(a, _submit(OP_1))
    await _ready(a)  # committed, version now 1

    await _send(a, _submit(OP_2, base_version=0))  # outdated base
    rejected = await _ready(a)
    assert rejected["type"] == rt.S_OPERATION_REJECTED
    assert rejected["reason"] == rt.E_STALE_VERSION
    assert rejected["current_version"] == 1
    assert rejected["client_version"] == 0

    await a.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_submit_malformed_operation_rejected() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)

    await _send(a, {"type": rt.C_OPERATION_SUBMIT, "operation_id": "x"})
    rejected = await _ready(a)
    assert rejected["type"] == rt.S_OPERATION_REJECTED
    assert rejected["reason"] == rt.E_INVALID_OPERATION

    await a.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_revoked_member_cannot_keep_writing() -> None:
    w = await _make_world()
    a = await _connect(w.bob, w.partnership.public_id)
    await _ready(a)

    # Revoke Bob's membership after the socket is already open.
    await sync_to_async(w.bob.memberships.filter(partnership=w.partnership).update)(
        status="REMOVED"
    )

    await _send(a, _submit())
    err = await _ready(a)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_FORBIDDEN
    await a.disconnect()


# --------------------------------------------------------------------------- sync / presence / protocol


@pytest.mark.django_db(transaction=True)
async def test_sync_delivers_missed_operations() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)
    await _send(a, _submit(OP_1))
    await _send(a, _submit(OP_2, base_version=1))
    await _ready(a)
    await _ready(a)
    await a.disconnect()

    b = await _connect(w.bob, w.partnership.public_id)
    ready = await _ready(b)
    assert ready["version"] == 2
    await _send(b, {"type": rt.C_SYNC_REQUEST, "version": 0})
    sync = await _ready(b)
    assert sync["type"] == rt.S_SYNC_OPS
    assert sync["version"] == 2
    assert [o["sequence"] for o in sync["operations"]] == [1, 2]
    assert [o["operation_type"] for o in sync["operations"]] == ["create_stroke", "create_stroke"]
    await b.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_presence_join_and_leave_broadcast() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)

    b = await _connect(w.bob, w.partnership.public_id)
    joined = await a.receive_json_from()  # Alice hears Bob join
    assert joined["type"] == rt.S_PRESENCE_JOINED
    assert joined["user"]["public_id"] == str(w.bob.public_id)
    await _ready(b)

    await b.disconnect()
    left = await a.receive_json_from()
    assert left["type"] == rt.S_PRESENCE_LEFT
    assert left["user"]["public_id"] == str(w.bob.public_id)

    await a.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_unknown_message_type_rejected() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)
    await _send(a, {"type": "no.such.type"})
    err = await _ready(a)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_INVALID_MESSAGE
    await a.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_oversized_message_rejected() -> None:
    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)
    huge = "x" * (rt.MAX_MESSAGE_BYTES + 1)
    await a.send_to(text_data=huge)
    err = await _ready(a)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_PAYLOAD_TOO_LARGE
    await a.disconnect()
