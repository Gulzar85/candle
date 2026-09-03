"""Domain enums for the partnerships app.

Django 6.1 exposes these via ``django.db.models.TextChoices`` (built on
Python's ``StrEnum``). Use the enum member everywhere so a typos is impossible
and database values stay stable/lowercase.
"""

from __future__ import annotations

from django.db import models


class PartnershipStatus(models.TextChoices):
    """Lifecycle of a partnership.

    PENDING: created, waiting on the invited partner to accept.
    ACTIVE:  both partners are connected.
    ENDED:   one partner ended the partnership (historical record preserved).
    CANCELLED: terminated while still PENDING (e.g. the invitation/offer was
               revoked and never accepted).
    """

    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"
    CANCELLED = "cancelled", "Cancelled"


class PartnershipMemberStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    LEFT = "left", "Left"
    REMOVED = "removed", "Removed"


class PartnershipRole(models.TextChoices):
    """Who initiated the relationship (purely historical/UX; both partners
    have equal permissions on the active partnership)."""

    OWNER = "owner", "Owner"
    MEMBER = "member", "Member"


class InvitationStatus(models.TextChoices):
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"


class PartnershipEventType(models.TextChoices):
    """Audit/notification event types (kept stable; append, never rename)."""

    INVITATION_CREATED = "invitation_created", "Invitation created"
    INVITATION_RESENT = "invitation_resent", "Invitation resent"
    INVITATION_REVOKED = "invitation_revoked", "Invitation revoked"
    INVITATION_ACCEPTED = "invitation_accepted", "Invitation accepted"
    INVITATION_REJECTED = "invitation_rejected", "Invitation rejected"
    INVITATION_EXPIRED = "invitation_expired", "Invitation expired"
    INVITATION_INVALID = "invitation_invalid", "Invitation invalid"
    PARTNERSHIP_ACTIVATED = "partnership_activated", "Partnership activated"
    PARTNERSHIP_ENDED = "partnership_ended", "Partnership ended"
    PARTNERSHIP_CANCELLED = "partnership_cancelled", "Partnership cancelled"
    MEMBER_LEFT = "member_left", "Member left"
    MEMBER_REMOVED = "member_removed", "Member removed"
