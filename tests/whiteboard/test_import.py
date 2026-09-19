"""Whiteboard JSON import tests.

Tests the POST /api/whiteboards/<public_id>/import/ endpoint and
ImportService directly, covering:
- schema_version rejection (missing/wrong)
- whole-import rejection on any single bad object (atomic validation)
- object ids are always regenerated server-side, never trusted
- chunk-boundary behavior (imports larger than MAX_BATCH_OPERATIONS)
- MAX_IMPORT_OBJECTS boundary
- clear_first composes clear_canvas
- authorization and rate limiting
"""

from __future__ import annotations

import json

from django.test import TestCase
from django.urls import reverse

from apps.whiteboard import selectors
from apps.whiteboard.board_import import ImportService
from apps.whiteboard.errors import WhiteboardAPIError
from apps.whiteboard.models import WhiteboardOperation

from .base import WhiteboardTestCase, make_active_partnership, make_user
from .test_operations import _make_stroke_op, _op_url


def _import_url(partnership) -> str:  # type: ignore[no-untyped-def]
    return reverse("whiteboard:api_whiteboard_import", kwargs={"public_id": partnership.public_id})


def _export_object(object_id: str = "orig-id") -> dict:
    return {
        "object_type": "stroke",
        "object_id": object_id,
        "points": [{"x": 0, "y": 0}, {"x": 10, "y": 10}],
        "color": "#2563eb",
        "width": 3,
        "opacity": 1,
    }


def _export_payload(n: int = 1) -> dict:
    return {
        "schema_version": 1,
        "exported_at": "2026-09-04T00:00:00Z",
        "board_title": "Shared board",
        "objects": [_export_object(f"obj-{i}") for i in range(n)],
    }


class ImportServiceTests(TestCase):
    """Direct unit tests of ImportService -- no HTTP layer."""

    def setUp(self) -> None:
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = selectors.whiteboard_for_partnership(self.partnership)

    def test_import_creates_real_operations(self) -> None:
        result = ImportService().import_board(self.whiteboard, self.alice, _export_payload(3))
        self.assertEqual(result.imported, 3)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.version, 3)
        self.assertEqual(WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).count(), 3)

    def test_object_ids_are_regenerated_never_trusted(self) -> None:
        ImportService().import_board(
            self.whiteboard, self.alice, _export_payload_with_id("client-supplied-id")
        )
        ops = WhiteboardOperation.objects.filter(whiteboard=self.whiteboard)
        object_ids = [op.payload["object_id"] for op in ops]
        self.assertNotIn("client-supplied-id", object_ids)

    def test_missing_schema_version_rejected(self) -> None:
        payload = _export_payload(1)
        del payload["schema_version"]
        with self.assertRaises(WhiteboardAPIError):
            ImportService().import_board(self.whiteboard, self.alice, payload)

    def test_unsupported_schema_version_rejected(self) -> None:
        payload = _export_payload(1)
        payload["schema_version"] = 999
        with self.assertRaises(WhiteboardAPIError):
            ImportService().import_board(self.whiteboard, self.alice, payload)

    def test_whole_import_rejected_on_any_bad_object(self) -> None:
        payload = _export_payload(3)
        payload["objects"][1]["color"] = "not-a-color"
        with self.assertRaises(WhiteboardAPIError):
            ImportService().import_board(self.whiteboard, self.alice, payload)
        # Nothing was partially applied.
        self.assertEqual(WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).count(), 0)

    def test_objects_must_be_a_list(self) -> None:
        payload = {"schema_version": 1, "objects": "not-a-list"}
        with self.assertRaises(WhiteboardAPIError):
            ImportService().import_board(self.whiteboard, self.alice, payload)

    def test_oversized_import_rejected(self) -> None:
        from apps.whiteboard import limits

        payload = _export_payload(limits.MAX_IMPORT_OBJECTS + 1)
        with self.assertRaises(WhiteboardAPIError):
            ImportService().import_board(self.whiteboard, self.alice, payload)

    def test_chunked_import_beyond_max_batch_operations(self) -> None:
        from apps.whiteboard import limits

        n = limits.MAX_BATCH_OPERATIONS + 25  # forces (at least) 2 chunks
        result = ImportService().import_board(self.whiteboard, self.alice, _export_payload(n))
        self.assertEqual(result.imported, n)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.version, n)
        self.assertEqual(WhiteboardOperation.objects.filter(whiteboard=self.whiteboard).count(), n)

    def test_clear_first_composes_clear_canvas(self) -> None:
        # Existing content on the board.
        ImportService().import_board(self.whiteboard, self.alice, _export_payload(2))
        self.whiteboard.refresh_from_db()

        result = ImportService().import_board(
            self.whiteboard, self.alice, _export_payload(1), clear_first=True
        )
        self.assertEqual(result.imported, 1)
        state = selectors.get_whiteboard_state(self.whiteboard)
        self.assertEqual(len(state.objects), 1)  # cleared, then the one new object

    def test_import_is_additive_by_default(self) -> None:
        ImportService().import_board(self.whiteboard, self.alice, _export_payload(2))
        self.whiteboard.refresh_from_db()
        ImportService().import_board(self.whiteboard, self.alice, _export_payload(2))
        state = selectors.get_whiteboard_state(self.whiteboard)
        self.assertEqual(len(state.objects), 4)  # both imports kept


def _export_payload_with_id(object_id: str) -> dict:
    payload = _export_payload(1)
    payload["objects"][0]["object_id"] = object_id
    return payload


class ImportApiTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def test_import_via_api(self) -> None:
        resp = self.client.post(
            _import_url(self.partnership),
            data=json.dumps(_export_payload(2)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["imported"], 2)
        self.assertEqual(data["version"], 2)

    def test_import_bad_json_rejected(self) -> None:
        resp = self.client.post(
            _import_url(self.partnership), data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_import_requires_membership(self) -> None:
        self.client.force_login(make_user("outsider@example.com"))
        resp = self.client.post(
            _import_url(self.partnership),
            data=json.dumps(_export_payload(1)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_import_on_archived_board_rejected(self) -> None:
        from apps.whiteboard.enums import WhiteboardStatus

        whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        whiteboard.status = WhiteboardStatus.ARCHIVED
        whiteboard.save(update_fields=["status"])

        resp = self.client.post(
            _import_url(self.partnership),
            data=json.dumps(_export_payload(1)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_import_rate_limited(self) -> None:
        last = None
        for _ in range(6):
            last = self.client.post(
                _import_url(self.partnership),
                data=json.dumps(_export_payload(1)),
                content_type="application/json",
            )
        assert last is not None
        self.assertEqual(last.status_code, 429)

    def test_normal_operation_submit_still_works_after_import(self) -> None:
        """Regression guard: importing doesn't leave the whiteboard's version
        bookkeeping in a state that breaks a subsequent ordinary submit."""
        self.client.post(
            _import_url(self.partnership),
            data=json.dumps(_export_payload(2)),
            content_type="application/json",
        )
        resp = self.client.post(
            _op_url(self.partnership),
            data=json.dumps(_make_stroke_op(object_id="after-import", base_version=2)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
