# Health Checks

Already implemented and verified — this documents what exists, not a plan.

## Endpoints (`apps/core/views.py::health_check`)

| Endpoint | Purpose | Checks | Response |
|---|---|---|---|
| `/health/live/` | **Liveness** — is the process alive and able to handle a request at all? | Nothing beyond the process responding. | Always `200 {"status": "ok", "version": "0.1.0"}` if the process can run Python at all. |
| `/health/ready/` | **Readiness** — can this instance actually serve real traffic? | `SELECT 1` against the default PostgreSQL connection; if `CACHES` is configured (it always is — Redis in production, LocMemCache in dev), a `cache.set`/`cache.get` round-trip. | `200 {"status": "ok", "checks": {"database": true, "cache": true}}` if both pass. `503 {"status": "degraded", "checks": {...}}` if either fails — the failing check is named, not just a generic failure. |
| `/health/` (bare) | Backward-compatible alias. | Same as `/live/` — no dependency checks. | Always `200`. |

Both checks are deliberately cheap: one trivial query each, no table scans,
no expensive joins. A liveness/readiness probe that's itself slow or
resource-intensive defeats the purpose (a load balancer or orchestrator
polling it frequently shouldn't itself become load).

## What `/health/ready/` does *not* check

- The Channels/Redis **channel layer** specifically. The cache probe hits
  Redis via `django.core.cache.backends.redis.RedisCache`, which is the same
  Redis instance the channel layer uses in this deployment, but they're
  configured independently (`CACHES` vs `CHANNEL_LAYERS` in
  `config/settings/base.py`) — if a future deployment ever pointed them at
  *different* Redis instances or logical databases, this probe would not
  catch a channel-layer-specific outage. Worth revisiting if that
  configuration ever diverges; not an issue today since both point at the
  same `REDIS_URL`.
- Whether WebSocket connections can actually be established end-to-end
  (that requires a real client handshake, not something a lightweight HTTP
  probe can verify) — see `docs/operations/monitoring.md` for how to watch
  WebSocket health via connection/error metrics instead.
- Disk space, memory, or CPU headroom — infrastructure-level concerns, not
  application-level ones; a process orchestrator's own resource monitoring
  is the right layer for those, not this endpoint.

## Usage

- **Container/orchestrator liveness probe**: `/health/live/` — restart the
  process if this ever fails to respond (it should never legitimately fail
  unless the process itself is wedged).
- **Load balancer readiness probe / "should traffic route here"**:
  `/health/ready/` — pull an instance out of rotation on `503`, since it
  genuinely can't serve real requests (no DB, or the cache backend it needs
  for rate limiting is down).
- **External uptime monitoring**: either works; `/health/ready/` gives more
  signal (catches a DB/Redis outage the bare process being "alive" wouldn't
  reveal) at negligible extra cost.

## Deferred to actual deployment

Wiring these endpoints into a real orchestrator's probe configuration
(Kubernetes liveness/readiness probes, a load balancer's health-check path,
or an external uptime monitor like Pingdom/UptimeRobot) is a deployment-time
configuration step — the endpoints themselves are ready to be pointed at
today.
