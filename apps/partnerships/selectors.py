"""Read-side query layer for partnerships (selector pattern).

Centralizes and optimizes the queries the partnership pages and views need.
Queries are written to avoid N+1 where it matters (``select_related`` /
``prefetch_related``) so the dashboard and invitation list do not beat the
database with one query per row.
"""

from __future__ import annotations

from django.db.models import Prefetch, Q

from apps.accounts.models import User

from .enums import InvitationStatus, PartnershipMemberStatus, PartnershipStatus
from .models import Partnership, PartnershipInvitation, PartnershipMember


def get_active_partnership(user: User) -> Partnership | None:
    """Return the user's currently active partnership, if any."""
    membership = (
        PartnershipMember.objects.select_related("partnership")
        .filter(user=user, status=PartnershipMemberStatus.ACTIVE)
        .first()
    )
    return membership.partnership if membership else None


def get_pending_partnership(user: User) -> Partnership | None:
    """Return the user's pending partnership (where they are the active owner)."""
    membership = (
        PartnershipMember.objects.select_related("partnership")
        .filter(user=user, status=PartnershipMemberStatus.ACTIVE)
        .filter(partnership__status=PartnershipStatus.PENDING)
        .first()
    )
    return membership.partnership if membership else None


def get_partner(user: User, partnership: Partnership) -> User | None:
    """Return the other (non-``user``) member of ``partnership``.

    If the partnership is still pending and only the owner is a member, this
    returns the owner's counterpart as resolved from the (possibly not-yet-user
    account) pending invitee email -> ``None`` until accepted.
    """
    other = (
        PartnershipMember.objects.filter(
            partnership=partnership,
            status=PartnershipMemberStatus.ACTIVE,
        )
        .exclude(user=user)
        .select_related("user")
        .first()
    )
    return other.user if other else None


def partnership_with_members(partnership: Partnership) -> Partnership:
    """Return a partnership (or pk) preloaded with active members + profiles."""
    return (
        Partnership.objects.filter(pk=partnership.pk)
        .prefetch_related(
            Prefetch(
                "members",
                queryset=PartnershipMember.objects.filter(
                    status=PartnershipMemberStatus.ACTIVE
                ).select_related("user__profile"),
            )
        )
        .first()
        or partnership
    )


def list_sent_invitations(user: User) -> list[PartnershipInvitation]:
    """Invitations this user has issued, newest first, with partners attached."""
    return list(
        PartnershipInvitation.objects.filter(inviter=user)
        .select_related("partnership", "invitee", "invitee__profile")
        .order_by("-created_at")
    )


def list_received_invitations(user: User) -> list[PartnershipInvitation]:
    """Invitations addressed to this user (by resolved account or email).

    Excludes invitations from their own active partnership (if any).
    """
    active_partnership = get_active_partnership(user)
    qs = PartnershipInvitation.objects.select_related(
        "partnership", "inviter", "inviter__profile"
    ).filter(
        Q(invitee=user) | Q(invitee_email__iexact=user.email),
        status=InvitationStatus.SENT,
    )
    if active_partnership is not None:
        qs = qs.exclude(partnership=active_partnership)
    return list(qs.order_by("-created_at"))


def active_invitation_for_inviter(user: User) -> PartnershipInvitation | None:
    """Return the inviter's single active (SENT) invitation, if any."""
    return (
        PartnershipInvitation.objects.filter(inviter=user, status=InvitationStatus.SENT)
        .select_related("partnership")
        .first()
    )


def count_active_members(partnership: Partnership) -> int:
    return PartnershipMember.objects.filter(
        partnership=partnership, status=PartnershipMemberStatus.ACTIVE
    ).count()
