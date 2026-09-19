# Monitoring

**Status: specification of what to monitor, not a deployed dashboard.** No
monitoring stack (Prometheus, Grafana, Sentry, Datadog, or equivalent) is
wired up anywhere in this repository — there is nothing to monitor yet
because there is no deployed instance. This documents what a deployment
should watch and why, informed by what the application actually does, so the
first real deployment doesn't have to derive it from scratch.

## What to monitor and why

| Metric | Why it matters | Source |
|---|---|---|
| HTTP error rate (5xx) | Direct signal of application-level failures. | Reverse proxy / ASGI server access logs. |
| `/health/ready/` status | Composite DB+cache readiness — alert on sustained 503, not a single blip (transient network hiccups happen). | The endpoint itself; see `docs/operations/health-checks.md`. |
| PostgreSQL connectivity & query latency | The sole authoritative data store — any degradation here degrades everything. | Database-level monitoring (the hosting provider's built-in metrics, or `pg_stat_statements`). |
| Redis connectivity | Channels layer + rate-limit cache. Losing this degrades real-time collaboration and abuse protection, but never loses confirmed data (see `docs/operations/disaster-recovery.md`) — worth alerting on, not paging-at-3am critical. | Redis-level monitoring. |
| WebSocket connection count & failure rate | The real-time collaboration feature's core health signal; `apps/whiteboard/consumers.py` already logs `ws.connected`/`ws.rejected`/`ws.disconnected`/`ws.rate_limited` — these are the events to aggregate. | Structured application logs (`LOG_JSON=true`, see below). |
| Rate-limit trip counts (`ratelimit` events) | A sudden spike suggests either abuse or a legitimate usage pattern the limits are too tight for — either way, worth seeing. | Application logs — every rate-limited rejection is logged (`apps.accounts.views`, `apps.partnerships.views`, `apps.whiteboard.consumers`). |
| Operation rejection rate (`STALE_VERSION`, `INVALID_OPERATION`, etc.) | A baseline rate of `STALE_VERSION` is *expected* (two partners drawing concurrently) — the useful signal is a rate that's abnormally high relative to active-user count, which could indicate a client-side bug or an attack. | `apps/whiteboard/service.py` / `consumers.py` structured logs. |
| Request latency (p50/p95/p99) | Standard web-app health signal. | Reverse proxy / ASGI server, or APM if one is added later. |
| Disk usage (PostgreSQL host) | `WhiteboardOperation` grows append-only and is never pruned (deliberately — see `docs/architecture/phase-8-production-audit.md` DP-1) — this is the metric that tells you when snapshotting/compaction actually needs to move from "documented as deferred" to "implemented." | Host/infra-level disk monitoring. |
| Memory / CPU (application host) | Standard infra health. | Host/infra-level monitoring. |

## Suggested alert thresholds (proposed, not tuned against real traffic)

These are starting points, explicitly *not* validated against production
load (none exists) — tune after observing real traffic patterns rather than
trusting these numbers blindly:

- `/health/ready/` returning 503 for **more than 2 consecutive minutes** →
  page. A single missed check is noise; sustained failure is real.
- HTTP 5xx rate **above 1% of requests over 5 minutes** → page.
- PostgreSQL connection failures **at all** → page (this is the
  authoritative data store; any failure here is significant).
- Redis connection failures **at all** → alert, not page (degrades
  real-time collaboration and rate limiting, doesn't lose data — see
  disaster-recovery.md).
- Disk usage on the PostgreSQL host **above 80%** → alert with time to
  investigate; **above 90%** → page.
- TLS certificate expiry **within 14 days** → alert (infrastructure-level,
  not application code — see disaster-recovery.md).

Avoid alerting on noisy, expected-to-fluctuate signals without a sustained
threshold — a single rate-limited request or a single `STALE_VERSION`
rejection is normal application behavior, not an incident.

## Logs as the current, real observability mechanism

Structured JSON logging already exists and works today (not a future item):
`apps/core/logging.py`'s `JSONFormatter`, toggled via `LOG_JSON=true`
(`.env.example`). Every log line is a single JSON object with `timestamp`,
`level`, `logger`, `message`, and `exception` when present — suitable for
ingestion by any log aggregator (CloudWatch, Loki, Datadog logs, etc.) once
one is chosen at deployment time. **Known gap, documented honestly**: log
lines carry no per-request correlation ID, so tracing a single request's
full log trail across multiple log lines currently requires matching on
context (user id, whiteboard id, timestamp proximity) rather than a single
shared identifier — a reasonable next increment, not fixed this pass.

## Deferred to actual deployment

- Standing up any actual monitoring/alerting stack — nothing is deployed.
- Tuning the proposed thresholds above against real traffic.
- APM/tracing (request-level latency breakdown beyond aggregate metrics).
- Log aggregation pipeline (the JSON logs are ready to ship *to* one; none
  exists to receive them yet).
