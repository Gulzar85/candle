# Offline-First Architecture

**Date:** 2026-09-03
**Updated:** 2026-09-04 — rewritten to match the post-bugfix engine (no
client-side conflict quarantine; see §4–§5).
**Scope:** Phase 6 — durable offline authoring and reconciliation for Candle.
**Status:** Implemented and tested (frontend unit + type + build; backend checks).

---

## 1. Principle

Candle is a private two-partner whiteboard where **PostgreSQL remains the sole
source of truth**. Phase 6 adds *offline authoring* without ever letting a local
store silently override the server. When a network drops, a partner can keep
drawing; every committed operation is written to an on-device IndexedDB queue
**before** any server traffic, and later reconciled against the server when
connectivity returns.

This document is the top-level map. Companion documents:

- `docs/architecture/sync-engine.md` — lifecycle, retry, push logic, crash recovery.
- `docs/architecture/offline-conflict-matrix.md` — offline submission/version policy.
- `docs/architecture/indexeddb-schema.md` — schema, indexes, limits, eviction.
- `docs/architecture/pwa-caching.md` — service worker strategy and privacy rules.
- `docs/architecture/phase-6-audit.md` — pre-implementation findings and decisions.

## 2. Capabilities

1. **Durable queue** — every local operation is persisted to IndexedDB before it
   is considered accepted. A browser crash or tab close does not lose in-flight
   changes.
2. **Offline authoring** — drawing works with no network. State restores from a
   logical (not pixel) cache on reopen.
3. **Automatic reconciliation** — on reconnect, pending operations are flushed
   in order against the newest server version (anchored at pass start, advanced
   by each ack). The server stays the single authority: `STALE_VERSION` re-anchors
   and re-runs the pass, `operation_id` idempotency dedupes re-submits, and a
   genuine permanent rejection marks the operation `FAILED` (a save error)
   rather than silently dropping it.
4. **No client-side conflict quarantine** — operations are never parked for
   review, and there is no conflict panel. A flush always carries the newest
   base, so the client cannot accidentally submit stale work; a permanently
   rejected operation is surfaced as an explicit error state. Legacy quarantined
   rows from earlier builds are healed into the pending queue at reopen
   (`SyncEngine.requeueConflicts`).
5. **App shell** — a conservative service worker lets the app load offline while
   never caching private, account-scoped data.
6. **Account partitioning** — all device data is scoped by `ownerKey`
   (the account's `public_id`), so one partner's data never surfaces for the other.

## 3. The offline write path

```
user draws ─▶ engine.commit (pure, local) ─▶ index.ts ─▶ SyncManager.enqueue
                                                           │
        SyncManager routes to the durable store when scoping info exists:
                                                           ▼
                                    SyncEngine.submit ──▶ WhiteboardLocalStore.saveOperation
                                                           │  (status = PENDING)
                                                           │  ◀── durability point
                                                           ▼
                                    triggerSync (debounced ─▶ runSync)
                                                           │
                                        serverVersion (from realtime controller or cached)
                                                           ▼
                                    pushPending: reconcile each op, submit ordered batch
```

Key invariant: `saveOperation` **resolves before** the UI considers the change
saved, and before the engine touches the network. If IndexedDB is unavailable or
exceeds quota, the operation is *not* reported as synced and the user sees an
error rather than a false success.

## 4. The reconcile-on-reconnect path

1. `onConnectionState(true, false)` marks the network connected and triggers a
   debounced sync pass (`SyncEngine.triggerSync`).
2. `runSync` reads pending operations (ordered by `client_sequence`) and the
   current server version (preferred: from the realtime controller's confirmed
   version; fallback: client's cached version).
3. `pushPending` submits every op **in order, one at a time**, always anchored
   to the newest known version at that moment (the pass-start anchor, advanced
   by each ack). There is no client-side classification step — no `rebase`, no
   `reject`, no quarantine. A `STALE_VERSION` reply re-anchors to the
   server-reported `current_version` and re-runs the pass.
4. Confirmed ops are compacted out of the queue once the server acknowledges
   them (their ack also advances the pass anchor).

## 5. Durability, retry, and crash recovery

- Operations move `PENDING → SUBMITTING → CONFIRMED` (compacted) on success.
- On interruption (a `SUBMITTING` op left over from a crash/tab-kill),
  `recoverInterrupted()` returns it to `PENDING` on next `initialize`.
- Network / 0 / 5xx errors retry with exponential backoff capped at 30s
  (`retry_count` up to `MAX_RETRIES = 8`). Exhausting retries marks the op
  `FAILED` (never silently dropped).
- Rate limiting (429) honors `retry_after`. `STALE_VERSION` re-anchors to the
  server-reported `current_version` and re-schedules the pass. Permanent
  rejections (`FORBIDDEN`, `WHITEBOARD_ARCHIVED`, other 4xx) mark the operation
  `FAILED` — a save error the user can retry against fresh state, never a
  blocking quarantine.

See `docs/architecture/sync-engine.md` for the full lifecycle table.

## 6. Offline restore

- A logical snapshot of the current strokes (capped at 20,000 per board) and the
  board version is cached alongside the op queue (`cacheWhiteboard`).
- On offline reopen, `loadCachedWhiteboard` restores the strokes and the app
  **re-applies any pending operations on top** (`listPendingEnvelopes` →
  `applyRemoteOperation`, idempotent by `operation_id`, ordered by
  `client_sequence`), so the canvas matches what the user last saw plus everything
  they drew offline. The snapshot alone does not include offline-authored strokes;
  the pending-queue replay restores them.

## 7. Security and partitioning

- **No server authority shift.** Every local op is revalidated by version/base
  checks and idempotency keys on the server. The client can only *defer* the
  decision to the server, never assert it.
- **Scoped storage.** Every record carries `owner_key` (the account's
  `public_id`). All reads are filtered by the owner partition, which is what
  keeps User A's cache from ever surfacing to User B on a shared device.
- **Logout policy: preserve, never destroy.** Logging out does **not** clear
  IndexedDB — `SyncEngine.clearLocalData()` / `hasUnsyncedWork()` exist as
  primitives (`local-store.ts`) but are deliberately **not** wired to the
  logout action. Clearing on logout would be the *unsafe* choice here: a
  user with unsynced offline strokes who logs out (deliberately or via
  session expiry) must not have that work deleted before it ever reached the
  server. Because `owner_key` is the account's stable `public_id` (not a
  session id), the same account logging back in on the same device resumes
  exactly where it left off and the pending queue syncs normally — this is
  the "keep local pending data associated with the account" policy the
  offline spec allows as an alternative to blocking logout outright.
  A proactive "you have unsynced changes" confirmation *before* logout would
  need `hasUnsyncedWork()` reachable from every page with a sign-out control
  (the account menu, Settings), which today would mean loading
  whiteboard-only IndexedDB code into pages that never otherwise need it.
  Worth revisiting (e.g. via a small `localStorage` flag the whiteboard page
  maintains) if this becomes a real point of user confusion; deferred for now
  since the underlying data-loss risk it would prevent doesn't actually
  exist — nothing is destroyed either way.
- **Service worker never caches API responses** or authenticated HTML. Offline API
  calls return a terse `OFFLINE` response rather than fabricated data. This
  prevents cross-account data leakage on a shared device.

## 8. Non-goals (explicit)

- No CRDT / conflict-free replicated data type. Clients never decide merge
  semantics: they push against the newest server version, and the server is the
  sole authority on what commits (base-version validation + idempotency). See
  `docs/architecture/offline-conflict-matrix.md`.
- No op-based vectored merge for arbitrary divergent histories — heavily diverged
  boards recover via a full HTTP resync
  (`STALE_VERSION` → `sync.ops` replay).
- No collaborative undo coordination across offline peers (documented limitation).
- No screenshot-snapshot persistence; offline keeps the logical op/substrate model.

## 9. Module map

| Module | Responsibility |
|--------|----------------|
| `whiteboard/idb.ts` | Promise-based IndexedDB wrapper (open/upgrade/read/write helpers). |
| `whiteboard/local-store.ts` | `WhiteboardLocalStore` — sole typed IndexedDB access; schema, indexes, limits, eviction. |
| `whiteboard/sync-engine.ts` | `SyncEngine` — durable queue owner, always-anchored push, retry, crash recovery, legacy-conflict healing. |
| `whiteboard/sync.ts` | `SyncManager` — public facade; durable-first, HTTP-only fallback when unscoped. |
| `whiteboard/network-monitor.ts` | Connectivity state (navigator + transport signals). |
| `whiteboard/client-id.ts` | Persistent installation client ID. |
| `static/sw.js` | Service worker — app-shell-only caching, network-first navigation/API. |

## 10. Verification

- Frontend unit tests run in Node against an in-memory fake store (no IndexedDB
  in Vitest): `sync-engine.test.ts`, `sync.test.ts` (HTTP-only fallback),
  `network-monitor.test.ts`.
- `tsc --noEmit` clean; `vite build` succeeds; `python manage.py check` clean.
- Browser-level offline/restart/revocation checks are documented as a manual
  pass in `docs/phase-6-completion-report.md`.