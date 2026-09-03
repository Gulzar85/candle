"""Partnership views.

Views translate HTTP/HTMX into service-layer calls and back. They never contain
business logic — that lives in ``services`` and ``policies`` so REST/WebSocket
and mobile consumers can reuse it.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.accounts.models import User
from apps.accounts.ratelimit import check as rate_limit_check

from . import policies, selectors, services
from .enums import InvitationStatus
from .forms import ConfirmEndForm, InviteForm
from .models import Partnership, PartnershipInvitation
from .tokens import find_invitation_by_token

logger = logging.getLogger("apps")

RATE_LIMITS = getattr(
    settings,
    "PARTNERSHIP_RATE_LIMITS",
    {
        "invite": {"limit": 5, "window": 3600},
        "invite_resend": {"limit": 5, "window": 3600},
        "invite_accept": {"limit": 10, "window": 900},
        "invite_reject": {"limit": 10, "window": 900},
        "invite_revoke": {"limit": 5, "window": 3600},
    },
)


def _client_key(request: HttpRequest) -> str:
    return str(request.META.get("REMOTE_ADDR") or "unknown")


def _current_user(request: HttpRequest) -> User:
    """Return the authenticated user, narrowing the optional AnonymousUser.

    Safe because every caller sits behind ``@login_required`` (or already
    checked ``is_authenticated``), and it keeps mypy happy about ``User`` args.
    """
    assert isinstance(request.user, User)
    return request.user


def _rate_limited(request: HttpRequest) -> HttpResponse:
    return render(request, "accounts/rate_limited.html", status=429)


def _safe_redirect(request: HttpRequest, fallback: str) -> HttpResponse:
    """Redirect to a safe next URL, else fallback. Never an open redirect."""
    raw_next = request.POST.get("next") or request.GET.get("next")
    next_url: str | None = raw_next if isinstance(raw_next, str) else None
    allowed = bool(
        next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})
    )
    target = next_url or fallback if allowed else fallback
    return redirect(resolve_url(target))


def _invitation_detail_url(invitation: PartnershipInvitation) -> str:
    return resolve_url("partnerships:invitation_detail", public_id=invitation.public_id)


# --------------------------------------------------------------------------- #
# Partnership home / dashboard
# --------------------------------------------------------------------------- #
@login_required
def partnership_home(request: HttpRequest) -> HttpResponse:
    """Home for the partnership area. The single page presents whichever state
    the user is in: no partner (empty state + invite form), pending (invitation
    status), or connected (partner card + shared space entry)."""
    user = _current_user(request)
    active = selectors.get_active_partnership(user)
    pending = None
    invite_form = InviteForm()
    received_invitations = []
    sent_invitations = []

    if active is None:
        pending = selectors.get_pending_partnership(user)
        received_invitations = selectors.list_received_invitations(user)
        sent_invitations = selectors.list_sent_invitations(user)

    return render(
        request,
        "partnerships/home.html",
        {
            "page_title": "Partnership",
            "active": active,
            "pending": pending,
            "partner": selectors.get_partner(user, active) if active else None,
            "invite_form": invite_form,
            "received_invitations": received_invitations,
            "sent_invitations": sent_invitations,
            "invitation_expiry_hours": getattr(
                settings, "PARTNERSHIP_INVITATION_EXPIRY_HOURS", 168
            ),
            "end_form": ConfirmEndForm() if active else None,
        },
    )


@login_required
@require_http_methods(["POST"])
def invite(request: HttpRequest) -> HttpResponse:
    """Create (or resend) an invitation. HTMX-friendly: renders a fragment."""
    result = rate_limit_check(
        "invite", f"{_client_key(request)}:{request.user.pk}", **RATE_LIMITS["invite"]
    )
    if result.blocked:
        return _rate_limited(request)

    form = InviteForm(request.POST)
    if form.is_valid():
        try:
            services.create_invitation(_current_user(request), form.cleaned_data["email"])
            messages.success(request, "Invitation sent.")
            return redirect("partnerships:partnership_home")
        except services.CannotInviteSelfError as exc:
            messages.error(request, str(exc.user_message))
        except services.AlreadyConnectedError as exc:
            messages.error(request, str(exc.user_message))
        except services.PendingAlreadyExistsError as exc:
            messages.error(request, str(exc.user_message))
        except services.MutualInvitationExistsError as exc:
            messages.error(request, str(exc.user_message))
        except Exception:  # noqa: BLE001 - unexpected
            logger.exception("Invitation creation failed for user %s", request.user.pk)
            messages.error(request, "Something went wrong sending the invitation.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, str(error))

    return redirect("partnerships:partnership_home")


# --------------------------------------------------------------------------- #
# Invitation list & detail
# --------------------------------------------------------------------------- #
@login_required
def invitation_list(request: HttpRequest) -> HttpResponse:
    user = _current_user(request)
    active = selectors.get_active_partnership(user)
    if active is not None:
        return redirect("partnerships:partnership_home")
    return render(
        request,
        "partnerships/invitation_list.html",
        {
            "page_title": "Invitations",
            "received": selectors.list_received_invitations(user),
            "sent": selectors.list_sent_invitations(user),
            "invitation_expiry_hours": getattr(
                settings, "PARTNERSHIP_INVITATION_EXPIRY_HOURS", 168
            ),
        },
    )


@login_required
def invitation_detail(request: HttpRequest, public_id: str) -> HttpResponse:
    invitation = get_object_or_404(PartnershipInvitation, public_id=public_id)
    if not policies.can_view_invitation(_current_user(request), invitation):
        return render(request, "errors/403.html", status=403)

    return render(
        request,
        "partnerships/invitation_detail.html",
        {
            "page_title": "Invitation",
            "invitation": invitation,
        },
    )


@login_required
@require_http_methods(["POST"])
def invitation_revoke(request: HttpRequest, public_id: str) -> HttpResponse:
    invitation = get_object_or_404(PartnershipInvitation, public_id=public_id)
    result = rate_limit_check(
        "invite_revoke",
        f"{_client_key(request)}:{request.user.pk}",
        **RATE_LIMITS["invite_revoke"],
    )
    if result.blocked:
        return _rate_limited(request)

    try:
        services.revoke_invitation(invitation, _current_user(request))
        messages.success(request, "Invitation cancelled.")
    except services.NotAuthorizedError:
        return render(request, "errors/403.html", status=403)
    except services.InvitationUsageError as exc:
        messages.error(request, str(exc.user_message))
    except Exception:  # noqa: BLE001
        logger.exception("Invitation revoke failed for user %s", request.user.pk)
        messages.error(request, "Something went wrong.")
    return redirect("partnerships:partnership_home")


@login_required
@require_http_methods(["POST"])
def invitation_resend(request: HttpRequest, public_id: str) -> HttpResponse:
    invitation = get_object_or_404(PartnershipInvitation, public_id=public_id)
    result = rate_limit_check(
        "invite_resend",
        f"{_client_key(request)}:{request.user.pk}",
        **RATE_LIMITS["invite_resend"],
    )
    if result.blocked:
        return _rate_limited(request)

    try:
        services.resend_invitation(invitation, _current_user(request))
        messages.success(request, "Invitation resent.")
    except services.NotAuthorizedError:
        return render(request, "errors/403.html", status=403)
    except (services.InvitationUsageError, services.AlreadyConnectedError) as exc:
        messages.error(request, str(exc.user_message))
    except Exception:  # noqa: BLE001
        logger.exception("Invitation resend failed for user %s", request.user.pk)
        messages.error(request, "Something went wrong.")
    return redirect("partnerships:partnership_home")


@login_required
@require_http_methods(["POST"])
def invitation_accept(request: HttpRequest, public_id: str) -> HttpResponse:
    invitation = get_object_or_404(PartnershipInvitation, public_id=public_id)
    result = rate_limit_check(
        "invite_accept",
        f"{_client_key(request)}:{request.user.pk}",
        **RATE_LIMITS["invite_accept"],
    )
    if result.blocked:
        return _rate_limited(request)

    try:
        services.accept_invitation(invitation, _current_user(request))
        messages.success(request, "You're now connected!")
        return redirect("partnerships:partnership_home")
    except services.NotAuthorizedError:
        return render(request, "errors/403.html", status=403)
    except (
        services.InvitationUsageError,
        services.CapacityError,
    ) as exc:
        messages.error(request, str(exc.user_message))
        return redirect(_invitation_detail_url(invitation))
    except Exception:  # noqa: BLE001
        logger.exception("Invitation accept failed for user %s", request.user.pk)
        messages.error(request, "Something went wrong.")
        return redirect(_invitation_detail_url(invitation))


@login_required
@require_http_methods(["POST"])
def invitation_reject(request: HttpRequest, public_id: str) -> HttpResponse:
    invitation = get_object_or_404(PartnershipInvitation, public_id=public_id)
    result = rate_limit_check(
        "invite_reject",
        f"{_client_key(request)}:{request.user.pk}",
        **RATE_LIMITS["invite_reject"],
    )
    if result.blocked:
        return _rate_limited(request)

    try:
        services.reject_invitation(invitation, _current_user(request))
        messages.success(request, "Invitation declined.")
        return redirect("partnerships:partnership_home")
    except services.NotAuthorizedError:
        return render(request, "errors/403.html", status=403)
    except services.InvitationUsageError as exc:
        messages.error(request, str(exc.user_message))
        return redirect(_invitation_detail_url(invitation))
    except Exception:  # noqa: BLE001
        logger.exception("Invitation reject failed for user %s", request.user.pk)
        messages.error(request, "Something went wrong.")
        return redirect(_invitation_detail_url(invitation))


# --------------------------------------------------------------------------- #
# End partnership
# --------------------------------------------------------------------------- #
@login_required
@require_http_methods(["POST"])
def partnership_end(request: HttpRequest) -> HttpResponse:
    partnership = get_object_or_404(Partnership, public_id=request.POST.get("partnership_id", ""))
    form = ConfirmEndForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            str(form.errors.get("confirm", ["Confirmation failed."])[0]),
        )
        return redirect("partnerships:partnership_home")

    if not policies.can_manage_partnership(_current_user(request), partnership):
        return render(request, "errors/403.html", status=403)

    try:
        services.end_partnership(partnership, _current_user(request))
        messages.success(request, "Your partnership has ended.")
    except services.NotAuthorizedError:
        return render(request, "errors/403.html", status=403)
    except services.PartnershipError as exc:
        messages.error(request, str(exc.user_message))
    except Exception:  # noqa: BLE001
        logger.exception("Partnership end failed for user %s", request.user.pk)
        messages.error(request, "Something went wrong.")
    return redirect("partnerships:partnership_home")


# --------------------------------------------------------------------------- #
# Public invitation landing (token-authenticated)
# --------------------------------------------------------------------------- #
def invite_landing(request: HttpRequest, token: str) -> HttpResponse:
    """Public landing page reached from the emailed link.

    The token proves possession; the server decides what it authorizes. The
    page shows only *limited*, privacy-conscious info about the inviter before
    the user signs in/up. Accepting/rejecting requires an authenticated user
    who is the intended invitee.
    """
    invitation = find_invitation_by_token(token)
    if invitation is None:
        return render(
            request,
            "partnerships/invite_invalid.html",
            {"page_title": "Invitation not found"},
            status=404,
        )

    expired = invitation.status == InvitationStatus.EXPIRED or invitation.is_expired
    if invitation.status == InvitationStatus.SENT and invitation.is_expired:
        # Deterministic terminal state.
        invitation.status = InvitationStatus.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])

    context = {
        "page_title": "Invitation",
        "invitation": invitation,
        "inviter_name": policies.safe_display_name(invitation.inviter),
        "token": token,
        "expired": expired,
        "used": invitation.status
        in (
            InvitationStatus.ACCEPTED,
            InvitationStatus.REVOKED,
            InvitationStatus.REJECTED,
        ),
    }

    if request.user.is_authenticated:
        context["is_invitee"] = policies.is_invitation_for(request.user, invitation)
        context["recipient_matches"] = (
            request.user.email.lower() == invitation.invitee_email.lower()
        )
        # Pre-resolve the invitee account for a sign-up prompt.
        invitee_user = User.objects.filter(email__iexact=invitation.invitee_email).first()
        context["invitee_has_account"] = invitee_user is not None
    else:
        context["is_invitee"] = False
        # A non-account visitor is told they need an account at that address.
        context["invitee_has_account"] = User.objects.filter(
            email__iexact=invitation.invitee_email
        ).exists()

    if (
        request.user.is_authenticated
        and policies.is_invitation_for(request.user, invitation)
        and invitation.status == InvitationStatus.SENT
        and not invitation.is_expired
    ):
        context["actionable"] = True
    else:
        context["actionable"] = False

    return render(request, "partnerships/invite_landing.html", context)
