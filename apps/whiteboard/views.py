"""Whiteboard views.

Views translate HTTP into selector/policy calls and back. The whiteboard page is
a *full-viewport* surface: it extends ``base.html`` directly (not the shell
layout) so the canvas gets maximum space and there is no dashboard chrome.

Authorization is entirely server-side: the partnership is resolved only from its
``public_id`` in the URL, then membership is checked. Logging in is necessary but
not sufficient — anything other than an active member of that partnership is
denied with a 403 before any page is rendered.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.models import User
from apps.partnerships.models import Partnership
from apps.partnerships.policies import safe_display_name
from apps.partnerships.selectors import get_partner

from . import policies, selectors
from .service import WhiteboardMetadataService


def _current_user(request: HttpRequest) -> User:
    """Narrow the authenticated user (safe behind ``@login_required``)."""
    assert isinstance(request.user, User)
    return request.user


@login_required
def whiteboard_home(request: HttpRequest, public_id: str) -> HttpResponse:
    """Render the full-viewport whiteboard for a partnership's UUID.

    The URL only *locates* the partnership; the server decides access by checking
    the requesting user holds an ACTIVE membership in it. Pending partnerships
    (not yet accepted) or ended partnerships are redirected to the partnership
    home rather than exposed.
    """
    partnership = get_object_or_404(Partnership, public_id=public_id)

    if not policies.can_view_whiteboard(_current_user(request), partnership):
        return render(request, "errors/403.html", status=403)

    if not partnership.is_active:
        # No shared space until the partnership is active.
        return redirect("partnerships:partnership_home")

    user = _current_user(request)
    partner = get_partner(user, partnership)
    whiteboard = selectors.whiteboard_for_partnership(partnership)

    context = {
        "page_title": "Shared space",
        "whiteboard": whiteboard,
        "partnership": partnership,
        "partner": partner,
        "user_initials": (user.profile.get_initials if hasattr(user, "profile") else "") or "",
        "partner_initials": (
            partner.profile.get_initials if partner and hasattr(partner, "profile") else ""
        )
        or "",
        "partner_name": safe_display_name(partner),
        # Pure client-side draw state. Access is still fully server-authoritative;
        # this identity is only used to tag objects created in this browser.
        "creator_initials": (user.profile.get_initials if hasattr(user, "profile") else "") or "",
        # Account-scoped key used to partition local (IndexedDB) cache so one
        # account's data never appears for another.
        "account_id": str(user.public_id),
    }
    return render(request, "whiteboard/whiteboard.html", context)


@login_required
def whiteboard_archive_toggle(request: HttpRequest, public_id: str) -> HttpResponse:
    """Archive or restore a whiteboard (metadata; server-rendered form target).

    POST only, body: ``action=archive`` or ``action=unarchive``. Goes through
    the same ``WhiteboardMetadataService`` as every other metadata mutation —
    there is exactly one path that changes a whiteboard's archive state,
    whether triggered from here or (in the future) a JSON client.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    partnership = get_object_or_404(Partnership, public_id=public_id)
    if not policies.can_view_whiteboard(_current_user(request), partnership):
        return render(request, "errors/403.html", status=403)
    if not partnership.is_active:
        return redirect("partnerships:partnership_home")

    whiteboard = selectors.whiteboard_for_partnership(partnership)
    action = request.POST.get("action")
    service = WhiteboardMetadataService()
    if action == "archive":
        service.archive(whiteboard)
    elif action == "unarchive":
        service.unarchive(whiteboard)

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(reverse("accounts:dashboard"))
