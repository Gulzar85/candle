"""Whiteboard view + authorization tests.

The whiteboard URL is ``/partnership/<uuid:public_id>/whiteboard/``. Access is
server-authoritative: only an ACTIVE member of that partnership may open it, and
PENDING/ended partnerships never expose a shared space. No client-side state
grants access.
"""

from __future__ import annotations

from django.urls import reverse

from apps.partnerships.enums import PartnershipStatus
from apps.partnerships.models import Partnership, PartnershipMember

from .base import WhiteboardTestCase, make_active_partnership, make_user


def whiteboard_url(partnership: Partnership) -> str:
    return reverse("whiteboard:whiteboard_home", kwargs={"public_id": partnership.public_id})


class WhiteboardAccessTests(WhiteboardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bobby = make_user("bobby@example.com")
        self.mallory = make_user("mallory@example.com")
        self.partnership = make_active_partnership(self.alice, self.bobby)

    def test_anonymous_is_redirected_to_login(self) -> None:
        resp = self.client.get(whiteboard_url(self.partnership))
        expected = f"{reverse('accounts:login')}?next={whiteboard_url(self.partnership)}"
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], expected)

    def test_non_member_gets_403(self) -> None:
        self.client.force_login(self.mallory)
        resp = self.client.get(whiteboard_url(self.partnership))
        self.assertEqual(resp.status_code, 403)

    def test_active_member_can_open_whiteboard(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.get(whiteboard_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<canvas", html=False)

    def test_page_keys_realtime_bindings_to_partnership_public_id(self) -> None:
        """The page/API/WS bindings must key off the *partnership* public_id."""
        self.client.force_login(self.alice)
        resp = self.client.get(whiteboard_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        expected = str(self.partnership.public_id)
        # data attributes used to build /api/whiteboards/<uuid>/ and
        # /ws/whiteboards/<uuid>/, so they must carry the partnership id.
        self.assertContains(resp, f'data-wb-public-id="{expected}"', html=False)
        self.assertContains(resp, f'data-wb-api-base="/api/whiteboards/{expected}"', html=False)
        # And must NOT key the page off the whiteboard's own (distinct) uuid.
        self.assertNotContains(resp, f'data-wb-public-id="{self.partnership.whiteboard.public_id}"')

    def test_lazy_whiteboard_created_for_active_partnership(self) -> None:
        self.client.force_login(self.bobby)
        resp = self.client.get(whiteboard_url(self.partnership))
        self.assertEqual(resp.status_code, 200)
        whiteboard = self.partnership.whiteboard  # OneToOne related accessor
        self.assertEqual(whiteboard.partnership_id, self.partnership.pk)

    def test_whiteboard_row_is_reused_not_duplicated(self) -> None:
        self.client.force_login(self.alice)
        self.client.get(whiteboard_url(self.partnership))
        first = self.partnership.whiteboard
        self.client.get(whiteboard_url(self.partnership))
        # Same row; get_or_create keeps it stable and unique (OneToOne).
        self.assertEqual(self.partnership.whiteboard.pk, first.pk)

    def test_pending_partnership_redirects_to_home_not_whiteboard(self) -> None:
        self.partnership.status = PartnershipStatus.PENDING
        self.partnership.save(update_fields=["status"])
        # Alice is still the active owner member of the pending partnership.
        self.client.force_login(self.alice)
        resp = self.client.get(whiteboard_url(self.partnership))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("partnerships:partnership_home"))

    def test_member_who_left_cannot_access(self) -> None:
        membership = PartnershipMember.objects.get(partnership=self.partnership, user=self.bobby)
        membership.status = "LEFT"
        membership.save(update_fields=["status"])
        self.client.force_login(self.bobby)
        resp = self.client.get(whiteboard_url(self.partnership))
        self.assertEqual(resp.status_code, 403)

    def test_unknown_public_id_404(self) -> None:
        self.client.force_login(self.alice)
        url = reverse(
            "whiteboard:whiteboard_home",
            kwargs={"public_id": "00000000-0000-0000-0000-000000000000"},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
