"""Whiteboard authorization policies.

A whiteboard is owned by a partnership, so access is governed by partnership
membership. This module re-exports the single source of truth (
``apps.partnerships.policies.can_access_whiteboard``) under a whiteboard-scoped
name, keeping the rule in one place while giving the whiteboard app a stable,
intentional API.

Security boundary: the server resolves the partnership from its ``public_id``
and then checks membership. Client-supplied ids and client-side state are never
trusted; the URL's UUID only *locates* the resource, it does not grant access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.partnerships.policies import can_access_whiteboard as _can_access

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.accounts.models import User
    from apps.partnerships.models import Partnership


def can_view_whiteboard(user: User, partnership: Partnership) -> bool:
    """Return whether ``user`` may open the whiteboard of ``partnership``.

    Access requires an **active membership** in that partnership — a pending or
    ended partnership grants nothing, and a non-member (or the partner whose
    membership has lapsed) is denied. ``@login_required`` alone is not enough;
    this predicate is the authorization gate.
    """
    return _can_access(user, partnership)
