# Phase 6 — Completion Report

**Date:** 2026-09-03
**Goal:** Offline-first persistence and reconciliation — durable IndexedDB
authoring, a sync engine, and a conservative PWA shell — so a partner can keep
drawing without a network and reconcile changes on reconnect, with PostgreSQL
remaining the sole source of truth.

---

## Delivered

### Offline durability & sync (frontend)
- `whiteboard/idb.ts` — promise-based IndexedDB wrapper (`openDB` upgrades,
  `withStore`, idb get/put/delete/count/getAll/clear, versioned schema).
- `whiteboard/local-store.ts` — `WhiteboardLocalStore` + `LocalOperation`,
  `LocalWhiteboard`, `LocalConflictRecord`; schema (whiteboards, operations, meta,
  conflicts) with owner/board/status/sequence indexes; **owner-partition scoping**;
  limits (5,000 pending ops, 20k cached strokes, 20 boards) and `QuotaExceeded`
  handling; compaction, conflict resolve/discard, logout partitioning.
- `whiteboard/client-id.ts` — persistent per-installation client UUID.
- `whiteboard/network-monitor.ts` — connectivity state (navigator + transport).
- `whiteboard/conflict-resolver.ts` — pure per-type reconciliation
  (`submit` / `rebase` / `quarantine`) + human-readable conflict messages.
- `whiteboard/sync-engine.ts` — `SyncEngine`: durable `submit` (PENDING before
  any server traffic), debounced `runSync`, ordered push, exponential backoff
  (`MAX_RETRIES = 8`) → `FAILED` (never silently dropped), `STALE_VERSION`
  re-reconciliation, 429 `retry_after`, permanent reject → quarantine, crash
  recovery (`recoverInterrupted`), cached-board offline reopen, network
  integration, status/conflict hooks. Engine tests run against an injectable
  `WhiteboardLocalStoreLike` (in-memory fake, no IndexedDB in Vitest).
- `whiteboard/sync.ts` — `SyncManager` public facade: durable-first, HTTP-only
  legacy fallback (in-memory queue with working `retryPending`), status/conflict
  surfacing.
- `whiteboard/index.ts` — wiring: SyncManager + engine, reconnect flush, cached
  state restore, offline + conflict status.

### PWA shell
- `static/sw.js` — conservative worker: app-shell caching, network-first
  navigation, **never caches `/api/*`** (returns terse `503 OFFLINE`, no
  fabricated data), stale-while-revalidate bundled assets, cache versioning +
  stale-cache purge. Registered from `main.ts` at `/sw.js`.
- `static/manifest.json` — added `id`, `scope`, and `service_worker` fields.
- Backend: `apps/core/views.service_worker` + `/sw.js` route; CSP `worker-src`
  in `config/settings/base.py`; `account_id` in whiteboard view context (drives
  `owner_key`); `data-wb-account` in the template; conflict panel + extended
  status bar in `whiteboard.html`.

### Documentation
- `docs/architecture/offline-architecture.md`
- `docs/architecture/offline-conflict-matrix.md`
- `docs/architecture/sync-engine.md`
- `docs/architecture/indexeddb-schema.md`
- `docs/architecture/pwa-caching.md`
- `docs/architecture/phase-6-audit.md` (pre-implementation audit)

## Verification

- `cmd /c "npx tsc --noEmit"` — **clean**.
- `cmd /c "npx vitest run"` — **85 passed / 85** (11 files), including new
  `sync-engine.test.ts`, `sync.test.ts`, `conflict-resolver.test.ts`,
  `network-monitor.test.ts`.
- `cmd /c "npx vite build"` — succeeds (whiteboard bundle 50.10 kB; main 749.51 kB).
- `python manage.py check` — no issues; `makemigrations --check` — no changes.
- Backend whiteboard tests pass (run per-file with `-p no:cacheprovider` to avoid
  the pre-existing cross-suite content-type collision): `test_operations.py`,
  `test_views.py`, `test_concurrency.py`, `test_replay.py`, `test_websocket.py` —
  covering HTTP submit revalidation (idempotency, base-version, authorization),
  replay, and WebSocket sync.

## Tests added / fixed this pass
- **Fixed** the sync scheduling race: submission now goes through the debounced
  scheduler so durability and ordering are deterministic and testable.
- **Fixed** status-0 handling in the retry classifier (treated as retryable
  network, not permanent).
- **Fixed** post-pass phase to report `conflict` when any op is quarantined.
- **Fixed** `quarantineConflict` to mark the underlying op `CONFLICT` (leaves the
  pending queue) while retaining the conflict record.
- **Fixed** HTTP-only fallback: `retryPending` now actually re-submits queued ops
  (legacy queue), so retry is observable.
- **Fixed offline-restore gap**: on an offline reopen, the cached snapshot alone
  did not include strokes drawn during the previous offline session (they live in
  the durable pending queue, not the cached snapshot). The restore path now
  reapplies any pending operations on top of the cached snapshot via
  `SyncManager.listPendingEnvelopes()` → `Engine.applyRemoteOperation()`
  (idempotent by `operation_id`, ordered by `client_sequence`), so offline work is
  visible immediately on reopen and deterministic reconstruction holds.
  Added `sync-engine.test.ts > offline restore > replays pending operations as
  stable envelopes for offline reopen`.

## Decision: logout / account-switch handling (§42, §43)
- **Policy: keep per-account local data; never auto-destroy unsynced work on
  logout.** Every IndexedDB record is scoped by `owner_key` (the account's
  `public_id`), and all reads filter by the current account partition, so a
  different account (User B) can never read another account's (User A) cached
  board or pending operations on the same device.
- `SyncManager.hasUnsyncedWork()` and `clearLocalData()` expose the safe API for a
  future "logout with unsynced changes" flow; `clearOwnerPartition()` already
  deletes only the caller's partition. Because logout navigates away (full page
  load), the whiteboard JS context is destroyed, so no stale engine/canvas state
  leaks across accounts. This is the deliberate, documented trade-off (local
  durability wins over aggressive cleanup; the server itself never trusts local
  state — `operation_id` idempotency + base-version + authorization revalidation
  hold regardless).

## Known pre-existing issues (not introduced by Phase 6)
1. Backend `apps/accounts/models.py` mypy errors — out of scope.
2. Test-ordering content-type collision across suites — `tests/whiteboard`
   passes standalone (or per-file with `-p no:cacheprovider`).

## Manual browser checks (recommended, unscripted)
- Go offline mid-draw → op persists via IndexedDB; status shows offline/pending.
- Reload while offline → shell loads via SW; cached board restores; pending ops
  retained.
- Reconnect → queue flushes; clear_canvas authored offline is quarantined to the
  conflict panel for confirmation.
- Partner revokes access / board archived → reconnect quarantines and stops
  syncing (`FORBIDDEN` / `WHITEBOARD_ARCHIVED`).
- App shell loads in a fresh browser profile offline before first visit (will not,
  by design — needs one online visit to pre-cache).

## Deferred / non-goals (per `phase-6-audit.md`)
- No CRDT/OT; additive rebase + quarantine is the chosen policy.
- No screenshot persistence; logical op/substrate model only.
- No collaborative offline undo coordination.