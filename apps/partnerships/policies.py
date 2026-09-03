"""Domain authorization policies for partnerships.

Every partnership operation must verify three things **together**:

    Authenticated user
        + authorized relationship
        + allowed action

`@login_required` alone only establishes *authentication*, not authorization.
These predicates centralize object-level checks so controllers, views, forms,
future REST/WebSocket surfaces and tests all rely on one implementation. They
deliberately do NOT raise; callers decide how to present a denial (403 page,
HTMX fragment, or a JSON error) depending on the transport.
"""

from __future__ import annotations

from collections.abc import Iterable

from apps.accounts.models import User

from .enums import PartnershipMemberStatus
from .models import Partnership, PartnershipInvitation, PartnershipMember

_ANONYMOUS_LABEL = "A Candle user"


def safe_display_name(user: User | None) -> str:
    """Return a display-safe partner name that never leaks the email address.

    Used on public-facing surfaces (invitation landing) where we intentionally
    avoid exposing the inviter's email even if they have no display name set.
    """
    if user is None or not user.is_authenticated:
        return _ANONYMOUS_LABEL
    profile = getattr(user, "profile", None)
    name = (profile.display_name if profile else "") or user.first_name or ""
    return name.strip() if name.strip() else _ANONYMOUS_LABEL


def _memberships(user: User) -> Iterable[PartnershipMember]:
    return user.memberships.all() if user.is_authenticated else PartnershipMember.objects.none()


def is_member(user: User, partnership: Partnership) -> bool:
    """Return whether ``user`` is any (historical or current) member."""
    if not user.is_authenticated:
        return False
    return PartnershipMember.objects.filter(partnership=partnership, user=user).exists()


def is_active_member(user: User, partnership: Partnership) -> bool:
    """Return whether ``user`` currently holds an ACTIVE membership."""
    if not user.is_authenticated:
        return False
    return PartnershipMember.objects.filter(
        partnership=partnership,
        user=user,
        status=PartnershipMemberStatus.ACTIVE,
    ).exists()


def can_view_partnership(user: User, partnership: Partnership) -> bool:
    """A member may view a partnership (and its shared resources)."""
    return is_active_member(user, partnership)


def can_manage_partnership(user: User, partnership: Partnership) -> bool:
    """An active member may manage the partnership (end it, etc.).

    Both partners have equal management permissions on an active partnership;
    ``owner`` vs ``member`` is purely historical/UX.
    """
    return is_active_member(user, partnership)


def can_access_whiteboard(user: User, partnership: Partnership) -> bool:
    """Future shared-resource authorization point (Phase 3+).

    A whiteboard will be owned by a *partnership*, not a user. Access requires
    an active membership. Kept here so consumers/views/WebSockets reuse exactly
    this rule rather than scattering checks.
    """
    return is_active_member(user, partnership)


def can_issue_invitation(user: User) -> bool:
    """A user with an active partnership cannot invite; the boundary is full."""
    return not (
        user.is_authenticated
        and PartnershipMember.objects.filter(
            user=user, status=PartnershipMemberStatus.ACTIVE
        ).exists()
    )


# --- Invitation-specific policies ------------------------------------------- #


def can_view_invitation(user: User, invitation: PartnershipInvitation) -> bool:
    """A user may view an invitation if they are the inviter, the invitee, or a
    member of the underlying partnership."""
    if not user.is_authenticated:
        return False
    if invitation.inviter_id == user.pk:
        return True
    if invitation.invitee_id == user.pk:
        return True
    return is_member(user, invitation.partnership)


def can_manage_invitation(user: User, invitation: PartnershipInvitation) -> bool:
    """Only the inviter may revoke/resend an invitation."""
    return user.is_authenticated and invitation.inviter_id == user.pk


def is_invitation_for(user: User, invitation: PartnershipInvitation) -> bool:
    """Return whether ``user`` is the intended recipient of ``invitation``.

    The recipient resolves from the invitee email. Always compare case-folded
    and normalized addresses so the check is unambiguous.
    """
    if not user.is_authenticated:
        return False
    if invitation.invitee_id == user.pk:
        return True
    email = (invitation.invitee_email or "").lower()
    return user.email.lower() == email


def can_accept_invitation(user: User, invitation: PartnershipInvitation) -> bool:
    """Server-side gate for accepting; status/expiry validated separately in the
    service layer (which is the authoritative check).
    """
    if not user.is_authenticated:
        return False
    return is_invitation_for(user, invitation)
