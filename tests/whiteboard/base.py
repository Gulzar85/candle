"""Shared helpers for whiteboard tests."""

from __future__ import annotations

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings

from apps.accounts.models import User
from apps.partnerships.enums import PartnershipMemberStatus, PartnershipStatus
from apps.partnerships.models import Partnership, PartnershipMember

# Capture email in-memory so tests can assert on what was sent.
LOCMEM_MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}


def make_user(email: str, *, verified: bool = True) -> User:
    user = User.objects.create_user(email=email, password="S3curePass!23")
    if verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
    return user


def make_active_partnership(alice: User, bobby: User) -> Partnership:
    """Create an ACTIVE partnership with the two partners as active members.

    The membership-invariant trigger (``enforce_partnership_member_rules`` on
    PostgreSQL, its SQLite equivalent elsewhere) insists on exactly the two
    active members created here and a fresh user set per call (one active
    membership per user), which this helper satisfies.
    """
    partnership = Partnership.objects.create(status=PartnershipStatus.ACTIVE, accepted_at=None)
    PartnershipMember.objects.create(
        partnership=partnership,
        user=alice,
        role="OWNER",
        status=PartnershipMemberStatus.ACTIVE,
    )
    PartnershipMember.objects.create(
        partnership=partnership,
        user=bobby,
        role="MEMBER",
        status=PartnershipMemberStatus.ACTIVE,
    )
    return partnership


@override_settings(MAILERS=LOCMEM_MAILERS)
class WhiteboardTestCase(TestCase):
    """Base case: isolate the email outbox and rate-limit cache per test."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        mail.outbox.clear()


@override_settings(MAILERS=LOCMEM_MAILERS)
class ConcurrencyTestCase(TransactionTestCase):
    """Base for tests that spawn worker threads against the database.

    ``TestCase`` wraps every test in a single transaction that worker threads
    (which use their own DB connections) cannot see, so it cannot validate the
    real row-locking behaviour. ``TransactionTestCase`` commits each write,
    letting threads observe the committed rows and exercise genuine
    ``select_for_update`` concurrency. ``serialized_rollback`` restores the
    database between tests.
    """

    serialized_rollback = True
    reset_sequences = True

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        mail.outbox.clear()
