"""Shared helpers for partnership tests."""

from __future__ import annotations

import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.partnerships.models import PartnershipInvitation

# Capture email in-memory so tests can assert on what was sent.
LOCMEM_MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}

# Matches the invite accept URL: /invite/<token>/
INVITE_URL_RE = re.compile(r"/invite/([A-Za-z0-9_-]+)/")


def make_user(email: str, *, verified: bool = True) -> User:
    user = User.objects.create_user(email=email, password="S3curePass!23")
    if verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
    return user


def extract_invite_token(email_body: str) -> str:
    """Extract the raw invitation token from an invitation email body."""
    match = INVITE_URL_RE.search(email_body)
    assert match, "invitation link not found in email body"
    return match.group(1)


@override_settings(MAILERS=LOCMEM_MAILERS)
class PartnershipTestCase(TestCase):
    """Base case: isolate the email outbox and rate-limit cache per test."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        mail.outbox.clear()

    def last_invitation(self) -> PartnershipInvitation:
        return PartnershipInvitation.objects.latest("created_at")
