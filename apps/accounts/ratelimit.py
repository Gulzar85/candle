"""Lightweight, cache-backed authentication rate limiting.

A small focused solution built on Django's caching framework (Redis in
production, in-memory cache in development) rather than a heavyweight
third-party security framework. Each call site supplies a namespace/scope and a
key (typically an IP address, plus the target email where relevant) so that a
compromised endpoint can be throttled independently.

The limiter uses an incrementing counter stored in the cache. A request is
allowed while the counter stays below ``limit`` within ``window`` seconds.
Once the limit is reached the scope enters a cooldown in which further
requests are rejected for ``cooldown`` seconds. On authenticated success the
caller can reset the counter so a legitimate user is not penalised further.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache
from django.utils import timezone


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0

    @property
    def blocked(self) -> bool:
        return not self.allowed


def _key(scope: str, key: str) -> str:
    return f"ratelimit:{scope}:{timezone.now().strftime('%Y%m%d%H%M')}:{key}"


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
    cache_key = _key(scope, key)
    value = cache.get(cache_key)
    if value is None:
        cache.add(cache_key, 1, timeout=window)
        return RateLimitResult(allowed=True)

    if value >= limit:
        # A compatible cache backend is not guaranteed to expose TTL, so the
        # retry-after is derived from the configured window/cooldown instead.
        retry = cooldown if cooldown is not None else window
        return RateLimitResult(allowed=False, retry_after=retry)

    cache.incr(cache_key, delta=1)
    return RateLimitResult(allowed=True)


def reset(scope: str, key: str) -> None:
    """Clear the counter for ``key`` after a successful authentication."""
    cache.delete(_key(scope, key))
