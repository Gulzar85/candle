"""Partnership, membership and invitation models.

A ``Partnership`` is a **security boundary**: it groups exactly two users (the
inviter/owner and the joined member) and every future shared resource will be
authorizable through it. No partnership information is stored on ``User``.

Concurrency / integrity are enforced at the database level (see the migrations
that install a PL/pgSQL trigger on PostgreSQL, and an equivalent set of native
triggers on SQLite):

* a partnership can hold **at most two ACTIVE members** (cannot be bypassed by
  racing requests);
* a user can have **at most one ACTIVE membership** (one active partnership per
  person).

Application code additionally uses ``select_for_update()`` + ``atomic()`` so
accept flows serialise and give deterministic, user-friendly errors. SQLite
has no row-level locking, but its writer transactions are already serialized
whole-database (see ``OPTIONS["transaction_mode"]`` in settings), which gives
the same effective guarantee for a single-process deployment.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User

from .enums import (
    InvitationStatus,
    PartnershipEventType,
    PartnershipMemberStatus,
    PartnershipRole,
    PartnershipStatus,
)

# How long an invitation stays valid; overridable via
# settings.PARTNERSHIP_INVITATION_EXPIRY_HOURS (default 7 days).
INVITATION_LIFETIME_HOURS = 7 * 24


def hash_token(raw_token: str) -> str:
    """Return a non-reversible SHA-256 digest of a raw invitation token."""
    if not raw_token:
        raise ValueError("raw_token must not be empty")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class Partnership(models.Model):
    """A bounded relationship between two people."""

    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=PartnershipStatus.choices,
        default=PartnershipStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "partnership"
        verbose_name_plural = "partnerships"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Partnership {self.public_id} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == PartnershipStatus.ACTIVE

    @property
    def is_pending(self) -> bool:
        return self.status == PartnershipStatus.PENDING


class PartnershipMember(models.Model):
    """A user's membership (and role/history) within a partnership."""

    id = models.BigAutoField(primary_key=True)
    partnership = models.ForeignKey(
        Partnership,
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=16, choices=PartnershipRole.choices)
    status = models.CharField(
        max_length=16,
        choices=PartnershipMemberStatus.choices,
        default=PartnershipMemberStatus.ACTIVE,
        db_index=True,
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "partnership member"
        verbose_name_plural = "partnership members"
        constraints = [
            # A user can appear at most once per partnership (history is kept
            # on this same row via status, e.g. ACTIVE -> LEFT).
            models.UniqueConstraint(
                fields=["partnership", "user"], name="uniq_member_per_partnership"
            ),
            # A user can have at most ONE ACTIVE membership across all
            # partnerships (one active partnership per person). This partial
            # unique index makes the "one active partnership per user" rule
            # database-enforced rather than only checked in application code,
            # closing a race window for concurrent accepts.
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status=PartnershipMemberStatus.ACTIVE),
                name="uniq_one_active_membership_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["partnership", "status"], name="idx_member_part_status"),
            models.Index(fields=["user", "status"], name="idx_member_user_status"),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.partnership_id} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == PartnershipMemberStatus.ACTIVE


class PartnershipInvitation(models.Model):
    """A secure, single-use, time-limited invitation to join a partnership.

    Only a SHA-256 digest of the raw token is stored here; the raw token
    travels only inside the emailed accept link. Invitation possession is
    proved by the token; the server decides what that token authorizes.
    """

    id = models.BigAutoField(primary_key=True)
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    partnership = models.ForeignKey(
        Partnership, on_delete=models.CASCADE, related_name="invitations"
    )
    inviter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_invitations")
    # The invited email. If the person is not yet a user, they may register and
    # verify, after which ``invitee`` resolves and they can accept.
    invitee_email = models.EmailField(db_index=True)
    invitee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_invitations",
    )
    token_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=InvitationStatus.choices,
        default=InvitationStatus.SENT,
        db_index=True,
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # To keep the condition expressions readable they are repeated inline in
    # the Meta constraints below (a bare Q attribute is not preserved by
    # Django's metaclass, so we do not store it as a class attribute).
    class Meta:
        verbose_name = "partnership invitation"
        verbose_name_plural = "partnership invitations"
        constraints = [
            # At most one live (SENT) invitation per (inviter, email): protects
            # against double-click / duplicate send.
            models.UniqueConstraint(
                fields=["inviter", "invitee_email"],
                condition=Q(status=InvitationStatus.SENT),
                name="uniq_app_one_active_sent_inviter_email",
            ),
            # At most one live invitation per partnership -> invitee.
            models.UniqueConstraint(
                fields=["partnership", "invitee_email"],
                condition=Q(status=InvitationStatus.SENT),
                name="uniq_app_one_active_partnership_email",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="idx_inv_status_expiry"),
            models.Index(fields=["invitee", "status"], name="idx_inv_invitee_status"),
        ]

    def __str__(self) -> str:
        return f"Invitation {self.inviter_id} -> {self.invitee_email} ({self.status})"

    @property
    def is_sent(self) -> bool:
        return self.status == InvitationStatus.SENT

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def clean(self) -> None:
        if self.expires_at is not None and self.expires_at <= timezone.now():
            raise ValidationError("expires_at must be in the future")


class PartnershipEvent(models.Model):
    """Immutable audit/notification ledger of partnership domain events.

    No tokens or passwords are ever stored here. Serves traceability (audit)
    and, together with email delivery in the service layer, the notification
    foundation future features can build on.
    """

    partner = models.ForeignKey(
        Partnership,
        on_delete=models.CASCADE,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    event_type = models.CharField(
        max_length=32, choices=PartnershipEventType.choices, db_index=True
    )
    # Fixed, stable JSON of context (e.g. {"invitation_id": 3, "email": "a@b"})
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "partnership event"
        verbose_name_plural = "partnership events"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} on {self.partner_id}"


class Notification(models.Model):
    """Lightweight in-app notification foundation.

    Designed to be reusable by future features (whiteboard activity, etc.).
    Email delivery is separate (see apps.core.emailing) so a notification can
    have an email, an in-app record, or both, without coupling.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event_type = models.CharField(max_length=32, db_index=True)
    text = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"], name="idx_notif_recip_read")]

    def __str__(self) -> str:
        return f"{self.event_type} for {self.recipient_id}"

    def mark_read(self) -> None:
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])


def generate_invitation_token() -> str:
    """Return a cryptographically secure, unguessable raw invitation token."""
    return secrets.token_urlsafe(32)
