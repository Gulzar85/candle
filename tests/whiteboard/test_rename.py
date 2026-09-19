"""Whiteboard rename (metadata) service + API tests.

Renaming is a presentation change stored on ``Whiteboard.title``. It must not
create an operation nor touch the version ledger, and it is subject to the exact
same authorization as every other board endpoint (membership + active
partnership).
"""

from __future__ import annotations

import json

from django.urls import reverse

from apps.partnerships.enums import PartnershipStatus
from apps.whiteboard.models import Whiteboard
from apps.whiteboard.service import WhiteboardMetadataService

from .base import WhiteboardTestCase, make_active_partnership, make_user


def rename_url(partnership) -> str:
    return reverse("whiteboard:api_whiteboard_rename", kwargs={"public_id": partnership.public_id})


class RenameServiceTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = Whiteboard.objects.create(
            partnership=self.partnership, title="Current title"
        )

    def test_rename_returns_and_persists_trimmed_title(self) -> None:
        result = WhiteboardMetadataService().rename(self.whiteboard, "  Our ideas  ")
        self.assertEqual(result, "Our ideas")
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Our ideas")

    def test_rename_blank_title_rejected_and_not_saved(self) -> None:
        service = WhiteboardMetadataService()
        from apps.whiteboard.errors import InvalidOperationError

        with self.assertRaises(InvalidOperationError):
            service.rename(self.whiteboard, "   ")
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Current title")

    def test_rename_too_long_rejected(self) -> None:
        from apps.whiteboard.errors import InvalidOperationError

        service = WhiteboardMetadataService()
        # > max_length (120)
        with self.assertRaises(InvalidOperationError):
            service.rename(self.whiteboard, "x" * 121)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Current title")

    def test_rename_does_not_bump_version_or_create_operations(self) -> None:
        # Create one prior operation so version is > 0, then rename.
        from apps.whiteboard.models import WhiteboardOperation

        WhiteboardOperation.objects.create(
            whiteboard=self.whiteboard,
            operation_id="00000000-0000-0000-0000-000000000001",
            sequence=1,
            actor=self.alice,
            operation_type="create_stroke",
            payload={},
            base_version=0,
            resulting_version=1,
        )
        self.whiteboard.refresh_from_db()
        before_version = self.whiteboard.version
        WhiteboardMetadataService().rename(self.whiteboard, "Renamed")
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Renamed")
        self.assertEqual(self.whiteboard.version, before_version)
        self.assertEqual(self.whiteboard.operations.count(), 1)


class RenameApiTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.mallory = make_user("mallory@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = Whiteboard.objects.create(partnership=self.partnership, title="Old")

    def _patch(self, title):
        return self.client.patch(
            rename_url(self.partnership),
            data=json.dumps({"title": title}),
            content_type="application/json",
        )

    def test_anonymous_is_denied(self) -> None:
        # DRF `IsAuthenticated` returns 403 (not a login redirect) for API calls.
        resp = self.client.patch(
            rename_url(self.partnership),
            data=json.dumps({"title": "Hi"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_member_gets_403(self) -> None:
        self.client.force_login(self.mallory)
        resp = self._patch("Hi")
        self.assertEqual(resp.status_code, 403)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Old")

    def test_active_member_can_rename(self) -> None:
        self.client.force_login(self.alice)
        resp = self._patch("  Renamed title  ")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Renamed title")
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Renamed title")

    def test_blank_title_rejected(self) -> None:
        self.client.force_login(self.bobby)
        resp = self._patch("   ")
        self.assertEqual(resp.status_code, 400)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Old")

    def test_non_string_title_rejected(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.patch(
            rename_url(self.partnership),
            data=json.dumps({"title": 123}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.title, "Old")

    def test_inactive_partnership_is_forbidden(self) -> None:
        self.partnership.status = PartnershipStatus.PENDING
        self.partnership.save(update_fields=["status"])
        self.client.force_login(self.alice)
        resp = self._patch("Hi")
        self.assertEqual(resp.status_code, 403)
