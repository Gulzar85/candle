"""Object-level authorization (IDOR) and security tests for partnership views.

The most important question this file answers: can an authenticated user access
another user's partnership/invitation simply by changing an ID/public ID in the
URL? The answer must be NO — verified here.
"""

from django.core import mail
from django.urls import reverse

from apps.partnerships.enums import InvitationStatus, PartnershipStatus
from apps.partnerships.models import (
    PartnershipInvitation,
)
from apps.partnerships.services import (
    accept_invitation,
    create_invitation,
)

from .base import PartnershipTestCase, extract_invite_token, make_user


class AuthorizationIDORTest(PartnershipTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bob = make_user("bob@example.com")
        self.mallory = make_user("mallory@example.com")

    def test_invite_requires_login(self):
        response = self.client.get(reverse("partnerships:partnership_home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_user_cannot_view_another_partnership_home(self):
        """The home page only shows *your own* partnership — no ID needed, so
        there is nothing to tamper with. This is the first line of defense."""
        inv = create_invitation(self.alice, "bob@example.com")
        accept_invitation(inv, self.bob)

        self.client.force_login(self.mallory)
        response = self.client.get(reverse("partnerships:partnership_home"))
        self.assertEqual(response.status_code, 200)
        # Mallory sees the empty-state invite form, NOT Alice/Bob's partnership.
        self.assertContains(response, "Invite your partner")
        self.assertNotContains(response, "Enter your shared space")

    def test_mallory_cannot_view_alice_detail(self):
        # Build an invite landing scenario (does not depend on the landing view).
        inv = create_invitation(self.alice, "bob@example.com")
        self.client.force_login(self.mallory)
        # The invitation detail is only viewable by inviter/invitee/member.
        response = self.client.get(reverse("partnerships:invitation_detail", args=[inv.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_mallory_cannot_accept_alice_invitation(self):
        inv = create_invitation(self.alice, "bob@example.com")
        self.client.force_login(self.mallory)
        response = self.client.post(
            reverse("partnerships:invitation_accept", args=[inv.public_id])
        )
        self.assertEqual(response.status_code, 403)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.SENT)

    def test_mallory_cannot_reject_alice_invitation(self):
        inv = create_invitation(self.alice, "bob@example.com")
        self.client.force_login(self.mallory)
        response = self.client.post(
            reverse("partnerships:invitation_reject", args=[inv.public_id])
        )
        self.assertEqual(response.status_code, 403)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.SENT)

    def test_mallory_cannot_resend_alice_invitation(self):
        inv = create_invitation(self.alice, "bob@example.com")
        before_hash = inv.token_hash
        self.client.force_login(self.mallory)
        response = self.client.post(
            reverse("partnerships:invitation_resend", args=[inv.public_id])
        )
        self.assertEqual(response.status_code, 403)
        inv.refresh_from_db()
        self.assertEqual(inv.token_hash, before_hash)

    def test_mallory_cannot_revoke_alice_invitation(self):
        inv = create_invitation(self.alice, "bob@example.com")
        self.client.force_login(self.mallory)
        response = self.client.post(
            reverse("partnerships:invitation_revoke", args=[inv.public_id])
        )
        self.assertEqual(response.status_code, 403)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.SENT)

    def test_mallory_cannot_end_alice_partnership(self):
        inv = create_invitation(self.alice, "bob@example.com")
        accept_invitation(inv, self.bob)
        partnership = self.alice.memberships.first().partnership
        self.client.force_login(self.mallory)
        response = self.client.post(
            reverse("partnerships:partnership_end"),
            {"partnership_id": str(partnership.public_id), "confirm": "end"},
        )
        self.assertEqual(response.status_code, 403)
        partnership.refresh_from_db()
        self.assertEqual(partnership.status, PartnershipStatus.ACTIVE)

    def test_token_auth_does_not_trust_url_ids(self):
        """Accepting uses the token hashed at rest; a guessed public_id alone
        cannot be used because the invitee check runs server-side."""
        inv = create_invitation(self.alice, "bob@example.com")
        self.client.force_login(self.mallory)
        # Mallory has no token; even hitting the accept by public_id fails.
        response = self.client.post(
            reverse("partnerships:invitation_accept", args=[inv.public_id])
        )
        self.assertEqual(response.status_code, 403)


class LandingPrivacyTest(PartnershipTestCase):
    def setUp(self):
        super().setUp()
        self.alice = make_user("alice@example.com")
        self.bob = make_user("bob@example.com")

    def _invite(self) -> tuple[PartnershipInvitation, str]:
        inv = create_invitation(self.alice, "bob@example.com")
        token = extract_invite_token(mail.outbox[0].body)
        return inv, token

    def test_landing_shows_inviter_name_not_email(self):
        inv, token = self._invite()
        response = self.client.get(reverse("partnerships:invite_accept", args=[token]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're invited")
        # Do NOT leak the inviter's or invitee's email.
        self.assertNotContains(response, "alice@example.com")
        self.assertNotContains(response, "bob@example.com")

    def test_invalid_token_returns_not_found(self):
        response = self.client.get(reverse("partnerships:invite_accept", args=["garbage-token"]))
        self.assertEqual(response.status_code, 404)

    def test_empty_token_not_found(self):
        response = self.client.get(reverse("partnerships:invite_accept", args=["x"]))
        # No valid invitation resolves; 404 (not a revealing 400).
        self.assertEqual(response.status_code, 404)

    def test_expired_invitation_shows_expired(self):
        from datetime import timedelta

        from django.utils import timezone

        inv, token = self._invite()
        PartnershipInvitation.objects.filter(pk=inv.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        response = self.client.get(reverse("partnerships:invite_accept", args=[token]))
        self.assertContains(response, "expired")

    def test_revoked_invitation_shows_unavailable(self):
        from apps.partnerships.services import revoke_invitation

        inv, token = self._invite()
        revoke_invitation(inv, self.alice)
        response = self.client.get(reverse("partnerships:invite_accept", args=[token]))
        self.assertContains(response, "no longer available")

    def test_anonymous_sees_sign_in_or_register_prompt(self):
        inv, token = self._invite()
        inv.invitee = self.bob  # b has an account
        inv.save(update_fields=["invitee"])
        response = self.client.get(reverse("partnerships:invite_accept", args=[token]))
        self.assertContains(response, "Sign in to accept")


class InvitationFlowViewTest(PartnershipTestCase):
    def test_invite_post_creates_and_redirects(self):
        alice = make_user("alice@example.com")
        self.client.force_login(alice)
        response = self.client.post(reverse("partnerships:invite"), {"email": "bob@example.com"})
        self.assertRedirects(
            response,
            reverse("partnerships:partnership_home"),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            PartnershipInvitation.objects.filter(
                inviter=alice, invitee_email="bob@example.com"
            ).exists()
        )

    def test_invite_too_many_requests_rate_limited(self):
        alice = make_user("alice@example.com")
        self.client.force_login(alice)
        limit = 5
        for i in range(limit):
            self.client.post(reverse("partnerships:invite"), {"email": f"bob{i}@example.com"})
        # The 6th request should be rate-limited.
        response = self.client.post(reverse("partnerships:invite"), {"email": "bob99@example.com"})
        self.assertEqual(response.status_code, 429)

    def test_accept_through_view(self):
        alice = make_user("alice@example.com")
        bob = make_user("bob@example.com")
        inv = create_invitation(alice, "bob@example.com")
        self.client.force_login(bob)
        response = self.client.post(
            reverse("partnerships:invitation_accept", args=[inv.public_id])
        )
        self.assertRedirects(
            response,
            reverse("partnerships:partnership_home"),
            fetch_redirect_response=False,
        )
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.ACCEPTED)

    def test_reject_through_view(self):
        alice = make_user("alice@example.com")
        bob = make_user("bob@example.com")
        inv = create_invitation(alice, "bob@example.com")
        self.client.force_login(bob)
        response = self.client.post(
            reverse("partnerships:invitation_reject", args=[inv.public_id])
        )
        self.assertEqual(response.status_code, 302)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.REJECTED)

    def test_end_partnership_requires_confirmation(self):
        alice = make_user("alice@example.com")
        bob = make_user("bob@example.com")
        inv = create_invitation(alice, "bob@example.com")
        accept_invitation(inv, bob)
        partnership = alice.memberships.first().partnership
        self.client.force_login(alice)

        # Wrong confirmation text is rejected.
        self.client.post(
            reverse("partnerships:partnership_end"),
            {"partnership_id": str(partnership.public_id), "confirm": "no"},
        )
        partnership.refresh_from_db()
        self.assertEqual(partnership.status, PartnershipStatus.ACTIVE)

        # Correct confirmation ends it.
        self.client.post(
            reverse("partnerships:partnership_end"),
            {"partnership_id": str(partnership.public_id), "confirm": "end"},
        )
        partnership.refresh_from_db()
        self.assertEqual(partnership.status, PartnershipStatus.ENDED)
