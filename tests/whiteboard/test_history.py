"""Human-readable whiteboard history tests.

Covers apps/whiteboard/history.py (pure humanization logic) and the
GET /api/whiteboards/<public_id>/history/ endpoint.
"""

from __future__ import annotations

import json
import uuid

from django.test import TestCase
from django.urls import reverse

from apps.whiteboard import selectors
from apps.whiteboard.enums import WhiteboardOperationType
from apps.whiteboard.history import build_history, entry_to_dict
from apps.whiteboard.restore import RestoreService
from apps.whiteboard.service import WhiteboardOperationService
from apps.whiteboard.validator import OperationValidator

from .base import WhiteboardTestCase, make_active_partnership, make_user
from .test_operations import _make_stroke_op, _op_url


def _history_url(partnership) -> str:  # type: ignore[no-untyped-def]
    return reverse(
        "whiteboard:api_whiteboard_history", kwargs={"public_id": partnership.public_id}
    )


def _make_delete_op(*, object_id: str, base_version: int) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.DELETE_OBJECT,
        "base_version": base_version,
        "payload": {"object_id": object_id},
    }


def _make_clear_op(*, base_version: int) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.CLEAR_CANVAS,
        "base_version": base_version,
        "payload": {},
    }


def _make_move_op(*, object_id: str, base_version: int) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.MOVE_OBJECT,
        "base_version": base_version,
        "payload": {"object_id": object_id, "dx": 1, "dy": 1},
    }


def _make_resize_op(*, object_id: str, base_version: int) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation_type": WhiteboardOperationType.RESIZE_OBJECT,
        "base_version": base_version,
        "payload": {
            "object_id": object_id,
            "anchor": {"x": 0, "y": 0},
            "scale_x": 2,
            "scale_y": 2,
        },
    }


class HistoryHumanizationTests(TestCase):
    """Direct unit tests of build_history() -- no HTTP layer involved."""

    def setUp(self) -> None:
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        self.svc = WhiteboardOperationService(validator=OperationValidator())

    def _submit(self, actor, op: dict) -> None:  # type: ignore[no-untyped-def]
        self.svc.submit(self.whiteboard, actor, op)
        self.whiteboard.refresh_from_db()

    def test_all_op_types_have_a_readable_sentence(self) -> None:
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        self._submit(self.alice, _make_move_op(object_id="s1", base_version=1))
        self._submit(self.alice, _make_resize_op(object_id="s1", base_version=2))
        self._submit(self.alice, _make_delete_op(object_id="s1", base_version=3))
        self._submit(self.alice, _make_clear_op(base_version=4))
        RestoreService().restore(
            self.whiteboard,
            self.alice,
            operation_id=str(uuid.uuid4()),
            base_version=5,
            target_sequence=0,
        )
        self.whiteboard.refresh_from_db()

        entries = build_history(self.whiteboard, self.alice)
        texts = {e.operation_type: e.text for e in entries}
        self.assertEqual(texts[WhiteboardOperationType.CREATE_STROKE], "You added a drawing.")
        self.assertEqual(texts[WhiteboardOperationType.MOVE_OBJECT], "You moved a drawing.")
        self.assertEqual(texts[WhiteboardOperationType.RESIZE_OBJECT], "You resized a drawing.")
        self.assertEqual(texts[WhiteboardOperationType.DELETE_OBJECT], "You removed a drawing.")
        self.assertEqual(texts[WhiteboardOperationType.CLEAR_CANVAS], "You cleared the board.")
        self.assertEqual(
            texts[WhiteboardOperationType.RESTORE_VERSION], "You restored an earlier version."
        )
        # No raw operation ids or internal jargon anywhere in the text.
        for e in entries:
            self.assertNotIn("operation_id", e.text)
            self.assertNotIn("sequence", e.text.lower())

    def test_actor_framing_is_viewer_relative(self) -> None:
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        self._submit(self.bobby, _make_stroke_op(object_id="s2", base_version=1))

        as_alice = build_history(self.whiteboard, self.alice)
        as_bobby = build_history(self.whiteboard, self.bobby)

        # Newest entry (bobby's stroke): "You" from bobby's view, "Partner" from alice's.
        self.assertEqual(as_bobby[0].text, "You added a drawing.")
        self.assertEqual(as_alice[0].text, "Partner added a drawing.")
        # Oldest entry (alice's stroke): reversed.
        self.assertEqual(as_alice[1].text, "You added a drawing.")
        self.assertEqual(as_bobby[1].text, "Partner added a drawing.")

    def test_newest_first_ordering(self) -> None:
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        self._submit(self.alice, _make_stroke_op(object_id="s2", base_version=1))
        self._submit(self.alice, _make_clear_op(base_version=2))

        entries = build_history(self.whiteboard, self.alice)
        sequences = [e.sequence for e in entries]
        self.assertEqual(sequences, sorted(sequences, reverse=True))
        self.assertEqual(entries[0].operation_type, WhiteboardOperationType.CLEAR_CANVAS)

    def test_consecutive_same_actor_same_type_collapse(self) -> None:
        for i in range(4):
            self._submit(self.alice, _make_stroke_op(object_id=f"s{i}", base_version=i))

        entries = build_history(self.whiteboard, self.alice)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].count, 4)
        self.assertIn("×4", entries[0].text)

    def test_collapse_breaks_on_different_actor(self) -> None:
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        self._submit(self.bobby, _make_stroke_op(object_id="s2", base_version=1))
        self._submit(self.alice, _make_stroke_op(object_id="s3", base_version=2))

        entries = build_history(self.whiteboard, self.alice)
        self.assertEqual(len(entries), 3)
        self.assertTrue(all(e.count == 1 for e in entries))

    def test_collapse_breaks_on_different_type(self) -> None:
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        self._submit(self.alice, _make_move_op(object_id="s1", base_version=1))

        entries = build_history(self.whiteboard, self.alice)
        self.assertEqual(len(entries), 2)

    def test_clear_and_restore_never_collapse(self) -> None:
        self._submit(self.alice, _make_clear_op(base_version=0))
        self._submit(self.alice, _make_clear_op(base_version=1))

        entries = build_history(self.whiteboard, self.alice)
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e.count == 1 for e in entries))

    def test_can_restore_false_only_for_current_version(self) -> None:
        # Different op types so they don't collapse into one entry.
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        self._submit(self.alice, _make_clear_op(base_version=1))

        entries = build_history(self.whiteboard, self.alice)
        self.assertEqual(len(entries), 2)
        self.assertFalse(entries[0].can_restore)  # newest == current version
        self.assertTrue(entries[1].can_restore)

    def test_pagination_before_sequence(self) -> None:
        for i in range(5):
            self._submit(self.alice, _make_clear_op(base_version=i))  # never collapses

        first_page = build_history(self.whiteboard, self.alice, limit=2)
        self.assertEqual(len(first_page), 2)
        self.assertEqual([e.sequence for e in first_page], [5, 4])

        second_page = build_history(
            self.whiteboard, self.alice, before_sequence=first_page[-1].sequence, limit=2
        )
        self.assertEqual([e.sequence for e in second_page], [3, 2])

    def test_empty_board_has_no_history(self) -> None:
        entries = build_history(self.whiteboard, self.alice)
        self.assertEqual(entries, [])

    def test_entry_to_dict_has_no_raw_internals(self) -> None:
        self._submit(self.alice, _make_stroke_op(object_id="s1", base_version=0))
        entry = build_history(self.whiteboard, self.alice)[0]
        d = entry_to_dict(entry)
        self.assertEqual(
            set(d.keys()),
            {"sequence", "operation_type", "text", "count", "created_at", "can_restore"},
        )


class HistoryApiTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_history_empty_board(self) -> None:
        resp = self.client.get(_history_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["count"], 0)

    def test_history_after_operations(self) -> None:
        self.client.post(
            _op_url(self.partnership),
            data=json.dumps(_make_stroke_op(object_id="s1", base_version=0)),
            content_type="application/json",
        )
        resp = self.client.get(_history_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["entries"][0]["text"], "You added a drawing.")
        self.assertTrue(data["entries"][0]["can_restore"] is False)

    def test_history_requires_membership(self) -> None:
        self.client.force_login(make_user("outsider@example.com"))
        resp = self.client.get(_history_url(self.partnership))
        self.assertEqual(resp.status_code, 403)

    def test_history_negative_before_sequence_rejected(self) -> None:
        resp = self.client.get(f"{_history_url(self.partnership)}?before_sequence=-1")
        self.assertEqual(resp.status_code, 400)

    def test_history_limit_respected(self) -> None:
        for i in range(5):
            self.client.post(
                _op_url(self.partnership),
                data=json.dumps(_make_clear_op(base_version=i)),
                content_type="application/json",
            )
        resp = self.client.get(f"{_history_url(self.partnership)}?limit=2")
        data = resp.json()
        self.assertEqual(data["count"], 2)
