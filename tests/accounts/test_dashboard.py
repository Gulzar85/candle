"""Dashboard (product home) tests.

The dashboard is the signed-in home page. It surfaces the user's single active
partnership + whiteboard when one exists, and a "invite a partner" empty state
when it does not. Authorization follows the account layer: only an authenticated
user reaches the dashboard, and an active partnership is always a partner's own.
"""

from __future__ import annotations

from django.urls import reverse

from apps.partnerships.enums import PartnershipMemberStatus, PartnershipRole, PartnershipStatus
from apps.partnerships.models import Partnership, PartnershipMember
from apps.whiteboard.models import Whiteboard
from tests.whiteboard.base import make_active_partnership, make_user

from .base import AccountTestCase


def dashboard_url() -> str:
    return reverse("accounts:dashboard")


class DashboardTests(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.alice = make_user("alice@example.com")

    def test_anonymous_redirected_to_login(self) -> None:
        resp = self.client.get(dashboard_url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])

    def test_no_partnership_shows_empty_state_cta(self) -> None:
        self.client.force_login(self.alice)
        resp = self.client.get(dashboard_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Invite partner")
        self.assertContains(resp, "Candle is made for two.")

    def test_active_partnership_shows_board(self) -> None:
        bobby = make_user("bobby@example.com")
        partnership = make_active_partnership(self.alice, bobby)
        Whiteboard.objects.create(partnership=partnership, title="Our shared board")
        self.client.force_login(self.alice)
        resp = self.client.get(dashboard_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Our shared board")
        self.assertContains(resp, "Open board")
        self.assertContains(resp, str(partnership.public_id))

    def test_pending_partnership_shows_waiting_state(self) -> None:
        # Owner-only pending partnership (bobby not yet joined).
        partnership = Partnership.objects.create(status=PartnershipStatus.PENDING)
        PartnershipMember.objects.create(
            partnership=partnership,
            user=self.alice,
            role=PartnershipRole.OWNER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        self.client.force_login(self.alice)
        resp = self.client.get(dashboard_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Waiting for your partner")
        self.assertNotContains(resp, "Open board")
