"""Domain services for partnerships and invitations.

Business operations live here so they are independent of transport: the same
``InvitationService`` / ``PartnershipService`` calls are usable from Django
template views, HTMX fragments, a future REST API, WebSocket consumers, or a
mobile client. Views only translate HTTP into service calls and HTTP back out.

Invariants enforced in the service layer (with database-level backstops):

* one active partnership per user (partial unique index on membership status)
* at most two active members per partnership (PostgreSQL trigger)
* one SENT invitation per (inviter, email) and per (partnership, email)
* invitations are single-use, time-limited, revocable, and token-authenticated
* mutual invitations never create two partnerships (detected at creation time)
"""

from __future__ import annotations

import logging
import zlib
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.emailing import send_html_email

from .enums import (
    InvitationStatus,
    PartnershipEventType,
    PartnershipMemberStatus,
    PartnershipRole,
    PartnershipStatus,
)
from .models import (
    Notification,
    Partnership,
    PartnershipEvent,
    PartnershipInvitation,
    PartnershipMember,
)
from .tokens import digest, generate_raw_token

logger = logging.getLogger("apps")

# Invitation validity window, overridable via settings. Default 7 days.
INVITATION_LIFETIME_HOURS = getattr(settings, "PARTNERSHIP_INVITATION_EXPIRY_HOURS", 7 * 24)


def _absolute(path: str) -> str:
    """Prefix a site-relative URL with the configured public origin."""
    return settings.SITE_URL.rstrip("/") + path


def _invitation_accept_url(raw_token: str) -> str:
    return _absolute(reverse("partnerships:invite_accept", args=[raw_token]))


def _record_event(
    partnership: Partnership,
    event_type: str,
    actor: User | None,
    data: dict[str, object] | None = None,
) -> None:
    PartnershipEvent.objects.create(
        partner=partnership,
        actor=actor,
        event_type=event_type,
        data=data or {},
    )


def _notify(
    recipient: User,
    event_type: str,
    text: str,
    link: str = "",
) -> None:
    Notification.objects.create(
        recipient=recipient,
        event_type=event_type,
        text=text,
        link=link,
    )


def _send_invitation_email(
    to_email: str,
    inviter_name: str,
    accept_url: str,
    expires_hours: int,
) -> None:
    send_html_email(
        subject="You're invited to connect on Candle",
        to_email=to_email,
        template_base="invitation_received",
        context={
            "inviter_name": inviter_name,
            "accept_url": accept_url,
            "expires_hours": expires_hours,
        },
    )


def _send_invitation_status_email(
    to_email: str,
    subject: str,
    template_base: str,
    context: dict[str, object],
) -> None:
    send_html_email(
        subject=subject,
        to_email=to_email,
        template_base=template_base,
        context=context,
    )


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class PartnershipError(Exception):
    """Base class for all partnership domain errors."""

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


class AlreadyConnectedError(PartnershipError):
    """The user already has an active partnership."""


class PendingAlreadyExistsError(PartnershipError):
    """The user already has a pending invitation/partnership."""


class MutualInvitationExistsError(PartnershipError):
    """The desired partner has already invited this user."""


class CannotInviteSelfError(PartnershipError):
    """The inviter cannot invite themselves."""


class InvitationInvalidError(PartnershipError):
    """The invitation token is invalid or the invitation is not acceptable."""


class InvitationUsageError(PartnershipError):
    """The invitation is expired/used/revoked and cannot be acted upon."""


class CapacityError(PartnershipError):
    """The partnership is full or the user cannot hold another membership."""


class NotAuthorizedError(PartnershipError):
    """The actor is not authorized to perform this operation."""


# --------------------------------------------------------------------------- #
# Invitation creation
# --------------------------------------------------------------------------- #
def _pair_lock_keys(a: str, b: str) -> tuple[int, int]:
    """Two stable int4 advisory-lock keys for an unordered pair of emails.

    The pair is normalized and sorted so that A→B and B→A map to the *same* key,
    which is what lets a `pg_advisory_xact_lock` serialize mutual invites.
    """
    left, right = sorted((a.lower().strip(), b.lower().strip()))
    blob = f"{left}\0{right}".encode()
    # Mask to 31 bits so the value always fits PostgreSQL's signed int4 range
    # (an advisory-lock key collision only ever means unnecessary serialization,
    # never a correctness problem).
    return zlib.crc32(blob) & 0x7FFFFFFF, zlib.crc32(blob[::-1]) & 0x7FFFFFFF


@transaction.atomic
def create_invitation(inviter: User, invitee_email: str) -> PartnershipInvitation:
    """Create (or resend, idempotently) an invitation from ``inviter``.

    Handles, deterministically:

    * self-invitation -> rejected
    * inviter already connected -> rejected
    * inviter already has a pending partnership -> rejected (unless same email,
      in which case the existing invitation is *resent*)
    * the matching reverse invitation already exists (mutual) -> rejected and the
      inviting user is directed to accept the incoming invitation instead

    The invitation ties to a fresh PENDING partnership where the inviter is the
    active OWNER member. Returns the (possibly reused) SENT invitation.
    """
    email = invitee_email.strip().lower()
    if not email:
        raise ValidationError("An email address is required.")

    if inviter.email.lower() == email:
        raise CannotInviteSelfError(
            "You cannot invite yourself.", user_message="You can't invite yourself."
        )

    # Serialize invitation creation between the same unordered pair of people so
    # that two *mutual* invitations exchanged at the same instant cannot both
    # pass the "reverse invitation exists" check and create two partnerships.
    # A transaction-scoped advisory lock keyed on the sorted email pair is
    # released automatically when this atomic transaction commits or rolls back.
    key1, key2 = _pair_lock_keys(inviter.email, email)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s::int, %s::int)", (key1, key2))

    if _has_connected_partnership(inviter):
        raise AlreadyConnectedError(
            "Inviter already has an active partnership.",
            user_message="You already have a partner.",
        )

    # Mutual-invitation detection: if this person has already invited us, do NOT
    # create a second partnership; guide the user to accept the incoming one.
    other_user = User.objects.filter(email__iexact=email).first()
    if other_user is not None:
        reverse_pending = PartnershipInvitation.objects.filter(
            inviter=other_user,
            invitee_email__iexact=inviter.email,
            status=InvitationStatus.SENT,
        ).exists()
        if reverse_pending:
            raise MutualInvitationExistsError(
                "The recipient has already invited you.",
                user_message=(
                    "This person has already invited you. Accept their "
                    "invitation instead — no need to send another."
                ),
            )

    existing = (
        PartnershipInvitation.objects.filter(
            inviter=inviter,
            invitee_email__iexact=email,
            status=InvitationStatus.SENT,
        )
        .select_for_update()
        .first()
    )

    if existing is not None:
        # Same recipient, still pending: idempotently resend.
        return resend_invitation(existing, inviter)

    if _has_pending_owned_partnership(inviter):
        raise PendingAlreadyExistsError(
            "Inviter already has a pending invitation.",
            user_message=(
                "You already have a pending invitation. Cancel it before inviting someone else."
            ),
        )

    partnership = Partnership.objects.create(
        status=PartnershipStatus.PENDING,
    )
    PartnershipMember.objects.create(
        partnership=partnership,
        user=inviter,
        role=PartnershipRole.OWNER,
        status=PartnershipMemberStatus.ACTIVE,
        joined_at=timezone.now(),
    )

    raw_token = generate_raw_token()
    invitation = PartnershipInvitation.objects.create(
        partnership=partnership,
        inviter=inviter,
        invitee_email=email,
        invitee=other_user,
        token_hash=digest(raw_token),
        status=InvitationStatus.SENT,
        expires_at=timezone.now() + timedelta(hours=INVITATION_LIFETIME_HOURS),
    )

    _record_event(
        partnership,
        PartnershipEventType.INVITATION_CREATED,
        inviter,
        {"invitation_id": invitation.pk, "email": email},
    )
    _notify(
        inviter,
        "invitation_sent",
        f"Invitation sent to {email}.",
        reverse("partnerships:partnership_home"),
    )

    _send_invitation_email(
        to_email=email,
        inviter_name=inviter.profile.get_display_name(),
        accept_url=_invitation_accept_url(raw_token),
        expires_hours=INVITATION_LIFETIME_HOURS,
    )
    return invitation


def _has_connected_partnership(user: User) -> bool:
    """A fully ACTIVE partnership (both partners connected)."""
    return PartnershipMember.objects.filter(
        user=user,
        status=PartnershipMemberStatus.ACTIVE,
        partnership__status=PartnershipStatus.ACTIVE,
    ).exists()


def _has_any_active_membership(user: User) -> bool:
    """Any ACTIVE membership row (whether the partnership is pending or active).

    Used for the accept gate: a user may hold at most one active membership row
    (enforced by the ``uniq_one_active_membership_per_user`` partial unique
    index), so accepting a second one is impossible and must be surfaced cleanly.
    """
    return PartnershipMember.objects.filter(
        user=user, status=PartnershipMemberStatus.ACTIVE
    ).exists()


def _has_pending_owned_partnership(user: User) -> bool:
    return PartnershipMember.objects.filter(
        user=user,
        status=PartnershipMemberStatus.ACTIVE,
        partnership__status=PartnershipStatus.PENDING,
    ).exists()


# --------------------------------------------------------------------------- #
# Resend / revoke
# --------------------------------------------------------------------------- #
@transaction.atomic
def resend_invitation(invitation: PartnershipInvitation, actor: User) -> PartnershipInvitation:
    """Resend a SENT/EXPIRED invitation owned by ``actor``.

    Generates a fresh token and extends the expiry. Idempotent: calling it on an
    already-pending invitation just reissues the link and resends the email.
    """
    if invitation.inviter_id != actor.pk:
        raise NotAuthorizedError(
            "Only the inviter can resend this invitation.",
            user_message="You can't resend this invitation.",
        )
    if invitation.status not in (InvitationStatus.SENT, InvitationStatus.EXPIRED):
        raise InvitationUsageError(
            "Only pending or expired invitations can be resent.",
            user_message="This invitation can't be resent.",
        )
    # An inviter who is fully connected can no longer keep issuing invitations.
    if _has_connected_partnership(actor):
        raise AlreadyConnectedError(
            "Inviter is already connected.", user_message="You already have a partner."
        )

    raw_token = generate_raw_token()
    invitation.token_hash = digest(raw_token)
    invitation.expires_at = timezone.now() + timedelta(hours=INVITATION_LIFETIME_HOURS)
    invitation.revoked_at = None
    invitation.rejected_at = None
    invitation.accepted_at = None
    invitation.status = InvitationStatus.SENT
    invitation.save(
        update_fields=[
            "token_hash",
            "expires_at",
            "revoked_at",
            "rejected_at",
            "accepted_at",
            "status",
            "updated_at",
        ]
    )

    _record_event(
        invitation.partnership,
        PartnershipEventType.INVITATION_RESENT,
        actor,
        {"invitation_id": invitation.pk},
    )
    _send_invitation_email(
        to_email=invitation.invitee_email,
        inviter_name=actor.profile.get_display_name(),
        accept_url=_invitation_accept_url(raw_token),
        expires_hours=INVITATION_LIFETIME_HOURS,
    )
    return invitation


@transaction.atomic
def revoke_invitation(invitation: PartnershipInvitation, actor: User) -> None:
    """Revoke a pending invitation and cancel its pending partnership."""
    if invitation.inviter_id != actor.pk:
        raise NotAuthorizedError(
            "Only the inviter can revoke this invitation.",
            user_message="You can't revoke this invitation.",
        )
    if invitation.status != InvitationStatus.SENT:
        raise InvitationUsageError(
            "Only a pending invitation can be revoked.",
            user_message="This invitation can't be revoked anymore.",
        )

    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["status", "revoked_at", "updated_at"])

    partnership = Partnership.objects.select_for_update().get(pk=invitation.partnership_id)
    if partnership.status == PartnershipStatus.PENDING:
        partnership.status = PartnershipStatus.CANCELLED
        partnership.save(update_fields=["status", "updated_at"])
        # The owner's active membership for this cancelled partnership ends.
        PartnershipMember.objects.filter(
            partnership=partnership, status=PartnershipMemberStatus.ACTIVE
        ).update(status=PartnershipMemberStatus.LEFT, left_at=timezone.now())
        _record_event(
            partnership,
            PartnershipEventType.PARTNERSHIP_CANCELLED,
            actor,
            {"invitation_id": invitation.pk},
        )

    _record_event(
        partnership,
        PartnershipEventType.INVITATION_REVOKED,
        actor,
        {"invitation_id": invitation.pk},
    )


# --------------------------------------------------------------------------- #
# Accept / reject
# --------------------------------------------------------------------------- #
@transaction.atomic
def _mark_expired_invitation(invitation_id: int) -> None:
    """Idempotently persist the terminal ``EXPIRED`` state for a stale invite.

    Runs in its own committed atomic called *before* the accepting transaction,
    so a later ``InvitationUsageError`` cannot roll the marker back. Stopping
    the invite works purely from ``is_expired`` anyway; this just makes the
    terminal state explicit and auditable.
    """
    inv = (
        PartnershipInvitation.objects.select_for_update()
        .filter(pk=invitation_id, status=InvitationStatus.SENT)
        .first()
    )
    if inv is not None and inv.is_expired:
        inv.status = InvitationStatus.EXPIRED
        inv.save(update_fields=["status", "updated_at"])
        _record_event(
            inv.partnership,
            PartnershipEventType.INVITATION_EXPIRED,
            None,
            {"invitation_id": inv.pk},
        )


def accept_invitation(invitation: PartnershipInvitation, user: User) -> Partnership:
    """Accept an invitation and activate the partnership.

    Concurrency-safe: the accepting mutation is wrapped in a transaction that
    locks the invitation (and partnership) row, revalidates, and activates the
    partnership. Any failure rolls back that inner block; the terminal expires
    are committed independently beforehand.
    """
    # Persist a stale invitation's EXPIRED state in its own committed atomic so
    # that a raise below (as a result) cannot undo it.
    _mark_expired_invitation(invitation.pk)

    with transaction.atomic():
        locked = (
            PartnershipInvitation.objects.select_for_update()
            .select_related("partnership")
            .get(pk=invitation.pk)
        )
        if locked.status == InvitationStatus.EXPIRED:
            raise InvitationUsageError(
                "Invitation has expired.",
                user_message="This invitation has expired.",
            )
        if locked.status != InvitationStatus.SENT:
            raise InvitationUsageError(
                "Invitation is no longer pending.",
                user_message="This invitation has already been used or is no longer active.",
            )
        if not _is_invitee(locked, user):
            raise NotAuthorizedError(
                "This invitation is not addressed to the current user.",
                user_message="This invitation is not addressed to you.",
            )

    with transaction.atomic():
        try:
            partnership = Partnership.objects.select_for_update().get(pk=locked.partnership_id)
            if partnership.status != PartnershipStatus.PENDING:
                raise InvitationUsageError(
                    "Partnership is no longer pending.",
                    user_message="This partnership is no longer available.",
                )

            if _has_any_active_membership(user):
                raise CapacityError(
                    "User already has an active partnership.",
                    user_message="You already have a partner.",
                )

            active_members = PartnershipMember.objects.filter(
                partnership=partnership,
                status=PartnershipMemberStatus.ACTIVE,
            ).count()
            if active_members >= 2:
                raise CapacityError(
                    "Partnership already has two active members.",
                    user_message="This partnership is full.",
                )

            PartnershipMember.objects.create(
                partnership=partnership,
                user=user,
                role=PartnershipRole.MEMBER,
                status=PartnershipMemberStatus.ACTIVE,
                joined_at=timezone.now(),
            )
            partnership.status = PartnershipStatus.ACTIVE
            partnership.accepted_at = timezone.now()
            partnership.save(update_fields=["status", "accepted_at", "updated_at"])

            locked.status = InvitationStatus.ACCEPTED
            locked.accepted_at = timezone.now()
            locked.save(update_fields=["status", "accepted_at", "updated_at"])
        except (OperationalError, IntegrityError) as exc:
            # A lost race against another concurrent accept surfaces either as a
            # constraint violation (IntegrityError) or, when both requests lock
            # overlapping rows, as a PostgreSQL deadlock / serialization failure
            # (OperationalError). Both mean "another accept won" — surface it as
            # a clean conflict the UI can present.
            raise CapacityError(
                "Membership integrity conflict.",
                user_message="You are already connected to someone else.",
            ) from exc

    _post_accept_housekeeping(partnership, locked, user)
    return partnership


def _is_invitee(invitation: PartnershipInvitation, user: User) -> bool:
    if invitation.invitee_id == user.pk:
        return True
    return invitation.invitee_email.lower() == user.email.lower()


def _post_accept_housekeeping(
    partnership: Partnership,
    invitation: PartnershipInvitation,
    user: User,
) -> None:
    _record_event(
        partnership,
        PartnershipEventType.INVITATION_ACCEPTED,
        user,
        {"invitation_id": invitation.pk},
    )
    _record_event(
        partnership,
        PartnershipEventType.PARTNERSHIP_ACTIVATED,
        user,
        {},
    )

    partner_user = (
        PartnershipMember.objects.filter(
            partnership=partnership, status=PartnershipMemberStatus.ACTIVE
        )
        .exclude(user=user)
        .select_related("user")
        .first()
    )
    owner = partner_user.user if partner_user else invitation.inviter

    _notify(
        user,
        "partnership_active",
        "You're now connected!",
        reverse("partnerships:partnership_home"),
    )
    _notify(
        owner,
        "invitation_accepted",
        f"{user.profile.get_display_name()} accepted your invitation.",
        reverse("partnerships:partnership_home"),
    )
    _send_invitation_status_email(
        to_email=owner.email,
        subject="Your invitation was accepted",
        template_base="invitation_accepted",
        context={
            "partner_name": user.profile.get_display_name(),
            "partnership_url": _absolute(reverse("partnerships:partnership_home")),
        },
    )


@transaction.atomic
def reject_invitation(invitation: PartnershipInvitation, user: User) -> None:
    """Reject a pending invitation addressed to ``user``."""
    locked = (
        PartnershipInvitation.objects.select_for_update()
        .select_related("partnership")
        .get(pk=invitation.pk)
    )

    if locked.status != InvitationStatus.SENT:
        raise InvitationUsageError(
            "Invitation is no longer pending.",
            user_message="This invitation has already been handled.",
        )
    if not _is_invitee(locked, user):
        raise NotAuthorizedError(
            "This invitation is not addressed to the current user.",
            user_message="This invitation is not addressed to you.",
        )

    locked.status = InvitationStatus.REJECTED
    locked.rejected_at = timezone.now()
    locked.save(update_fields=["status", "rejected_at", "updated_at"])

    partnership = Partnership.objects.select_for_update().get(pk=locked.partnership_id)
    if partnership.status == PartnershipStatus.PENDING:
        partnership.status = PartnershipStatus.CANCELLED
        partnership.save(update_fields=["status", "updated_at"])
        PartnershipMember.objects.filter(
            partnership=partnership, status=PartnershipMemberStatus.ACTIVE
        ).update(status=PartnershipMemberStatus.LEFT, left_at=timezone.now())
        _record_event(
            partnership,
            PartnershipEventType.PARTNERSHIP_CANCELLED,
            user,
            {"invitation_id": locked.pk},
        )

    _record_event(
        partnership,
        PartnershipEventType.INVITATION_REJECTED,
        user,
        {"invitation_id": locked.pk},
    )
    _notify(
        locked.inviter,
        "invitation_rejected",
        f"{user.profile.get_display_name()} declined your invitation.",
        reverse("partnerships:partnership_home"),
    )
    _send_invitation_status_email(
        to_email=locked.inviter.email,
        subject="Your invitation was declined",
        template_base="invitation_rejected",
        context={
            "partner_name": user.profile.get_display_name(),
        },
    )


# --------------------------------------------------------------------------- #
# Ending a partnership
# --------------------------------------------------------------------------- #
@transaction.atomic
def end_partnership(partnership: Partnership, actor: User) -> None:
    """End an ACTIVE partnership, preserving all historical records.

    Memberships become LEFT (with ``left_at``), the partnership becomes ENDED,
    and both partners are notified. No records are deleted.
    """
    locked = Partnership.objects.select_for_update().get(pk=partnership.pk)
    if locked.status != PartnershipStatus.ACTIVE:
        raise PartnershipError(
            "Only an active partnership can be ended.",
            user_message="This partnership can't be ended.",
        )
    if not PartnershipMember.objects.filter(
        partnership=locked, user=actor, status=PartnershipMemberStatus.ACTIVE
    ).exists():
        raise NotAuthorizedError(
            "Only an active member can end the partnership.",
            user_message="You can't end this partnership.",
        )

    locked.status = PartnershipStatus.ENDED
    locked.ended_at = timezone.now()
    locked.save(update_fields=["status", "ended_at", "updated_at"])

    members = list(
        PartnershipMember.objects.filter(
            partnership=locked, status=PartnershipMemberStatus.ACTIVE
        ).select_related("user")
    )
    PartnershipMember.objects.filter(
        partnership=locked, status=PartnershipMemberStatus.ACTIVE
    ).update(status=PartnershipMemberStatus.LEFT, left_at=timezone.now())

    _record_event(locked, PartnershipEventType.PARTNERSHIP_ENDED, actor, {})

    for member in members:
        _record_event(
            locked,
            PartnershipEventType.MEMBER_LEFT,
            member.user,
            {"member_id": member.pk},
        )
        _notify(
            member.user,
            "partnership_ended",
            "Your partnership has ended.",
            reverse("partnerships:partnership_home"),
        )
        if member.user_id != actor.pk:
            _send_invitation_status_email(
                to_email=member.user.email,
                subject="Your partnership has ended",
                template_base="partnership_ended",
                context={
                    "partnership_url": _absolute(reverse("partnerships:partnership_home")),
                },
            )
