# Phase 4 Audit — Whiteboard Persistence, Operations & Synchronization Foundation

**Auditor:** Candle engineering (Senior Django Architect / Distributed Systems / Frontend)
**Date:** 2026-09-03
**Applies to:** the codebase after Phase 3 (local whiteboard engine), reviewed to
plan Phase 4 (server-side operation persistence + sync foundation).
**Purpose:** Record the audit of Phases 0–3 as the input to Phase 4, list issues,
severity, impact, recommended action, and what was done. This is the working
document Phase 4 is built against; it complements `phase-3-audit.md`.

> Methodology: static review of models, policies, views, the TypeScript engine,
> tests, and docs, plus a verification run of the toolchain (backend suite,
> frontend suite, `tsc`, build) recorded in the Phase 3 audit.

---

## 1. Verified environment (unchanged)

- Django **6.1.1** (latest stable at writing; supports Python 3.12/3.13/3.14,
  confirmed by `manage.py --version`). Python 3.13.4.
- DRF **3.18.0** pre-installed and pre-configured (`SessionAuthentication` +
  `IsAuthenticated` + `PageNumberPagination` `PAGE_SIZE=25`), but **not yet used**
  by any endpoint.
- PostgreSQL authoritative DB (psycopg3), Redis cache + channel layer.
- Frontend: strict TS, Vite multi-entry (`main`, `whiteboard`), Vitest 4, Lucide,
  Tailwind v4, CSP (nonce + a documented `unsafe-eval` for Alpine).
- Rate limiter module exists: `apps/accounts/ratelimit.py` (cache-backed, reusable).

---

## 2. Audit of previous phases

### Phase 0 — Foundation (status: healthy)
| Issue | Severity | Impact | Recommended action | Action taken |
|---|---|---|---|---|
| `SECURE_CSP.script-src` allows `unsafe-eval` (Alpine) | Low | Broader CSP | Keep; documented, Alpine requires it | Keep (documented) |
| `main.js` bundle > 500 kB (htmx+alpine), htmx `eval` warning | Low | Build hygiene / perf | Defer; separate whiteboard entry already sized | Keep (Phase-4 does not regress) |
| No snapshot/op infra | — | n/a | Phase 4 adds it | See §5 |

### Phase 1 — Accounts (status: healthy, known debt)
| Issue | Severity | Impact | Recommended action | Action taken |
|---|---|---|---|---|
| Mypy errors in `apps/accounts/models.py` (LSP overrides) | Medium | Tooling debt | Defer; whiteboard app must stay mypy-clean | Confirmed whiteboard clean; accounts left as-is (pre-existing) |
| Account lifecycle binary (`is_active`) | Medium | Deactivation policy | Already documented in prior audits | Not in Phase 4 scope; whiteboard uses partnership membership, not user `is_active`, for access |

### Phase 2 — Partnerships (status: healthy — the security boundary Phase 4 builds on)
| Issue | Severity | Impact | Recommended action | Action taken |
|---|---|---|---|---|
| None blocking | — | — | — | `can_access_whiteboard` is the single auth predicate to reuse; membership + partnership-status gate everything |

Key assets Phase 4 reuses unchanged:
- `Partnership.is_active`, `PartnershipMember` status, the DB trigger enforcing
  "exactly one active membership per user" and "at most two active members".
- `apps/partnerships/policies.can_access_whiteboard` — the authorization source
  of truth. Phase 4 must NOT redefine authorization; it reuses this.

### Phase 3 — Whiteboard engine (status: good foundation; gaps are exactly Phase 4)
| Issue | Severity | Impact | Recommended action | Action taken |
|---|---|---|---|---|
| **No operation persistence** — drawing is localStorage-free but entirely in-memory; lost on navigation | High (by design) | No durability | Phase 4: operation model + service + endpoint | Built (this phase) |
| `Whiteboard` model has **no `title`, `version`, `last_operation_at`, `archived_at`** | Medium | Can't track revision/archive state | Phase 4 adds these | Added |
| Frontend `version` is a **renderer invalidation counter**, unrelated to any server version | Medium | Confusion; no sync baseline | Introduce an explicit local/server version ledger in Phase 4 | Repository/sync carries its own `localVersion`/`serverVersion` |
| Undo/redo is **client-side only** via `past`/`future` stacks | Medium (documentation) | Future-collab undo mapping unclear | Document that local undo must become a new operation server-side, never row deletion | Documented in `whiteboard-versioning.md` / `whiteboard-sync-contract.md` |
| Engine `commit(op)` writes straight to local board with **no repository/persistence seam** | Medium | Can't persist without rewrite | Introduce a `WhiteboardRepository` + in-memory pending op queue | Added (frontend) |
| **No server-side operation validation** (type, payload, limits) | High | Client can persist arbitrary JSON | Phase 4: OperationValidator | Added |
| **No idempotency / dedup** of operations | High | Retried ops applied twice | Phase 4: `operation_id` unique constraint + atomic commit | Added |
| **No version/sequence discipline** for concurrent writers | High | Duplicate versions/sequences | Phase 4: row-locked atomic commit + sequence/version derived server-side | Added |
| `clear`/`remove` represented as client Ops | Low (good) | — | Persist as `CLEAR_CANVAS` / `DELETE_OBJECT` logical ops | Kept & mapped |
| No error contract / structured HTTP errors | Medium | Ambiguous client handling | Phase 4 error codes + correct status codes | Added |
| No rate limiting on draw/write endpoints | High | Abuse | Reuse `ratelimit` module on POST | Added |
| CSRF: `CSRF_COOKIE_HTTPONLY=True` means JS cannot read the token from the cookie | Medium | API POST needs CSRF token from the DOM | Serve `csrf_token` in page meta; frontend adapter sends header | Handled |

### Architecture overall
| Issue | Severity | Impact | Recommended action | Action taken |
|---|---|---|---|---|
| No documented Phase 4 API/versioning/sync contract | High (readiness) | Phase 5/6 groundwork unclear | Write the architect docs + API doc | Written |
| No performance bench for replay / op load | Medium | Unknown snapshot threshold | Phase 4 measures load+replay on realistic dataset | Performance test added + decision documented |

---

## 3. Governing decisions carried into Phase 4

1. **Whiteboard belongs to the Partnership**, never to a single user. Actions
   (operations) record `actor` per operation.
2. **Operations are the primary source of truth**; the server never stores a
   screenshot or a continuously-overwritten giant JSON state blob. It stores an
   append-only, deterministically-replayable operation ledger.
3. **Client stays decoupled from Django** via a `WhiteboardRepository`;
   persistence goes through HTTP now and WebSockets later, not into the renderer.
4. **Server is authoritative** for: actor, timestamps, version, sequence,
   resulting state, and operation validity. Client-supplied `user_id` /
   `created_at` / `resulting_version` are ignored.
5. **No real-time broadcasting in Phase 4.** No WebSockets, no Channels
   consumers, no Redis pub/sub for collaboration. Phase 4 only prepares the
   persistence/service seam Phase 5 attaches a transport to.
6. **Stale versions are detected, not auto-resolved.** Phase 4 returns a
   structured `STALE_VERSION`; real conflict resolution is Phase 5.

---

## 4. Verified Django 6.1 APIs used

- `models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)` — public ids.
- `models.JSONField` — compact per-operation payloads.
- `models.UniqueConstraint(..., condition=Q(...))` — idempotency ("one op per
  `operation_id`", "operation_id unique within whiteboard").
- `models.Index(fields=[...])` (optionally `condition=`/`opclasses=`) — ordering.
- `transaction.atomic()` + `select_for_update()` — row-locked concurrent commit.
- `transaction.on_commit(...)` — deferred post-commit side effects (documented,
  **not** wired to broadcasting in Phase 4).
- DRF: `APIView`, `Response`, `status` codes, `SessionAuthentication`,
  `IsAuthenticated`, `Throttling` avoided in favour of existing `ratelimit` (to
  match project conventions and keep one rate limiter).

---

## 5. Issue log — resolved / deferred before coding

| Issue | Severity | Resolution |
|---|---|---|
| Whiteboard lacks revision/archive fields | Medium | Added `title`, `version`, `last_operation_at`, `archived_at` (forward migration) |
| No operation model | High | Added `WhiteboardOperation` (one migration) |
| No idempotency | High | Partial unique constraint on `(whiteboard, operation_id)` + atomic get-or-create |
| No atomic concurrency discipline | High | `transaction.atomic()` + `select_for_update()` on the whiteboard row; sequence/version derived server-side under the lock |
| No validation | High | Dedicated `OperationValidator` (type, schema, ranges, size limits, forbidden fields) |
| No error contract | Medium | Central `WhiteboardAPIError`/`error_payload` helper + codes + correct HTTP status |
| No write rate limiting | High | `ratelimit.check("wb_ops", user-key, ...)` on POST |
| No repository seam | Medium | Frontend `WhiteboardRepository` + in-memory `OperationQueue` |
| CSRF token unreachable in JS cookie | Medium | Page renders `csrf_token` in a meta tag; adapter reads it and sends the header |

**Deferred deliberately (Phase 5/6):** WebSockets broadcasting, Channels consumers,
Redis pub/sub collaboration, cursors/presence, live conflict resolution, IndexedDB
offline storage, service-worker sync. Phase 4 leaves the payload / operation /
version model ready for them without introducing the transport.

---

## 6. Definition-of-done traceability

Phase 4 is complete when the items in §97 of the brief are all met; this audit and
the accompanying docs (`whiteboard-persistence.md`, `whiteboard-operations.md`,
`whiteboard-versioning.md`, `whiteboard-sync-contract.md`, `api/whiteboard.md`)
record how each is satisfied, verified by the backend/frontend suites, the
concurrency/idempotency/replay tests, the performance bench, and the final report
at `docs/architecture/phase-4-report.md`.
