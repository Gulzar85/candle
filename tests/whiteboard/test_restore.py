"""Restore-to-an-earlier-version tests.

Tests the POST /api/whiteboards/<public_id>/restore/ endpoint covering:
- restore never deletes/rewrites history (row count only grows)
- restore reflects the target historical state correctly
- restore-to-zero, restore-of-a-restore
- idempotency (duplicate operation_id)
- stale version / out-of-range target_sequence rejection
- archived board (read-only) and authorization
- rate limiting
"""

from __future__ import annotations

import json
import uuid

from django.urls import reverse

from apps.partnerships.enums import PartnershipStatus
from apps.whiteboard import selectors
from apps.whiteboard.enums import WhiteboardStatus
from apps.whiteboard.models import WhiteboardOperation

from .base import WhiteboardTestCase, make_active_partnership, make_user
from .test_operations import _make_stroke_op, _op_url, _state_url


def _restore_url(partnership) -> str:  # type: ignore[no-untyped-def]
    return reverse(
        "whiteboard:api_whiteboard_restore", kwargs={"public_id": partnership.public_id}
    )


def _restore_body(*, target_sequence: int, base_version: int) -> dict:
    return {
        "operation_id": str(uuid.uuid4()),
        "base_version": base_version,
        "target_sequence": target_sequence,
    }


class RestoreVersionTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.client.force_login(self.alice)

    def _submit(self, op: dict) -> dict:
        resp = self.client.post(
            _op_url(self.partnership), data=json.dumps(op), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_restore_never_deletes_rows(self) -> None:
        self._submit(_make_stroke_op(object_id="s1", base_version=0))
        self._submit(_make_stroke_op(object_id="s2", base_version=1))
        self._submit(_make_stroke_op(object_id="s3", base_version=2))

        whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        rows_before = WhiteboardOperation.objects.filter(whiteboard=whiteboard).count()
        self.assertEqual(rows_before, 3)

        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=1, base_version=3)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        rows_after = WhiteboardOperation.objects.filter(whiteboard=whiteboard).count()
        self.assertEqual(rows_after, 4)  # 3 original + 1 restore op, none deleted

    def test_restore_reflects_target_state(self) -> None:
        self._submit(_make_stroke_op(object_id="s1", base_version=0))
        self._submit(_make_stroke_op(object_id="s2", base_version=1))
        self._submit(_make_stroke_op(object_id="s3", base_version=2))

        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=1, base_version=3)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["version"], 4)

        state = self.client.get(_state_url(self.partnership)).json()
        ids = {o["object_id"] for o in state["objects"]}
        self.assertEqual(ids, {"s1"})

    def test_restore_to_zero_empties_board(self) -> None:
        self._submit(_make_stroke_op(object_id="s1", base_version=0))
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=0, base_version=1)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        state = self.client.get(_state_url(self.partnership)).json()
        self.assertEqual(state["count"], 0)

    def test_restore_of_a_restore(self) -> None:
        self._submit(_make_stroke_op(object_id="s1", base_version=0))
        self._submit(_make_stroke_op(object_id="s2", base_version=1))

        # Restore to seq 1 (only s1) -> version 3.
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=1, base_version=2)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["version"], 3)

        # Restore again to seq 0 (empty) -> version 4.
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=0, base_version=3)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["version"], 4)

        state = self.client.get(_state_url(self.partnership)).json()
        self.assertEqual(state["count"], 0)

        # Every operation, including both restores, is still a real row.
        whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        self.assertEqual(WhiteboardOperation.objects.filter(whiteboard=whiteboard).count(), 4)

    def test_restore_idempotent_retry(self) -> None:
        self._submit(_make_stroke_op(object_id="s1", base_version=0))
        body = _restore_body(target_sequence=0, base_version=1)

        first = self.client.post(
            _restore_url(self.partnership), data=json.dumps(body), content_type="application/json"
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["version"], 2)

        # Retry with the exact same operation_id: no double-apply.
        second = self.client.post(
            _restore_url(self.partnership), data=json.dumps(body), content_type="application/json"
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["version"], 2)
        self.assertTrue(second.json()["acks"][0]["duplicate"])

        whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        self.assertEqual(WhiteboardOperation.objects.filter(whiteboard=whiteboard).count(), 2)

    def test_stale_base_version_rejected(self) -> None:
        self._submit(_make_stroke_op(object_id="s1", base_version=0))
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=0, base_version=0)),  # stale
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "STALE_VERSION")

    def test_target_sequence_beyond_current_rejected(self) -> None:
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=99, base_version=0)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_negative_target_sequence_rejected(self) -> None:
        body = _restore_body(target_sequence=0, base_version=0)
        body["target_sequence"] = -1
        resp = self.client.post(
            _restore_url(self.partnership), data=json.dumps(body), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_restore_on_archived_board_rejected(self) -> None:
        whiteboard = selectors.whiteboard_for_partnership(self.partnership)
        whiteboard.status = WhiteboardStatus.ARCHIVED
        whiteboard.save(update_fields=["status"])

        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=0, base_version=0)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "WHITEBOARD_READ_ONLY")

    def test_restore_requires_membership(self) -> None:
        self.client.force_login(make_user("outsider@example.com"))
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=0, base_version=0)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_restore_requires_active_partnership(self) -> None:
        self.partnership.status = PartnershipStatus.ENDED
        self.partnership.save(update_fields=["status"])
        resp = self.client.post(
            _restore_url(self.partnership),
            data=json.dumps(_restore_body(target_sequence=0, base_version=0)),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_restore_rate_limited(self) -> None:
        # Same body repeatedly: rate limiting is checked before base_version,
        # so later 409s from a stale body still count against the limit.
        body = _restore_body(target_sequence=0, base_version=0)
        last = None
        for _ in range(11):
            last = self.client.post(
                _restore_url(self.partnership),
                data=json.dumps(body),
                content_type="application/json",
            )
        assert last is not None
        self.assertEqual(last.status_code, 429)
