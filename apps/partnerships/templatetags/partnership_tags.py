"""Template tags for partnership views."""

from __future__ import annotations

from datetime import datetime

from django import template
from django.utils import timezone

from apps.partnerships.enums import InvitationStatus

register = template.Library()


@register.filter
def invitation_status_label(invitation: object) -> str:
    """Human-readable status label with expired-awareness."""
    status = getattr(invitation, "status", None)
    if status == InvitationStatus.SENT and getattr(invitation, "is_expired", False):
        return "Expired"
    return _status_display(invitation)


@register.filter
def is_expired(invitation: object) -> bool:
    if not hasattr(invitation, "is_expired"):
        return False
    return bool(invitation.is_expired)


@register.simple_tag
def remaining_time(target: datetime | None) -> str:
    """Render a friendly 'time until' string for a future datetime."""
    if target is None:
        return ""
    diff = target - timezone.now()
    if diff.total_seconds() <= 0:
        return "Expired"
    days = diff.days
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _status_display(invitation: object) -> str:
    method = getattr(invitation, "get_status_display", None)
    if callable(method):
        return str(method())
    return str(getattr(invitation, "status", ""))
