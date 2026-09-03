"""Invitation service tests: create, duplicate, resend, revoke, accept, reject,
expiration, self-invitation, reuse, and mutual invitations."""

from datetime import timedelta

from django.core import mail
from django.utils import timezone

from apps.partnerships.enums import (
    InvitationStatus,
    PartnershipMemberStatus,
    PartnershipRole,
    PartnershipStatus,
)
from apps.partnerships.models import (
    Partnership,
    PartnershipInvitation,
)
from apps.partnerships.services import (
    AlreadyConnectedError,
    CannotInviteSelfError,
    CapacityError,
    InvitationUsageError,
    MutualInvitationExistsError,
    NotAuthorizedError,
    PendingAlreadyExistsError,
    accept_invitation,
    create_invitation,
    reject_invitation,
    resend_invitation,
    revoke_invitation,
)

from .base import PartnershipTestCase, make_user


class CreateInvitationTest(PartnershipTestCase):
    def test_creates_partnership_member_and_invitation(self):
        alice = make_user("alice@example.com")
        inv = create_invitation(alice, "bob@example.com")

        self.assertEqual(inv.status, InvitationStatus.SENT)
        self.assertEqual(inv.invitee_email, "bob@example.com")
        self.assertEqual(inv.partnership.status, PartnershipStatus.PENDING)
        # Alice is the active owner member.
        member = inv.partnership.members.get(user=alice)
        self.assertEqual(member.status, PartnershipMemberStatus.ACTIVE)
        self.assertEqual(member.role, PartnershipRole.OWNER)
        # The token is stored only as a digest, never the raw token.

        self.assertEqual(len(inv.token_hash), 64)  # sha256 hex
        self.assertNotIn("bob@example.com", inv.token_hash)

        # Email sent to the invitee.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["bob@example.com"])

    def test_email_normalized_to_lowercase(self):
        alice = make_user("alice@example.com")
        inv = create_invitation(alice, "Bob@Example.COM")
        self.assertEqual(inv.invitee_email, "bob@example.com")

    def test_cannot_invite_self(self):
        alice = make_user("alice@example.com")
        with self.assertRaises(CannotInviteSelfError):
            create_invitation(alice, "alice@example.com")

    def test_already_connected_cannot_invite(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        accept_invitation(inv, b)
        # a is now active; trying to invite someone else is blocked.
        with self.assertRaises(AlreadyConnectedError):
            create_invitation(a, "c@example.com")

    def test_pending_exists_blocks_different_recipient(self):
        alice = make_user("alice@example.com")
        create_invitation(alice, "bob@example.com")
        with self.assertRaises(PendingAlreadyExistsError):
            create_invitation(alice, "carol@example.com")

    def test_same_pending_recipient_resends_idempotently(self):
        alice = make_user("alice@example.com")
        create_invitation(alice, "bob@example.com")
        before = PartnershipInvitation.objects.latest("created_at")
        # Inviting the same email again should resend, not create a duplicate.
        renewed = create_invitation(alice, "bob@example.com")
        self.assertEqual(PartnershipInvitation.objects.filter(inviter=alice).count(), 1)
        self.assertEqual(renewed.pk, before.pk)
        # A new token was issued and email sent again.
        self.assertNotEqual(renewed.token_hash, before.token_hash)
        self.assertEqual(len(mail.outbox), 2)

    def test_mutual_invitation_detected(self):
        alice = make_user("alice@example.com")
        bob = make_user("bob@example.com")
        # Bob invites Alice first.
        create_invitation(bob, "alice@example.com")
        # Alice trying to invite Bob must NOT create a second partnership.
        with self.assertRaises(MutualInvitationExistsError):
            create_invitation(alice, "bob@example.com")
        self.assertEqual(Partnership.objects.count(), 1)


class AcceptInvitationTest(PartnershipTestCase):
    def test_accept_activates_partnership(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")

        partnership = accept_invitation(inv, b)

        partnership.refresh_from_db()
        self.assertEqual(partnership.status, PartnershipStatus.ACTIVE)
        self.assertTrue(partnership.accepted_at)

        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.ACCEPTED)
        self.assertTrue(inv.accepted_at)

        # Both are now active members.
        self.assertEqual(
            partnership.members.filter(status=PartnershipMemberStatus.ACTIVE).count(),
            2,
        )
        self.assertTrue(
            partnership.members.filter(user=b, status=PartnershipMemberStatus.ACTIVE).exists()
        )
        # Email sent to the owner notifying them the invite was accepted.
        self.assertTrue(
            any(
                "accepted" in (out.subject or "").lower() and "a@example.com" in out.to[0]
                for out in mail.outbox
            )
        )

    def test_accept_is_single_use(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        accept_invitation(inv, b)
        with self.assertRaises(InvitationUsageError):
            accept_invitation(inv, b)

    def test_accept_wrong_user_blocked(self):
        a = make_user("a@example.com")
        malicious = make_user("malicious@example.com")
        inv = create_invitation(a, "b@example.com")
        with self.assertRaises(NotAuthorizedError):
            accept_invitation(inv, malicious)

    def test_accept_expired_rejected(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        PartnershipInvitation.objects.filter(pk=inv.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        with self.assertRaises(InvitationUsageError):
            accept_invitation(inv, b)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.EXPIRED)

    def test_accept_user_already_connected_rejected(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        c = make_user("c@example.com")
        inv_ab = create_invitation(a, "b@example.com")
        # b is already connected to c via a different partnership.
        inv_bc = create_invitation(c, "b@example.com")
        accept_invitation(inv_bc, b)
        with self.assertRaises(CapacityError):
            accept_invitation(inv_ab, b)

    def test_accept_not_a_user_can_register_first(self):
        a = make_user("a@example.com")
        inv = create_invitation(a, "new@example.com")
        b = make_user("new@example.com")
        partnership = accept_invitation(inv, b)
        self.assertEqual(partnership.status, PartnershipStatus.ACTIVE)


class RejectInvitationTest(PartnershipTestCase):
    def test_reject_marks_invitation_and_cancels_partnership(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        reject_invitation(inv, b)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.REJECTED)
        self.assertTrue(inv.rejected_at)
        inv.partnership.refresh_from_db()
        self.assertEqual(inv.partnership.status, PartnershipStatus.CANCELLED)

    def test_reject_second_time_rejected(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        reject_invitation(inv, b)
        with self.assertRaises(InvitationUsageError):
            reject_invitation(inv, b)


class ResendRevokeTest(PartnershipTestCase):
    def test_resend_rotates_token_and_extends_expiry(self):
        a = make_user("a@example.com")
        inv = create_invitation(a, "b@example.com")
        before_hash = inv.token_hash
        before_expiry = inv.expires_at
        resend_invitation(inv, a)
        inv.refresh_from_db()
        self.assertNotEqual(inv.token_hash, before_hash)
        self.assertGreater(inv.expires_at, before_expiry)
        self.assertEqual(inv.status, InvitationStatus.SENT)
        self.assertEqual(len(mail.outbox), 2)

    def test_resend_by_non_inviter_blocked(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        with self.assertRaises(NotAuthorizedError):
            resend_invitation(inv, b)

    def test_revoke_marks_invitation_and_cancels(self):
        a = make_user("a@example.com")
        inv = create_invitation(a, "b@example.com")
        revoke_invitation(inv, a)
        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.REVOKED)
        self.assertTrue(inv.revoked_at)
        inv.partnership.refresh_from_db()
        self.assertEqual(inv.partnership.status, PartnershipStatus.CANCELLED)
        # Owner membership ends.
        self.assertIsNone(
            inv.partnership.members.filter(user=a, status=PartnershipMemberStatus.ACTIVE).first()
        )

    def test_revoke_by_non_inviter_blocked(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        with self.assertRaises(NotAuthorizedError):
            revoke_invitation(inv, b)
