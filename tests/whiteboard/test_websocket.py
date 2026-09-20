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

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse

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


async def _connect_headers(
    public_id: Any, headers: list[tuple[bytes, bytes]]
) -> WebsocketCommunicator:
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
    comm = await _connect_headers(
        w.partnership.public_id, [(b"origin", ORIGIN), (b"host", b"localhost")]
    )
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


@pytest.mark.django_db(transaction=True)
async def test_connect_wildcard_origin_allows_any_origin() -> None:
    w = await _make_world()
    headers = await _authenticated_headers(w.alice)
    comm = WebsocketCommunicator(
        APPLICATION,
        f"/ws/whiteboards/{w.partnership.public_id}/",
        headers=[
            (b"origin", b"http://192.168.10.5:8000"),
            *(h for h in headers if h[0] != b"origin"),
        ],
    )
    with override_settings(WEBSOCKET_ALLOWED_ORIGINS=["*"]):
        connected, _ = await comm.connect()
    assert connected is True
    await comm.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_connect_strict_origin_rejects_unlisted() -> None:
    w = await _make_world()
    headers = await _authenticated_headers(w.alice)
    comm = WebsocketCommunicator(
        APPLICATION,
        f"/ws/whiteboards/{w.partnership.public_id}/",
        headers=[(b"origin", ORIGIN), *(h for h in headers if h[0] != b"origin")],
    )
    with override_settings(WEBSOCKET_ALLOWED_ORIGINS=["https://example.com"]):
        connected, _ = await comm.connect()
    assert connected is False


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
async def test_http_submit_broadcasts_committed_to_live_peer() -> None:
    """An operation accepted over HTTP reaches a partner on a live WebSocket.

    Regression test for the real-time gap where the *author's* WebSocket was
    not live: its writes silently fell back to HTTP (``pushHttp``) and HTTP
    submits never broadcast, so a connected partner didn't see them until a
    resync. A connected peer must receive the committed operation regardless of
    the transport the author used.
    """
    w = await _make_world()
    alice = await _connect(w.alice, w.partnership.public_id)
    await _ready(alice)
    bob = await _connect(w.bob, w.partnership.public_id)
    await _ready(bob)

    op_id = str(uuid4())
    resp = await sync_to_async(_http_post_op)(w, op_id)
    assert resp.status_code == 200

    # The live partner sees the commit over the broadcast, not a resync.
    msg = await _ready(alice)
    assert msg["type"] == rt.S_OPERATION_COMMITTED
    assert msg["operation_id"] == op_id
    assert msg["version"] == 1
    assert msg["duplicate"] is False
    assert msg["actor"]["public_id"] == str(w.bob.public_id)
    assert msg["operation"]["operation_type"] == "create_stroke"

    # The HTTP author's own (still-open) socket also receives the broadcast —
    # its client deduplicates by operation_id, so this must never re-apply.
    self_msg = await _ready(bob)
    assert self_msg["type"] == rt.S_OPERATION_COMMITTED
    assert self_msg["operation_id"] == op_id

    await alice.disconnect()
    await bob.disconnect()


def _http_post_op(w: Any, op_id: str) -> Any:
    """Authenticated HTTP operation submit (sync; runs on a worker thread)."""
    client = Client()
    client.force_login(w.bob)
    return client.post(
        reverse("whiteboard:api_operation_submit", kwargs={"public_id": w.partnership.public_id}),
        data=json.dumps(
            {
                "operation_id": op_id,
                "base_version": 0,
                "operation_type": "create_stroke",
                "payload": {
                    "object_id": str(uuid4()),
                    "points": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
                    "color": "#2563eb",
                    "width": 4,
                    "opacity": 1.0,
                },
            }
        ),
        content_type="application/json",
    )


@pytest.mark.django_db(transaction=True)
async def test_http_submit_duplicate_still_broadcasts_duplicate_flag() -> None:
    w = await _make_world()
    alice = await _connect(w.alice, w.partnership.public_id)
    await _ready(alice)

    op_id = str(uuid4())
    assert (await sync_to_async(_http_post_op)(w, op_id)).status_code == 200
    first = await _ready(alice)
    assert first["type"] == rt.S_OPERATION_COMMITTED
    assert first["duplicate"] is False

    # Retrying the same operation id over HTTP broadcasts a duplicate ack.
    assert (await sync_to_async(_http_post_op)(w, op_id)).status_code == 200
    second = await _ready(alice)
    assert second["operation_id"] == op_id
    assert second["duplicate"] is True
    assert second["version"] == 1  # unchanged

    await alice.disconnect()


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


@pytest.mark.django_db(transaction=True)
async def test_ended_partnership_disconnects_passive_listener() -> None:
    """A purely passive connection (never sends a message) must not keep
    receiving broadcasts once the partnership that authorized it has ended.

    Unlike test_revoked_member_cannot_keep_writing, Bob here never sends
    anything — the server must push the re-check itself (see
    apps.whiteboard.realtime_signals.request_reauthorization, invoked from
    end_partnership via transaction.on_commit), not merely wait for Bob's
    next outbound message.
    """
    from apps.partnerships.services import end_partnership

    w = await _make_world()
    a = await _connect(w.alice, w.partnership.public_id)
    await _ready(a)
    b = await _connect(w.bob, w.partnership.public_id)
    await _ready(b)

    await sync_to_async(end_partnership)(w.partnership, w.alice)

    # Bob never sends anything, yet must be pushed a FORBIDDEN + closed.
    err = await _ready(b)
    assert err["type"] == rt.S_CONNECTION_ERROR
    assert err["code"] == rt.E_FORBIDDEN
    await b.disconnect()
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
