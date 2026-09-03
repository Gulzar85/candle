"""Send transactional emails for accounts workflows.

Email delivery is configured via Django's MAILERS setting (Phase 0): the
console backend in development and SMTP via environment variables in
production. This module builds branded HTML messages with a plain-text
fallback and makes no assumptions about credentials (none are logged or
hard-coded here).
"""

from __future__ import annotations

from django.conf import settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.emailing import send_html_email

from .models import User


def _absolute(path: str) -> str:
    """Prefix a site-relative URL with the configured public origin."""
    return settings.SITE_URL.rstrip("/") + path


def _send_email(
    *,
    subject: str,
    to_email: str,
    template_base: str,
    context: dict[str, object],
) -> None:
    # Delegates to apps.core.emailing, which uses Django 6.1's MAILERS handler
    # and pushes network delivery to a background thread (see that module).
    send_html_email(
        subject=subject,
        to_email=to_email,
        template_base=template_base,
        context=context,
    )


def build_verify_url(user: User, purpose: str, new_email: str = "") -> str:
    """Build the absolute verification URL (with scheme/host) for ``user``."""
    raw = user.issue_verification_token(purpose, new_email)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    path = reverse(
        "accounts:verify_email",
        kwargs={"uidb64": uid, "token": raw.decode("ascii")},
    )
    return _absolute(path)


def send_verification_email(user: User) -> None:
    """Send the initial signup email-verification link to ``user``."""
    verify_url = build_verify_url(user, purpose="verify_email")
    context: dict[str, object] = {
        "user": user,
        "verify_url": verify_url,
        "expires_hours": 24,
    }
    _send_email(
        subject="Verify your Candle email address",
        to_email=user.email,
        template_base="email_verify",
        context=context,
    )


def send_email_change_confirmation(
    user: User,
    new_email: str,
    purpose: str = "change_email",
) -> None:
    """Request confirmation that the user wants to switch to ``new_email``."""
    confirm_url = build_verify_url(user, purpose, new_email=new_email)
    context: dict[str, object] = {
        "user": user,
        "new_email": new_email,
        "confirm_url": confirm_url,
        "expires_hours": 24,
    }
    _send_email(
        subject="Confirm your new Candle email",
        to_email=new_email,
        template_base="email_change",
        context=context,
    )
