"""Shared transactional email sending.

Uses Django 6.1's ``MAILERS`` handler (``django.core.mail.mailers``) rather
than the deprecated ``EMAIL_BACKEND`` / ``get_connection()`` path.

Delivery is pushed to a short-lived daemon thread so a slow network SMTP
round-trip never blocks the user-facing request. In-process backends (console
and locmem) send synchronously so the development console and the test suite
(mail.outbox) behave deterministically.

Privacy: no token, password or message body is ever logged; only the template
name is recorded alongside failures.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, mailers
from django.template.loader import render_to_string

logger = logging.getLogger("apps")

# Backends that exist purely in-process and are cheap/deterministic to run
# synchronously (development console + test locmem outbox).
_IN_PROCESS_BACKENDS = ("console", "locmem")


def send_html_email(
    *,
    subject: str,
    to_email: str,
    template_base: str,
    context: dict[str, object],
) -> None:
    """Render and send an HTML+plain-text email from ``emails/<template_base>``."""
    text_body = render_to_string(f"emails/{template_base}.txt", context)
    html_body = render_to_string(f"emails/{template_base}.html", context)
    message = EmailMultiAlternatives(subject, text_body, to=[to_email])
    message.attach_alternative(html_body, "text/html")
    _deliver(message, template_base)


def _deliver(message: EmailMultiAlternatives, template_base: str) -> None:
    backend = mailers.settings["default"].get("BACKEND", "")
    in_process = any(token in backend for token in _IN_PROCESS_BACKENDS)
    if in_process or getattr(settings, "DEBUG", False):
        _send(message, template_base)
    else:
        thread = threading.Thread(
            target=_send,
            args=(message, template_base),
            name=f"email-{template_base}",
            daemon=True,
        )
        thread.start()


def _send(message: EmailMultiAlternatives, template_base: str) -> None:
    try:
        # A fresh connection isolates this send from any cached/borrowed
        # request-thread connection and is safe to use off the request thread.
        connection = mailers.create_connection("default")
        connection.send_messages([message])
    except Exception:  # noqa: BLE001 - never let a delivery failure break a request
        logger.exception("Transactional email delivery failed — %s", template_base)
