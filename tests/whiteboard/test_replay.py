"""State reconstruction (deterministic replay) tests.

Verifies that WhiteboardStateBuilder.replay() produces correct, deterministic
results given the same ordered operations. Tests cover:

- Creating strokes
- Deleting objects
- Clearing the canvas
- Multiple objects
- Ordering / z-order
- Re-creating an existing object (upsert)
- Empty operation list
- Mixed operation types
"""

from __future__ import annotations

from django.test import TestCase

from apps.whiteboard.enums import WhiteboardOperationType
from apps.whiteboard.state_reconstruction import (
    ReconstructedState,
    WhiteboardStateBuilder,
    reconstruct_state,
)


def _op_dict(op_type: str, payload: dict) -> dict:
    return {"operation_type": op_type, "payload": payload}


class ReplayCreateStrokeTests(TestCase):
    def test_single_stroke(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}, {"x": 10, "y": 10}],
                    "color": "#000000",
                    "width": 3,
                    "opacity": 1.0,
                },
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 1)
        self.assertIn("s1", state.objects)
        self.assertEqual(state.order, ["s1"])
        self.assertEqual(state.objects["s1"]["object_type"], "stroke")

    def test_multiple_strokes(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s2",
                    "points": [{"x": 5, "y": 5}],
                    "color": "#f00",
                    "width": 2,
                    "opacity": 0.5,
                },
            ),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s3",
                    "points": [{"x": 10, "y": 10}],
                    "color": "#0f0",
                    "width": 4,
                    "opacity": 0.8,
                },
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 3)
        self.assertEqual(state.order, ["s1", "s2", "s3"])

    def test_upsert_moves_to_top(self) -> None:
        """Re-creating an existing object_id moves it to the end of z-order."""
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s2",
                    "points": [{"x": 5, "y": 5}],
                    "color": "#f00",
                    "width": 2,
                    "opacity": 1,
                },
            ),
            # Re-create s1: should move to top.
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 20, "y": 20}],
                    "color": "#00f",
                    "width": 5,
                    "opacity": 1,
                },
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 2)
        self.assertEqual(state.order, ["s2", "s1"])
        # s1 should have updated points.
        self.assertEqual(state.objects["s1"]["points"], [{"x": 20, "y": 20}])


class ReplayDeleteTests(TestCase):
    def test_delete_removes_object(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.DELETE_OBJECT,
                {"object_id": "s1"},
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 0)
        self.assertEqual(state.order, [])

    def test_delete_nonexistent_is_noop(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.DELETE_OBJECT,
                {"object_id": "nonexistent"},
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 0)
        self.assertEqual(state.order, [])

    def test_delete_one_of_many(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s2",
                    "points": [{"x": 5, "y": 5}],
                    "color": "#f00",
                    "width": 2,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.DELETE_OBJECT,
                {"object_id": "s1"},
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 1)
        self.assertIn("s2", state.objects)
        self.assertEqual(state.order, ["s2"])


class ReplayClearTests(TestCase):
    def test_clear_removes_all(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s2",
                    "points": [{"x": 5, "y": 5}],
                    "color": "#f00",
                    "width": 2,
                    "opacity": 1,
                },
            ),
            _op_dict(WhiteboardOperationType.CLEAR_CANVAS, {}),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 0)
        self.assertEqual(state.order, [])

    def test_clear_then_add(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(WhiteboardOperationType.CLEAR_CANVAS, {}),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s2",
                    "points": [{"x": 5, "y": 5}],
                    "color": "#f00",
                    "width": 2,
                    "opacity": 1,
                },
            ),
        ]
        state = reconstruct_state(ops)
        self.assertEqual(len(state.objects), 1)
        self.assertIn("s2", state.objects)


class ReplayDeterminismTests(TestCase):
    def test_same_ops_produce_same_state(self) -> None:
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s2",
                    "points": [{"x": 5, "y": 5}],
                    "color": "#f00",
                    "width": 2,
                    "opacity": 1,
                },
            ),
            _op_dict(
                WhiteboardOperationType.DELETE_OBJECT,
                {"object_id": "s1"},
            ),
            _op_dict(WhiteboardOperationType.CLEAR_CANVAS, {}),
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s3",
                    "points": [{"x": 10, "y": 10}],
                    "color": "#0f0",
                    "width": 4,
                    "opacity": 1,
                },
            ),
        ]
        state1 = reconstruct_state(ops)
        state2 = reconstruct_state(ops)
        self.assertEqual(state1.objects, state2.objects)
        self.assertEqual(state1.order, state2.order)

    def test_empty_operations_produces_empty_state(self) -> None:
        state = reconstruct_state([])
        self.assertEqual(len(state.objects), 0)
        self.assertEqual(state.order, [])

    def test_start_with_existing_state(self) -> None:
        """Replay with a start state should build on top of it."""
        start = ReconstructedState(
            objects={"s0": {"object_id": "s0", "object_type": "stroke"}},
            order=["s0"],
        )
        ops = [
            _op_dict(
                WhiteboardOperationType.CREATE_STROKE,
                {
                    "object_id": "s1",
                    "points": [{"x": 0, "y": 0}],
                    "color": "#000",
                    "width": 3,
                    "opacity": 1,
                },
            ),
        ]
        state = WhiteboardStateBuilder.replay(ops, start=start)
        self.assertEqual(len(state.objects), 2)
        self.assertEqual(state.order, ["s0", "s1"])

    def test_start_state_not_mutated(self) -> None:
        """The start state must not be mutated by replay."""
        start = ReconstructedState(
            objects={"s0": {"object_id": "s0"}},
            order=["s0"],
        )
        ops = [
            _op_dict(WhiteboardOperationType.CLEAR_CANVAS, {}),
        ]
        WhiteboardStateBuilder.replay(ops, start=start)
        # Original start should be unchanged.
        self.assertEqual(len(start.objects), 1)
        self.assertEqual(start.order, ["s0"])


class ReplayStrokePayloadTests(TestCase):
    def test_stroke_preserves_all_fields(self) -> None:
        payload = {
            "object_id": "s1",
            "points": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
            "color": "#dc2626",
            "width": 5,
            "opacity": 0.7,
            "creator_id": "alice",
        }
        ops = [_op_dict(WhiteboardOperationType.CREATE_STROKE, payload)]
        state = reconstruct_state(ops)
        obj = state.objects["s1"]
        self.assertEqual(obj["color"], "#dc2626")
        self.assertEqual(obj["width"], 5)
        self.assertAlmostEqual(obj["opacity"], 0.7)
        self.assertEqual(obj["creator_id"], "alice")
        self.assertEqual(len(obj["points"]), 2)
