# Production Architecture

**Status: target/intended topology, not a deployed system.** No staging or
production environment currently exists for this application — this
document describes the architecture the codebase is already built for
(ASGI, Channels, PostgreSQL, Redis are all wired and configuration-gated on
real production settings), so that whoever provisions the first real
deployment has a concrete target rather than having to reverse-engineer it
from `config/settings/production.py`.

## Topology

```
                         Internet
                            │
                    ┌───────▼────────┐
                    │  Reverse Proxy  │   TLS termination, HTTP→HTTPS
                    │  (nginx/Caddy)  │   redirect, WebSocket upgrade
                    └───────┬────────┘   passthrough, /media/ serving
                            │
                    ┌───────▼────────┐
                    │  ASGI process   │   daphne or uvicorn workers
                    │ (Django+Channels)│  behind gunicorn's ASGI worker class
                    └───┬───────┬────┘
                        │       │
              ┌─────────▼─┐   ┌─▼──────────┐
              │ PostgreSQL │   │   Redis    │
              │ (authoritative│  (Channels layer +
              │  data store) │   cache/rate-limit)
              └────────────┘   └────────────┘
```

## Why ASGI, not WSGI

The application serves both ordinary HTTP (Django views/DRF) and WebSocket
connections (`apps/whiteboard/consumers.py`) from the same codebase. `config/
asgi.py` is the actual production entrypoint (`ASGI_APPLICATION` in
settings); `config/wsgi.py` exists for compatibility but cannot serve
WebSocket traffic — a production deployment **must** run the ASGI app, not
the WSGI one, or real-time collaboration silently stops working while HTTP
still appears fine.

Both `daphne` and `gunicorn` are listed as dependencies (`pyproject.toml`).
Two viable process models:
- `daphne -b 0.0.0.0 -p 8000 config.asgi:application` — Channels' own ASGI
  server, handles HTTP and WebSocket natively.
- `gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker` —
  gunicorn's process manager (restarts, worker count) with an ASGI-capable
  worker class. Requires adding `uvicorn` as a dependency if chosen (not
  currently in `pyproject.toml` — a deliberate choice point, not an
  oversight, since `daphne` alone already satisfies the requirement).

Either is reasonable; this repo doesn't mandate one. What matters is that
whichever is chosen actually speaks ASGI end-to-end.

## Reverse proxy responsibilities

- TLS termination and HTTP→HTTPS redirect (Django's own
  `SECURE_SSL_REDIRECT` in `production.py` is a second-layer redirect, not a
  substitute for the proxy actually terminating TLS).
- Forward `X-Forwarded-Proto` — `production.py` already sets
  `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` to trust
  this header; the proxy must actually set it correctly, or Django will
  incorrectly believe every request is insecure (or, worse, incorrectly
  trust a spoofed header if the proxy doesn't strip a client-supplied one —
  configure the proxy to always overwrite this header, never merge/append).
- WebSocket upgrade passthrough (`Upgrade`/`Connection` headers) for
  `/ws/whiteboards/<public_id>/` — a proxy that doesn't forward the upgrade
  handshake breaks real-time collaboration entirely while HTTP still works,
  which is an easy failure mode to miss in a smoke test that only checks
  page loads.
- Serving `/static/` is **not** the proxy's job — WhiteNoise
  (`whitenoise.storage.CompressedManifestStaticFilesStorage`, wired in
  `production.py`) serves static assets directly from the ASGI process with
  far-future cache headers and hashed filenames for safe cache-busting.
  Consider putting the proxy in front of it anyway for TLS/compression, but
  the actual file-serving logic is WhiteNoise's, not the proxy's.
- Serving `/media/` **is** the proxy's job. WhiteNoise's `STORAGES`
  override in `production.py` only replaces the `"staticfiles"` backend,
  never `"default"` (media stays `FileSystemStorage`) — see the corrected
  comment in `config/urls.py`. The proxy must be configured to serve
  `MEDIA_ROOT` at `MEDIA_URL` directly (Django itself only does this when
  `DEBUG=True`).

## Environment variables required at startup

`config/settings/production.py` fails loudly (`ImproperlyConfigured`) at
import time — before the process can serve a single request — if any of
these are missing or invalid: `DJANGO_SECRET_KEY` (≥50 chars),
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`,
`WEBSOCKET_ALLOWED_ORIGINS`, `POSTGRES_PASSWORD`. See `.env.example` for the
full set including optional overrides (`SESSION_COOKIE_AGE`,
`MAX_REQUEST_BODY_BYTES`, `POSTGRES_SSLMODE`, `LOG_JSON`, etc.) and the
recommended `SECRET_KEY` generation command.

## Redis

Two independent uses, both ephemeral (see `docs/architecture/phase-8-
production-audit.md` §14 for the full analysis):
1. Django Channels layer (`channels_redis.core.RedisChannelLayer`) — required
   for WebSocket messages to cross ASGI worker processes. A single-process
   deployment could theoretically use `InMemoryChannelLayer` (as development
   does), but that defeats horizontal scaling and isn't recommended for
   production even at small scale.
2. Cache backend (`django.core.cache.backends.redis.RedisCache`) — rate
   limiting and general caching. Losing this cache loses in-flight rate-limit
   counters (fails open, not closed — a restart resets abuse counters) and
   the `/health/ready/` cache probe would report degraded, but no
   confirmed whiteboard data is ever at risk from a Redis outage.

Redis ≥ 6 is required (the RESP2 `HELLO` handshake used by `redis-py` 8 /
`channels-redis` 4.3 — see `.env.example`'s comment on `REDIS_URL`).

## Horizontal scaling

The architecture already supports multiple ASGI worker processes/hosts
without code changes: Channels' Redis layer is exactly the mechanism that
lets WebSocket broadcasts cross process boundaries (a message committed by
the worker handling client A's connection reaches client B's connection on a
*different* worker via Redis pub/sub, not shared memory). No in-process
authoritative state exists anywhere in the request/consumer code — every
decision is re-derived from PostgreSQL (and, for ephemeral presence, from the
current WebSocket connection's own scope) on every request, which is what
makes this safe to scale horizontally.

## Deferred to actual deployment

This document describes the target. Choosing and provisioning the actual
reverse proxy, ASGI process manager, container/VM/bare-metal hosting,
DNS, and TLS certificate are deployment-time decisions this repository
cannot make on its own behalf — see `docs/production-readiness-report.md`
for the explicit list of what remains deferred until a real environment
exists.
