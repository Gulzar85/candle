"""Shared helpers for accounts tests."""

from __future__ import annotations

import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

# Capture email in-memory so tests can assert on what was sent.
LOCMEM_MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}

VERIFY_URL_RE = re.compile(r"/accounts/verify-email/([^/\s]+)/([^/\s]+)/")
RESET_URL_RE = re.compile(r"/accounts/password-reset/([^/\s]+)/([^/\s]+)/")


def extract_verify_uid_token(email_body: str) -> tuple[str, str]:
    """Extract (uid, token) from a verification email body."""
    match = VERIFY_URL_RE.search(email_body)
    assert match, "verification link not found in email body"
    return match.group(1), match.group(2)


def extract_reset_uid_token(email_body: str) -> tuple[str, str]:
    """Extract (uid, token) from a password-reset email body."""
    match = RESET_URL_RE.search(email_body)
    assert match, "password-reset link not found in email body"
    return match.group(1), match.group(2)


@override_settings(MAILERS=LOCMEM_MAILERS)
class AccountTestCase(TestCase):
    """Base case: isolate the email outbox and rate-limit cache per test."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        mail.outbox.clear()
