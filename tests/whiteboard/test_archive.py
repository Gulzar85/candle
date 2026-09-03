"""Whiteboard archive/unarchive (metadata) service + view tests.

Archiving is a presentation/lifecycle change stored on ``Whiteboard.status`` /
``archived_at``. It must be idempotent, gated by the same membership +
active-partnership authorization as every other board endpoint, and it must
make the board read-only (enforced independently by
``WhiteboardOperationService._ensure_writable``, covered in
test_operations.py / test_concurrency.py — not re-tested here).
"""

from __future__ import annotations

from django.urls import reverse

from apps.partnerships.enums import PartnershipStatus
from apps.whiteboard.enums import WhiteboardStatus
from apps.whiteboard.models import Whiteboard
from apps.whiteboard.service import WhiteboardMetadataService

from .base import WhiteboardTestCase, make_active_partnership, make_user


def archive_url(partnership) -> str:
    return reverse(
        "whiteboard:whiteboard_archive_toggle", kwargs={"public_id": partnership.public_id}
    )


class ArchiveServiceTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = Whiteboard.objects.create(partnership=self.partnership)

    def test_archive_sets_status_and_timestamp(self) -> None:
        self.assertFalse(self.whiteboard.is_archived)
        WhiteboardMetadataService().archive(self.whiteboard)
        self.whiteboard.refresh_from_db()
        self.assertTrue(self.whiteboard.is_archived)
        self.assertEqual(self.whiteboard.status, WhiteboardStatus.ARCHIVED)
        self.assertIsNotNone(self.whiteboard.archived_at)

    def test_archive_is_idempotent(self) -> None:
        service = WhiteboardMetadataService()
        service.archive(self.whiteboard)
        self.whiteboard.refresh_from_db()
        first_archived_at = self.whiteboard.archived_at
        service.archive(self.whiteboard)
        self.whiteboard.refresh_from_db()
        self.assertEqual(self.whiteboard.archived_at, first_archived_at)

    def test_unarchive_clears_status_and_timestamp(self) -> None:
        service = WhiteboardMetadataService()
        service.archive(self.whiteboard)
        service.unarchive(self.whiteboard)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)
        self.assertEqual(self.whiteboard.status, WhiteboardStatus.ACTIVE)
        self.assertIsNone(self.whiteboard.archived_at)

    def test_unarchive_of_active_board_is_a_noop(self) -> None:
        WhiteboardMetadataService().unarchive(self.whiteboard)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)


class ArchiveViewTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.mallory = make_user("mallory@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)
        self.whiteboard = Whiteboard.objects.create(partnership=self.partnership)

    def test_get_is_not_allowed(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.get(archive_url(self.partnership))
        self.assertEqual(resp.status_code, 405)

    def test_anonymous_is_redirected_to_login(self) -> None:
        resp = self.client.post(archive_url(self.partnership), {"action": "archive"})
        self.assertEqual(resp.status_code, 302)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)

    def test_non_member_gets_403(self) -> None:
        self.client.force_login(self.mallory)
        resp = self.client.post(archive_url(self.partnership), {"action": "archive"})
        self.assertEqual(resp.status_code, 403)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)

    def test_active_member_can_archive_and_unarchive(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.post(archive_url(self.partnership), {"action": "archive"})
        self.assertEqual(resp.status_code, 302)
        self.whiteboard.refresh_from_db()
        self.assertTrue(self.whiteboard.is_archived)

        self.client.force_login(self.bobby)
        resp = self.client.post(archive_url(self.partnership), {"action": "unarchive"})
        self.assertEqual(resp.status_code, 302)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)

    def test_unknown_action_changes_nothing(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.post(archive_url(self.partnership), {"action": "bogus"})
        self.assertEqual(resp.status_code, 302)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)

    def test_inactive_partnership_is_forbidden(self) -> None:
        self.partnership.status = PartnershipStatus.PENDING
        self.partnership.save(update_fields=["status"])
        self.client.force_login(self.alice)
        resp = self.client.post(archive_url(self.partnership), {"action": "archive"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("partnerships:partnership_home"), resp.url)
        self.whiteboard.refresh_from_db()
        self.assertFalse(self.whiteboard.is_archived)

    def test_redirect_defaults_to_dashboard_without_open_redirect(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.post(
            archive_url(self.partnership),
            {"action": "archive", "next": "https://evil.example/"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("accounts:dashboard"))
