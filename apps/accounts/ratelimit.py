"""Lightweight, cache-backed authentication rate limiting.

A small focused solution built on Django's caching framework (Redis in
production, in-memory cache in development) rather than a heavyweight
third-party security framework. Each call site supplies a namespace/scope and a
key (typically an IP address, plus the target email where relevant) so that a
compromised endpoint can be throttled independently.

The limiter uses an incrementing counter stored in the cache for exactly
``window`` seconds from the *first* request in that window (a true fixed
window, not a wall-clock-minute bucket -- a counter keyed by the current
calendar minute resets at every minute boundary regardless of when the
window actually started, which both under-counts a burst that straddles a
boundary and makes a "5 minute window" behave like a "1 minute window").
Once the limit is reached within that window, a *separate* cooldown marker
is set for ``cooldown`` seconds; unlike the counter, the cooldown is not
tied to the counter's expiry, so a blocked caller stays blocked for the
full cooldown even if the counter window happens to lapse first. On
authenticated success the caller can reset both so a legitimate user is not
penalised further.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0

    @property
    def blocked(self) -> bool:
        return not self.allowed


def _count_key(scope: str, key: str) -> str:
    return f"ratelimit:count:{scope}:{key}"


def _block_key(scope: str, key: str) -> str:
    return f"ratelimit:block:{scope}:{key}"


def check(
    scope: str,
    key: str,
    limit: int,
    window: int,
    *,
    cooldown: int | None = None,
) -> RateLimitResult:
    """Return whether a request identified by ``key`` is currently allowed.

    ``scope`` separates distinct endpoints so a burst on, say, login does not
    affect registration. ``cooldown`` (seconds) is how long to keep rejecting
    after the limit is exceeded; it defaults to ``window``.
    """
    cooldown = window if cooldown is None else cooldown

    if cache.get(_block_key(scope, key)) is not None:
        return RateLimitResult(allowed=False, retry_after=cooldown)

    count_key = _count_key(scope, key)
    if cache.add(count_key, 1, timeout=window):
        # First request of a fresh window.
        return RateLimitResult(allowed=True)

    try:
        value = cache.incr(count_key)
    except ValueError:
        # The counter expired between add() and incr() (a narrow race at the
        # window boundary) -- treat this as the first request of a new one.
        cache.add(count_key, 1, timeout=window)
        return RateLimitResult(allowed=True)

    if value > limit:
        cache.set(_block_key(scope, key), True, timeout=cooldown)
        return RateLimitResult(allowed=False, retry_after=cooldown)

    return RateLimitResult(allowed=True)


def reset(scope: str, key: str) -> None:
    """Clear the counter and any active cooldown after a successful auth."""
    cache.delete_many([_count_key(scope, key), _block_key(scope, key)])
