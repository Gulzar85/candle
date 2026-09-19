"""Whiteboard operation API tests.

Tests the POST /api/whiteboards/<public_id>/operations/ endpoint covering:
- successful submission (single and batch)
- idempotency (duplicate operation_id)
- invalid payloads
- authorization (non-member, inactive partnership)
- rate limiting
- archived board (read-only)
- stale version detection
"""

from __future__ import annotations

import json
import uuid

from django.test import TestCase
from django.urls import reverse

from apps.partnerships.enums import PartnershipMemberStatus, PartnershipStatus
from apps.partnerships.models import Partnership, PartnershipMember
from apps.whiteboard import selectors
from apps.whiteboard.enums import WhiteboardOperationType, WhiteboardStatus
from apps.whiteboard.models import WhiteboardOperation

from .base import WhiteboardTestCase, make_active_partnership, make_user


def _op_url(partnership: Partnership) -> str:
    return reverse("whiteboard:api_operation_submit", kwargs={"public_id": partnership.public_id})


def _state_url(partnership: Partnership) -> str:
    return reverse("whiteboard:api_whiteboard_state", kwargs={"public_id": partnership.public_id})


def _ops_list_url(partnership: Partnership) -> str:
    return reverse("whiteboard:api_operation_list", kwargs={"public_id": partnership.public_id})


def _make_stroke_op(
    *,
    object_id: str | None = None,
    base_version: int = 0,
    color: str = "#2563eb",
    width: int = 3,
    opacity: float = 1.0,
    points: list[dict] | None = None,
    creator_id: str | None = None,
) -> dict:
    payload: dict = {
        "object_id": object_id or str(uuid.uuid4()),
        "points": points or [{"x": 10, "y": 20}, {"x": 11, "y": 21}],
        "color": color,
        "width": width,
        "opacity": opacity,
    }
    if creator_id is not None:
        payload["creator_id"] = creator_id
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.CREATE_STROKE,
        "base_version": base_version,
        "payload": payload,
    }


def _make_delete_op(*, object_id: str, base_version: int = 0) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.DELETE_OBJECT,
        "base_version": base_version,
        "payload": {"object_id": object_id},
    }


def _make_clear_op(*, base_version: int = 0) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.CLEAR_CANVAS,
        "base_version": base_version,
        "payload": {},
    }


def _make_move_op(*, object_id: str, dx: float = 1, dy: float = 1, base_version: int = 0) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.MOVE_OBJECT,
        "base_version": base_version,
        "payload": {"object_id": object_id, "dx": dx, "dy": dy},
    }


def _make_resize_op(
    *,
    object_id: str,
    anchor: dict | None = None,
    scale_x: float = 2,
    scale_y: float = 2,
    base_version: int = 0,
) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.RESIZE_OBJECT,
        "base_version": base_version,
        "payload": {
            "object_id": object_id,
            "anchor": anchor or {"x": 0, "y": 0},
            "scale_x": scale_x,
            "scale_y": scale_y,
        },
    }


class OperationSubmitTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.mallory = make_user("mallory@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_submit_single_stroke(self) -> None:
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["acks"]), 1)
        self.assertEqual(data["acks"][0]["version"], 1)
        self.assertFalse(data["acks"][0]["duplicate"])
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["applied"], 1)

    def test_submit_batch(self) -> None:
        op1 = _make_stroke_op(base_version=0)
        op2 = _make_stroke_op(base_version=1)
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": [op1, op2]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["acks"]), 2)
        self.assertEqual(data["acks"][0]["version"], 1)
        self.assertEqual(data["acks"][1]["version"], 2)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["applied"], 2)

    def test_submit_array_format(self) -> None:
        op1 = _make_stroke_op(base_version=0)
        op2 = _make_delete_op(object_id="some-id", base_version=1)
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps([op1, op2]),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["applied"], 2)

    def test_empty_body_rejected(self) -> None:
        resp = self.client.post(
            _op_url(self.partnership),
            data="",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_rejected(self) -> None:
        resp = self.client.post(
            _op_url(self.partnership),
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_operations_rejected(self) -> None:
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": []}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_batch_exceeds_limit_rejected(self) -> None:
        ops = [_make_stroke_op(base_version=i) for i in range(51)]
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": ops}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class IdempotencyTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_duplicate_operation_returns_existing_ack(self) -> None:
        op = _make_stroke_op()
        # First submission.
        resp1 = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertFalse(data1["acks"][0]["duplicate"])

        # Second submission (same operation_id).
        resp2 = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertTrue(data2["acks"][0]["duplicate"])
        self.assertEqual(data2["acks"][0]["version"], data1["acks"][0]["version"])
        self.assertEqual(data2["applied"], 0)

    def test_only_one_row_in_database(self) -> None:
        op = _make_stroke_op()
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        wb = self.partnership.whiteboard
        count = WhiteboardOperation.objects.filter(
            whiteboard=wb, operation_id=op["operation_id"]
        ).count()
        self.assertEqual(count, 1)


class StaleVersionTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_stale_version_rejected(self) -> None:
        # Apply first operation.
        op1 = _make_stroke_op(base_version=0)
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op1),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        # Submit with stale base_version.
        op2 = _make_stroke_op(base_version=0)  # Should be 1
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op2),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["error"], "STALE_VERSION")
        self.assertEqual(data["current_version"], 1)
        self.assertEqual(data["client_version"], 0)


class AuthorizationTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.mallory = make_user("mallory@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)

    def test_anonymous_rejected(self) -> None:
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_member_rejected(self) -> None:
        self.client.force_login(self.mallory)
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_member_who_left_rejected(self) -> None:
        membership = PartnershipMember.objects.get(partnership=self.partnership, user=self.bobby)
        membership.status = PartnershipMemberStatus.LEFT
        membership.save(update_fields=["status"])
        self.client.force_login(self.bobby)
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_inactive_partnership_rejected(self) -> None:
        self.partnership.status = PartnershipStatus.ENDED
        self.partnership.save(update_fields=["status"])
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_bobby_can_also_submit(self) -> None:
        self.client.force_login(self.bobby)
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)


class ArchivedBoardTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)
        # Create whiteboard (lazily via selector) and archive it.
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        self.whiteboard.status = WhiteboardStatus.ARCHIVED
        self.whiteboard.save(update_fields=["status"])

    def test_submit_to_archived_board_rejected(self) -> None:
        op = _make_stroke_op()
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertEqual(data["error"], "WHITEBOARD_READ_ONLY")


class InvalidPayloadTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_unknown_operation_type_rejected(self) -> None:
        op = {
            "operation_id": str(uuid.uuid4()),
            "operation_type": "draw_circle",
            "base_version": 0,
            "payload": {},
        }
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_operation_id_rejected(self) -> None:
        op = {
            "operation_type": WhiteboardOperationType.CREATE_STROKE,
            "base_version": 0,
            "payload": {},
        }
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_base_version_rejected(self) -> None:
        op = {
            "operation_id": str(uuid.uuid4()),
            "operation_type": WhiteboardOperationType.CREATE_STROKE,
            "payload": {},
        }
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_forbidden_field_rejected(self) -> None:
        op = {
            "operation_id": str(uuid.uuid4()),
            "operation_type": WhiteboardOperationType.CREATE_STROKE,
            "base_version": 0,
            "actor": "someone",
            "payload": {},
        }
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_color_rejected(self) -> None:
        op = _make_stroke_op(color="not-a-color")
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_width_rejected(self) -> None:
        op = _make_stroke_op(width=0)
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_too_many_points_rejected(self) -> None:
        points = [{"x": i, "y": i} for i in range(4001)]
        op = _make_stroke_op(points=points)
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 413)


class MoveObjectTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def _create_stroke(self, object_id: str) -> None:
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(_make_stroke_op(object_id=object_id, base_version=0)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_move_applied_and_reflected_in_state(self) -> None:
        self._create_stroke("s1")
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(_make_move_op(object_id="s1", dx=5, dy=-3, base_version=1)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        state = self.client.get(_state_url(self.partnership)).json()
        obj = next(o for o in state["objects"] if o["object_id"] == "s1")
        # _make_stroke_op's default first point is (10, 20).
        self.assertEqual(obj["points"][0], {"x": 15, "y": 17})

    def test_move_missing_object_id_rejected(self) -> None:
        op = _make_move_op(object_id="s1", base_version=0)
        del op["payload"]["object_id"]
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_move_non_numeric_delta_rejected(self) -> None:
        op = _make_move_op(object_id="s1", base_version=0)
        op["payload"]["dx"] = "not-a-number"
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_move_non_finite_delta_rejected(self) -> None:
        op = _make_move_op(object_id="s1", base_version=0)
        op["payload"]["dx"] = float("inf")
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_move_nonexistent_object_still_commits_as_noop(self) -> None:
        """The op is structurally valid and commits (advancing version) even
        though the target object doesn't exist server-side — matching
        delete_object's existing no-op-on-absent convention."""
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(_make_move_op(object_id="nonexistent", base_version=0)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)


class ResizeObjectTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def _create_stroke(self, object_id: str) -> None:
        op = _make_stroke_op(
            object_id=object_id,
            base_version=0,
            points=[{"x": 0, "y": 0}, {"x": 10, "y": 10}],
        )
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)

    def test_resize_applied_and_reflected_in_state(self) -> None:
        self._create_stroke("s1")
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(
                _make_resize_op(
                    object_id="s1", anchor={"x": 0, "y": 0}, scale_x=2, scale_y=2, base_version=1
                )
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        state = self.client.get(_state_url(self.partnership)).json()
        obj = next(o for o in state["objects"] if o["object_id"] == "s1")
        self.assertEqual(obj["points"][1], {"x": 20, "y": 20})
        # Resize never scales stroke width.
        self.assertEqual(obj["width"], 3)

    def test_resize_missing_anchor_rejected(self) -> None:
        op = _make_resize_op(object_id="s1", base_version=0)
        del op["payload"]["anchor"]
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_resize_zero_scale_rejected(self) -> None:
        op = _make_resize_op(object_id="s1", scale_x=0, base_version=0)
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_resize_absurd_scale_rejected(self) -> None:
        op = _make_resize_op(object_id="s1", scale_x=1_000_000, base_version=0)
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_resize_non_finite_scale_rejected(self) -> None:
        op = _make_resize_op(object_id="s1", base_version=0)
        op["payload"]["scale_y"] = float("nan")
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_resize_boundary_scale_accepted(self) -> None:
        self._create_stroke("s1")
        op = _make_resize_op(object_id="s1", scale_x=0.001, scale_y=1000, base_version=1)
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)


class RestoreVersionValidatorTests(TestCase):
    """Structural validation only — RestoreService (which constructs a real
    restore_version payload server-side) is covered by test_restore.py."""

    def setUp(self) -> None:
        from apps.whiteboard.validator import OperationValidator

        self.validator = OperationValidator()

    def _op(self, payload: dict) -> dict:
        return {
            "operation_id": str(uuid.uuid4()),
            "operation_type": WhiteboardOperationType.RESTORE_VERSION,
            "base_version": 0,
            "payload": payload,
        }

    def test_valid_restore_payload_accepted(self) -> None:
        self.validator.validate(
            self._op(
                {
                    "target_sequence": 3,
                    "objects": [
                        {
                            "object_id": "s1",
                            "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
                            "color": "#000000",
                            "width": 3,
                            "opacity": 1,
                        }
                    ],
                }
            )
        )  # should not raise

    def test_negative_target_sequence_rejected(self) -> None:
        from apps.whiteboard.errors import WhiteboardAPIError

        with self.assertRaises(WhiteboardAPIError):
            self.validator.validate(self._op({"target_sequence": -1, "objects": []}))

    def test_missing_objects_rejected(self) -> None:
        from apps.whiteboard.errors import WhiteboardAPIError

        with self.assertRaises(WhiteboardAPIError):
            self.validator.validate(self._op({"target_sequence": 0}))

    def test_malformed_object_in_snapshot_rejected(self) -> None:
        from apps.whiteboard.errors import WhiteboardAPIError

        with self.assertRaises(WhiteboardAPIError):
            self.validator.validate(
                self._op({"target_sequence": 0, "objects": [{"object_id": "s1"}]})
            )

    def test_oversized_snapshot_rejected(self) -> None:
        from apps.whiteboard import limits
        from apps.whiteboard.errors import WhiteboardAPIError

        objects = [
            {
                "object_id": f"s{i}",
                "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
                "color": "#000000",
                "width": 3,
                "opacity": 1,
            }
            for i in range(limits.MAX_RESTORE_OBJECTS + 1)
        ]
        with self.assertRaises(WhiteboardAPIError):
            self.validator.validate(self._op({"target_sequence": 0, "objects": objects}))

    def test_restore_gets_higher_byte_cap_than_ordinary_ops(self) -> None:
        """A restore snapshot bigger than MAX_OPERATION_PAYLOAD_BYTES but
        under MAX_RESTORE_PAYLOAD_BYTES must be accepted."""
        from apps.whiteboard import limits

        # One object is small; build enough of them to exceed the ordinary
        # 100KB op cap but stay under the 150KB restore cap.
        objects = [
            {
                "object_id": f"s{i}",
                "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}],
                "color": "#000000",
                "width": 3,
                "opacity": 1,
            }
            for i in range(900)
        ]
        payload = {"target_sequence": 0, "objects": objects}
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        self.assertGreater(payload_bytes, limits.MAX_OPERATION_PAYLOAD_BYTES)
        self.assertLess(payload_bytes, limits.MAX_RESTORE_PAYLOAD_BYTES)
        self.validator.validate(self._op(payload))  # should not raise


class LoadStateTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_load_empty_state(self) -> None:
        resp = self.client.get(_state_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["version"], 0)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["objects"], [])

    def test_load_state_after_operations(self) -> None:
        # Submit two strokes.
        op1 = _make_stroke_op(object_id="stroke-1", base_version=0)
        op2 = _make_stroke_op(object_id="stroke-2", base_version=1)
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": [op1, op2]}),
            content_type="application/json",
        )
        resp = self.client.get(_state_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["count"], 2)

    def test_load_state_after_delete(self) -> None:
        op1 = _make_stroke_op(object_id="stroke-1", base_version=0)
        op2 = _make_delete_op(object_id="stroke-1", base_version=1)
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": [op1, op2]}),
            content_type="application/json",
        )
        resp = self.client.get(_state_url(self.partnership))
        data = resp.json()
        self.assertEqual(data["count"], 0)

    def test_load_state_after_clear(self) -> None:
        op1 = _make_stroke_op(object_id="stroke-1", base_version=0)
        op2 = _make_clear_op(base_version=1)
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": [op1, op2]}),
            content_type="application/json",
        )
        resp = self.client.get(_state_url(self.partnership))
        data = resp.json()
        self.assertEqual(data["count"], 0)

    def test_load_requires_auth(self) -> None:
        self.client.logout()
        resp = self.client.get(_state_url(self.partnership))
        # DRF's IsAuthenticated returns 403 (not a 302 redirect) for anonymous.
        self.assertEqual(resp.status_code, 403)

    def test_load_requires_membership(self) -> None:
        self.client.force_login(make_user("outsider@example.com"))
        resp = self.client.get(_state_url(self.partnership))
        self.assertEqual(resp.status_code, 403)


class LoadOperationsTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def _submit_ops(self, count: int) -> None:
        ops = [_make_stroke_op(base_version=i) for i in range(count)]
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps({"operations": ops}),
            content_type="application/json",
        )

    def test_load_operations_empty(self) -> None:
        resp = self.client.get(_ops_list_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["operations"], [])

    def test_load_operations(self) -> None:
        self._submit_ops(3)
        resp = self.client.get(_ops_list_url(self.partnership))
        data = resp.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["version"], 3)
        # Sequences should be in order.
        seqs = [op["sequence"] for op in data["operations"]]
        self.assertEqual(seqs, [1, 2, 3])

    def test_load_operations_after_sequence(self) -> None:
        self._submit_ops(5)
        resp = self.client.get(f"{_ops_list_url(self.partnership)}?after_sequence=3")
        data = resp.json()
        self.assertEqual(data["count"], 2)
        seqs = [op["sequence"] for op in data["operations"]]
        self.assertEqual(seqs, [4, 5])

    def test_load_operations_limit(self) -> None:
        self._submit_ops(10)
        resp = self.client.get(f"{_ops_list_url(self.partnership)}?limit=3")
        data = resp.json()
        self.assertEqual(data["count"], 3)

    def test_load_operations_negative_after_rejected(self) -> None:
        resp = self.client.get(f"{_ops_list_url(self.partnership)}?after_sequence=-1")
        self.assertEqual(resp.status_code, 400)


class PayloadSizeTests(WhiteboardTestCase):
    """Verify oversized payloads are rejected against the default limits."""

    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def _stroke_with_creator(self, creator_len: int) -> dict:
        op = _make_stroke_op(
            points=[{"x": 123.456, "y": 654.321} for _ in range(3000)],
            creator_id="x" * creator_len,
        )
        return op

    def test_oversized_payload_rejected(self) -> None:
        # ~3000 points * ~24 bytes + a large creator_id pushes the serialized
        # payload past the default 100KB per-op limit without exceeding the
        # 4000-point stroke limit.
        op = self._stroke_with_creator(200_000)
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(op),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 413)
