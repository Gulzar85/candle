# Phase 10 Audit — Scale, Advanced Collaboration, Extensibility & Intelligent Whiteboard

**Date:** 2026-09-05
**Scope:** Mandatory pre-implementation audit (Phase 10 spec §1). Evaluates the
mature product against "evidence before architecture": what the system actually
is, where its real limits and bottlenecks are, what Phase 10 should and should
not do.

**Method:** Direct code reading (backend models/services/consumers/settings,
frontend modules), review of all Phase 0–9 docs, production-readiness report,
both performance benchmark sets, and fresh measured test/build/check runs on
this working tree (backend whiteboard suite 198/198, full suite 301/311 with
the documented 10-test concurrency ordering flake, frontend vitest 114/114,
`tsc --noEmit` clean, `vite build` clean, `ruff check`/format clean,
`manage.py check` clean, `makemigrations --check --dry-run` clean).

---

## 1. Repository state (baseline note)

Phase 9 (history/restore/move-resize/export-import), the offline conflict-fix
(removed client quarantine), and the doc reconciliation are **all still
uncommitted** on top of `57e7b64` (Phase 0–7). The audit below reflects the
current working tree, which is the true state of the product. Phase 8 (production
hardening) and Phase 9 deliverables are the live baseline for Phase 10.

---

## 2. Current architecture

### Data
- Single PostgreSQL database. `WhiteboardOperation` is an append-only, immutable,
  server-sequenced ledger with `JSONB` payload (`apps/whiteboard/models.py:85`).
  Authoritative state = latest replayed ledger. Sequencing happens in one
  `transaction.atomic()` + `select_for_update()` row lock per whiteboard
  (`service.py:130-191`); sequences, versions, and idempotency
  (`(whiteboard, operation_id)` unique) are the correctness core.
- `Whiteboard.partnership` is `OneToOneField` — exactly one board per
  two-person Partnership, enforced structurally. The Partnership itself is
  DB-enforced to two active members (migration 0002 trigger + partial unique
  index). No attachments, comments, or text-object models (Phase 9 deferred them).
  `Notification` model exists in partnerships for intra-app notices.
- Time: `USE_TZ=True`, `TIME_ZONE="UTC"`; all timestamps aware.

### Realtime
- Channels + Daphne: `ProtocolTypeRouter`, session-cookie auth via
  `AuthMiddlewareStack`, `AsyncWebsocketConsumer` re-authorizing **every message**
  against the DB (`consumers.py:232-255`). Group membership lives in the channel
  layer; production uses `channels_redis.RedisChannelLayer`
  (`settings/base.py:105-112`); dev/tests use in-memory.
- WS submit and HTTP submit both funnel into the same
  `WhiteboardOperationService.submit_batch`, so sequence/version/idempotency
  semantics are identical on both paths. Catch-up is idempotent
  (`sync.ops` gap replay filtered by `sequence > client_version`, deduped
  client-side by `operation_id`).

### Offline / sync
- Durable IndexedDB queue (crash-safe `saveOperation` before any traffic),
  always-anchored flush (post-bugfix: no client-side quarantine/conflict panel),
  `STALE_VERSION` → re-anchor + re-run, permanent reject → `FAILED` save error.
  Legacy-conflict rows healed at reopen. Offline reopen restores a capped
  logical stroke snapshot + replays pending ops.

### Frontend
- Vanilla TypeScript + htmx + Alpine. Board model is pure op-stack replay
  (`state.ts`); canvas renderer uses a baked static layer + live stroke
  (`renderer.ts`); movement/resize redraw on top without touching the bake.
  Undo/redo = in-memory op stacks (`state.ts:13-21`), currently **unbounded**.
- i18n: none (strings hard-coded); timezone: history panel renders hand-rolled
  English relative time from ISO UTC (`history.ts:167-177`).
- No feature flags, no metrics client, no text/image object types.

---

## 3. Measured baseline (what we actually know)

| Measure | Result | Source |
|---|---|---|
| Board fetch / replay @ 100/1k/5k ops | 11.4/19.5/147.1 ms fetch; 0.1/1.6/7.9 ms replay | `docs/performance/whiteboard-benchmarks.md:31-35` |
| Restore `reconstruct_state_at` @ 100/1k/5k/20k objects | 14/29/221/631 ms | `docs/performance/phase-9-benchmarks.md:30-36` |
| Restore snapshot JSON @ 100/1k/5k/20k | 14.9 KB / 149.8 KB / 753.1 KB / 3,025.1 KB (~150 B/object) | same |
| Ledger growth | ~1–5 KB per operation ⇒ 10k ops ≈ 10–50 MB | `docs/architecture/phase-8-production-audit.md:258` |
| WS frame cap / per-conn limits | 250 KB frame; 600 msgs & 120 submits per 60 s | `realtime.py:72`, `consumers.py:97-103` |
| Bounds | offline pending ≤ 5,000; cached boards ≤ 20; cached strokes/board ≤ 20,000 | `local-store.ts:128-130`, `sync-engine.ts:569` |
| Tests | backend whiteboard 198/198; full suite 301/311 (10-test ContentType ordering flake, passes 10/10 in isolation); frontend 114/114; checks all clean | runs on 2026-09-05 |

The system has **no critical bottleneck at current scale**. Costs are all
measurable and all currently small. The three genuine growth/reliability risks
under measurement are: (1) **unbounded per-board objects/operations** (SC-1 —
also the only realistic abuse vector), (2) **linear-from-genesis replay** (DP-1)
on every load/reconnect/restore once a board passes tens of thousands of ops,
and (3) **per-message DB re-authorization** on the WebSocket hot path.

---

## 4. Current limitations and technical debt

- **SC-1 — no per-board object/operation cap.** Import is guarded
  (`MAX_IMPORT_OBJECTS`), drawing is not. Only realistic DoS/abuse vector
  (`phase-9-audit.md:45-48`). Fixing this is cheap and safe.
- **DP-1 — no snapshot/compaction; replay is O(history).** Restore explicitly
  reuses the `replay(start=...)` seam for a product reason, not as the DP-1
  performance fix (`phase-9-audit.md:38-44`). Measured 20k-object boards replay
  in ~0.6 s and ~3 MB; the crossing point where snapshotting pays is >~50k ops
  chronically. **Not justified to implement now**; justified to *design and gate*.
- **Per-message WS re-auth** (`consumers.py:232-255`) is a DB read on the hot
  path. Fine at 100–1,000 concurrent; not free. Candidate optimization only if
  load tests show it matters.
- **Per-process `MAX_CONNECTIONS_PER_USER=10`** (`consumers.py:51-52`) — a
  single-process safety cap; with N ASGI workers the cluster-wide limit becomes
  10×N. Cosmetic/safety only, not correctness; documented in code.
- **Unbounded undo/redo stacks** (`state.ts`) and unbounded `conflicts` IDB
  store (legacy-heal-only path). Memory growth risk in very long sessions /
  large boards (spec §35).
- **No correlation IDs** (E-2 partial), no metrics endpoint, no APM — only
  `/health/*`, JSON logging, and a monitoring *spec* doc
  (`docs/operations/monitoring.md`).
- **Unthrottled endpoints**: rename (PATCH), GET state/operations/history,
  verify_email, email_change, avatar upload, logout. Most are deliberate
  (`docs/security/rate-limits.md:84-90`).
- **Test-suite debt**: 301/311 full-suite with the inherited ContentType race;
  mypy 120 errors (all pre-existing category); no CI runner ever executed; no
  E2E; no load tests; no live staging/prod. `docs/production-readiness-report.md`.
- **Docs/ops**: legacy `gunicorn config.wsgi -w 4` reference is stale (ASGI
  mandatory); uvicorn not a dependency; no Procfile/Docker.

---

## 5. Actual scalability constraints (horizontal-scaling readiness)

Audited for the two-ASGI-instance topology the spec draws. Result: **production
is already horizontally safe by construction** — no correctness fix required.

| Concern | Finding |
|---|---|
| WebSocket routing | Same-origin cookie sessions; `AuthMiddlewareStack`; no affinity dependency (`asgi.py`, `consumers.py`). |
| Channel layer | `channels_redis.RedisChannelLayer` in production — groups/presence fanout cross workers (`settings/base.py:105-112`). |
| Sequencing | DB `select_for_update` row lock; both WS and HTTP hit the same service. Safe under N instances (`service.py:130-166`). |
| Idempotency | Unique `(whiteboard, operation_id)`; duplicate acks `duplicate: true`. Safe. |
| Rate limits | HTTP limits cache-backed (Redis in prod, `accounts/ratelimit.py`); WS limits are per-connection (correct per-connection semantics). |
| Presence | Broadcast-only; client maintains the online set; group membership lives in the channel layer → correct across workers. |
| Process-local state | Only `_user_connection_counts` (per-worker safety cap, documented) and per-connection rate timers. Nothing correctness-bearing. |

The one scaling-driver to plan *around*, not fix yet: PostgreSQL is the single
writer and the replay engine. Parallelism is per-board (row lock), so throughput
scales with distinct boards, not with ops per board.

### Realistic capacity assessment

This product is a **private two-partner whiteboard**. The spec's "100,000 users /
10,000 boards / 1,000+ concurrent WS" scenario is outside the demonstrated
product class; per the spec's own rule, do not build for hypothetical scale.

- **Small (100 users / 50 boards / 10 concurrent)** — trivially fine on one
  small VM + Postgres + Redis. No changes.
- **Medium (10k users / 1k boards / 100 concurrent)** — one Daphne instance
  plus a second for head-room, Redis channel layer, Postgres with sensible
  indexes (present). Bottleneck becomes sustained High-op boards (linear replay)
  and per-message re-auth — both deferred until load tests quantify them.
- **Large (100k users / 10k boards / 1k+ concurrent WS)** — would require
  measured justification, object-storage, snapshotting, and more ASGI workers;
  none of this is warranted by evidence today.

Estimated arithmetic for honesty: idle WS holds ~1,000s/instance; message flow is
bounded by per-connection limits (120 submits/60 s). Redis channel-layer cost is
per-broadcast × group size (≤2 on a board) — negligible. DB row-locked
submit throughput across 1k boards is well above plausible load.

Full capacity math goes in `docs/performance/phase-10-capacity-plan.md` (§3),
built from **load tests**, not assumptions.

---

## 6. Database growth, storage, retention

- Growth is append-only ledger + JSONB snapshots from restore. 10k ops ≈
  10–50 MB; restore payloads add bounded, predictable storage (~150 B/object,
  capped by `MAX_RESTORE_OBJECTS`/`MAX_RESTORE_PAYLOAD_BYTES`). No
  partitioning/archival/read-replica justified by measurement.
- Indexing is complete (unique `(whiteboard, sequence)` covers ordering/range;
  `(whiteboard, operation_id)` for idempotency; `(whiteboard, operation_type)`;
  `(whiteboard, created_at)`). No missing index found on hot paths.
- `operation-retention.md` and `whiteboard-snapshots.md` (spec §52) should be
  written as *policy + delegation decision* documents: retention is currently
  "keep everything, bounded per board by a new content cap"; snapshotting is
  gated behind a measured trigger (see §8). Neither should be implemented as
  code without that trigger firing.

---

## 7. Product opportunities (evidence-gated)

Existing-but-thin and genuinely useful, in descending confidence:

1. **Abuse/content cap (SC-1)** — a per-board configurable cap on objects/ops
   over the ledger's lifetime. Concrete, measurable, closes the one real
   DoS/abuse gap. Justified now.
2. **Observability §33** — add aggregate metrics (active WS, ops/min, sync
   failures, reconnect rate, board load time, export duration) as a minimal
   endpoint + log; add correlation IDs (E-2). Answers "are we healthy?" without
   collecting private content. Justified now.
3. **Collaboration polish (§11/§12)** — selection awareness ("Partner is viewing
   this object"), follow-collaborator viewport mode, and "user is editing"
   indicators. All ephemeral (no DB, no history, no offline queue — spec
   §10/§12 explicitly forbid persistence). Client-presence/follow have real
   two-person value and small cost. Justified as optional.
4. **Feature flags (§32)** — a lightweight settings-driven flag registry for
   future optional capabilities (AI/comments/attachments/templates). Prevents
   flag-based scope creep; cheap. Justified as foundation, not for today's
   features.
5. **Command palette + context menus + keyboard object actions (§19/§20/§37)** —
   power-user/mobile/accessibility parity, no new persistence. Optional.
6. **Performance budgets + capacity plan + scaling doc (§34/§48)** — required
   artifacts even when thresholds are "measured today, revisit when X".
7. **Account data export + deletion policy (§40/§41)** — privacy/legal
   readiness; genuinely large (partnered data, indexdb/caches, ownership rules).
   Needs an explicit product decision; not assumed.
8. **Load testing (§4)** — the evidence step that decides every numeric claim
   above.

Not justified by evidence: AI features (§26 — no demonstrated user problem),
attachments/comments/templates/search/presentation/minimap/advanced
shapes/connectors/smart objects (§13-25 — Phase 9 deferral reasoning stands,
none now has a use signal), i18n infra (§43 — single-language audience; note
architecture is i18n-ready server-side), API versioning (§30 — no external API
consumers exist), webhooks/integrations/embeds/links (§25/§31 — security surface
with no product pull).

---

## 8. Recommenders and desired non-goals

### Recommended Phase 10 work (evidence-first)
1. **Load testing + capacity plan + performance budgets + scaling strategy**
   (§3/§4/§34/§48) — the evidence backbone; writes the four performance/deploy
   docs with real numbers and revisit triggers.
2. **Per-board content/operation cap** (SC-1) — configurable, enforced at
   submit, integrated with the error surface; no data loss (reject-new, never
   delete).
3. **Aggregate observability + correlation IDs** (§33/E-2) — minimal metrics
   surface, no private content.
4. **Ephemeral collaboration improvements** (§11/§12) — selection awareness,
   follow mode, editing indicators; explicitly non-persistent.
5. **Lightweight feature flags** (§32) — settings-driven safe defaults.
6. **Docs deliverables** as policy + deferral: `operation-retention.md`,
   `whiteboard-snapshots.md`, `data-retention.md`, security review, test plan.
7. **Snapshotting / compaction code: NOT now.** Gated behind a measured trigger
   (>~50k ops/board recurrent, or load-test evidence). Keep the seam
   (`replay(start=...)`) documented; do not delete history or add derived-state
   complexity without the measurement firing.

### Features that must NOT be implemented (this phase)
Microservices, Kubernetes, Kafka, message bus, CRDT, OT, Elasticsearch,
GraphQL, React, native mobile, AI provider/infrastructure, complex event
sourcing, DB partitioning, read replicas, object-storage migration, full
project-management platform, chat, video, full vector editor, enterprise
permission systems, arbitrary iframe/embed support, public links/global
search. All lack a demonstrated requirement (spec §2/§51).

---

## 9. Security posture (audited, Phase 9 features included)

Strong and consistent (uniform membership checks, per-message WS re-auth,
server-side object-id regeneration, safe avatar re-encoding, no unsafe
deserialization, CSP + `check --deploy` clean). Remaining items are
maintenance, not holes: SC-1 abuse cap; thin integration/IDOR/WS security
tests (spec §46 wants more); unthrottled rename/profile/verify endpoints;
PBKDF2 default (informational); no correlation IDs; acceptable PWA CSP gap
(PWA-1). Full `phase-10-security-review.md` will re-test IDOR/XSS/CSRF/SSRF/
uploads/WS authz/rate limits/revocation and (since Phase 10 adds none here) the
URL/embed surface is confirmed non-existent.

---

## 10. Privacy & data

Stored data classes: account/partnership data, ledger content (board strokes
+ restore snapshots), presence (ephemeral, in-memory only), IndexedDB +
service-worker cache (device partition by `owner_key`). No AI data (none added).
Duties: `data-retention.md` (policy), account-export/deletion (decision
required), and the standing §42 test — account switching must not leak IndexedDB
data — already covered by `owner_key` partitioning + cache-logout policy, to be
re-verified in the Phase 10 test plan.

---

## 11. Decision points for Phase 10 scope

The audit's honest bottom line: the product is architecturally sound and
horizontally safe; nothing here *requires* new architecture. Phase 10's value
is (a) producing the measurement/plan artifacts the spec demands, (b) fixing
the one real abuse/growth gap (SC-1), and (c) the small ephemeral-collaboration
and observability polish. Snapshots, compaction, AI, attachments, comments,
advanced search, templates, and all "intelligent" features are deferred
pending demonstrated need — consistent with the product's own documented
deferral discipline.

Outstanding scope decisions for the user (each has material cost/benefit):
1. Load testing + capacity/performance/scaling docs (evidence phase).
2. SC-1 content cap.
3. Aggregated observability metrics + correlation IDs.
4. Ephemeral collaboration (selection awareness, follow, editing indicators).
5. Lightweight feature flags.
6. Account data export + deletion policy (privacy/legal readiness).
7. Docs-as-policy deliverables (retention/snapshots/data-retention/test plan/
   security review) vs. deeper implementation.