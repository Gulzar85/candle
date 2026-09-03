"""Secure invitation token generation and validation.

The raw token is a cryptographically secure, unguessable URL-safe string. It is
embedded in the emailed accept link but **never persisted**; only a SHA-256
digest is stored on ``PartnershipInvitation.token_hash``.

Invitation possession is proved by presenting the raw token; the server decides
what that token is authorized to perform (never trust ``?user_id=``).
"""

from __future__ import annotations

import secrets

from .models import PartnershipInvitation, hash_token

# Raw token entropy: 256 bits of randomness -> unguessable.
RAW_TOKEN_LENGTH = 32


def generate_raw_token() -> str:
    """Return a fresh, unguessable raw invitation token for a single email."""
    return secrets.token_urlsafe(RAW_TOKEN_LENGTH)


def digest(raw_token: str) -> str:
    """Return the stored digest for a raw token (never stores the raw token)."""
    return hash_token(raw_token)


def find_invitation_by_token(raw_token: str) -> PartnershipInvitation | None:
    """Resolve an invitation by its raw token, or ``None`` if unknown.

    A malformed or empty token simply yields ``None`` (never raises). The
    caller is responsible for checking status/expiry/ownership afterwards.
    """
    if not raw_token or len(raw_token) > 128:
        return None
    return (
        PartnershipInvitation.objects.select_related("partnership", "inviter")
        .filter(token_hash=hash_token(raw_token))
        .first()
    )
