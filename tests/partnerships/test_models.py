from django.db import IntegrityError

from apps.partnerships.enums import (
    PartnershipMemberStatus,
    PartnershipRole,
    PartnershipStatus,
)
from apps.partnerships.models import (
    Partnership,
    PartnershipInvitation,
    PartnershipMember,
)
from apps.partnerships.services import (
    accept_invitation,
    create_invitation,
    end_partnership,
)

from .base import PartnershipTestCase, make_user


class PartnershipModelTest(PartnershipTestCase):
    def test_public_id_is_unique_uuid(self):
        p1 = Partnership.objects.create()
        p2 = Partnership.objects.create()
        self.assertNotEqual(p1.public_id, p2.public_id)
        self.assertTrue(p1.public_id)

    def test_default_status_is_pending(self):
        p = Partnership.objects.create()
        self.assertEqual(p.status, PartnershipStatus.PENDING)
        self.assertTrue(p.is_pending)
        self.assertFalse(p.is_active)

    def test_active_flags(self):
        p = Partnership.objects.create(status=PartnershipStatus.ACTIVE)
        self.assertTrue(p.is_active)
        self.assertFalse(p.is_pending)

    def test_is_active_property(self):
        p = Partnership.objects.create(status=PartnershipStatus.ACTIVE)
        self.assertTrue(p.is_active)


class PartnershipMemberModelTest(PartnershipTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("owner@example.com")
        self.member = make_user("member@example.com")
        self.partnership = Partnership.objects.create()

    def test_member_creates_with_active_status(self):
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.owner,
            role=PartnershipRole.OWNER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        self.assertTrue(
            PartnershipMember.objects.filter(
                partnership=self.partnership, user=self.owner
            ).exists()
        )

    def test_duplicate_member_in_partnership_rejected(self):
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.owner,
            role=PartnershipRole.OWNER,
        )
        with self.assertRaises(IntegrityError):
            PartnershipMember.objects.create(
                partnership=self.partnership,
                user=self.owner,
                role=PartnershipRole.MEMBER,
            )

    def test_partnership_allows_two_active_members(self):
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.owner,
            role=PartnershipRole.OWNER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.member,
            role=PartnershipRole.MEMBER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        count = PartnershipMember.objects.filter(
            partnership=self.partnership, status=PartnershipMemberStatus.ACTIVE
        ).count()
        self.assertEqual(count, 2)

    def test_third_active_member_trigger_rejects(self):
        third = make_user("third@example.com")
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.owner,
            role=PartnershipRole.OWNER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.member,
            role=PartnershipRole.MEMBER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        with self.assertRaises(IntegrityError):
            PartnershipMember.objects.create(
                partnership=self.partnership,
                user=third,
                role=PartnershipRole.MEMBER,
                status=PartnershipMemberStatus.ACTIVE,
            )

    def test_user_one_active_membership_constraint(self):
        """The partial unique index on (user) where status=active holds."""
        p2 = Partnership.objects.create()
        PartnershipMember.objects.create(
            partnership=p2,
            user=self.member,
            role=PartnershipRole.MEMBER,
            status=PartnershipMemberStatus.ACTIVE,
        )
        with self.assertRaises(IntegrityError):
            PartnershipMember.objects.create(
                partnership=self.partnership,
                user=self.member,
                role=PartnershipRole.MEMBER,
                status=PartnershipMemberStatus.ACTIVE,
            )


class PartnershipInvitationModelTest(PartnershipTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("owner@example.com")
        self.partnership = Partnership.objects.create()
        PartnershipMember.objects.create(
            partnership=self.partnership,
            user=self.owner,
            role=PartnershipRole.OWNER,
            status=PartnershipMemberStatus.ACTIVE,
        )

    def test_token_hash_not_plaintext(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.partnerships.tokens import digest

        raw = "supersecrettoken"
        inv = PartnershipInvitation.objects.create(
            partnership=self.partnership,
            inviter=self.owner,
            invitee_email="bob@example.com",
            token_hash=digest(raw),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.assertNotIn(raw, inv.token_hash)
        self.assertNotEqual(inv.token_hash, raw)
        self.assertEqual(inv.token_hash, digest(raw))


class LifecycleEndTest(PartnershipTestCase):
    def test_end_partnership_preserves_records(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        accept_invitation(inv, b)
        partnership = a.memberships.first().partnership
        self.assertEqual(partnership.status, PartnershipStatus.ACTIVE)

        end_partnership(partnership, a)
        partnership.refresh_from_db()
        self.assertEqual(partnership.status, PartnershipStatus.ENDED)
        self.assertTrue(partnership.ended_at)
        # Records are preserved, not deleted.
        self.assertEqual(partnership.members.count(), 2)
        # Both memberships became LEFT.
        self.assertEqual(
            partnership.members.filter(status=PartnershipMemberStatus.ACTIVE).count(),
            0,
        )
        self.assertEqual(
            partnership.members.filter(status=PartnershipMemberStatus.LEFT).count(),
            2,
        )
        # Partner is no longer connected.
        self.assertIsNone(a.memberships.filter(status="active").first())
        self.assertIsNone(b.memberships.filter(status="active").first())

    def test_end_preserves_events_and_notifications(self):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        inv = create_invitation(a, "b@example.com")
        accept_invitation(inv, b)
        partnership = a.memberships.first().partnership
        end_partnership(partnership, a)
        self.assertTrue(partnership.events.filter(event_type="partnership_ended").exists())
        self.assertTrue(partnership.events.filter(event_type="member_left").count() >= 1)
