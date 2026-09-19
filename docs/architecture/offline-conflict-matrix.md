# Offline Submission Policy (formerly "Offline Conflict Matrix")

**Date:** 2026-09-03
**Updated:** 2026-09-04 — rewritten to match the post-bugfix engine.
**Scope:** Phase 6 — how a locally-queued operation is submitted against a server
that may have advanced while the client was offline.

This is the offline complement to `docs/architecture/conflict-strategy.md` (which
covers *realtime* concurrent writes).

Earlier builds of Candle (Phases 6–9 and the first post-Phase-9 iteration)
classified each queued operation client-side via `decideReconciliation`, then
auto-applied "safe" ops and **quarantined** the rest into a `conflicts` store and
a "Some changes need attention" review panel. **That machinery was removed.**
`conflict-resolver.ts` is deleted, `SyncPhase` has no `conflict` state, and there
is no conflict panel in the UI. The quarantine path surfaced during routine
two-partner writing and generated 409/duplicate HTTP noise even when no real user
decision existed, so it was replaced wholesale.

## The current rule

The engine submits every pending operation against the **newest known server
version at the moment it is pushed** — an anchor refreshed at pass start and
advanced by each ack. There is no submit/rebase/reject decision and no
per-operation-type table:

| Condition | Behavior |
|-----------|----------|
| Any queued op | Submitted against the anchored live version. A stale base is impossible by construction; if a partner commits between the anchor and this submit, the server replies `STALE_VERSION` and the engine re-anchors to `current_version` and re-runs the pass. |
| Network / 0 / 5xx | Exponential backoff (`1s·2^n`, capped 30s, up to `MAX_RETRIES = 8`), then `FAILED`. |
| 429 rate-limited | Honor `retry_after` (default 10s), then retry. |
| Permanent rejection (`FORBIDDEN`, `WHITEBOARD_ARCHIVED`, other 4xx) | Operation marked `FAILED` — a visible save error the user can retry against fresh state. Never a blocking panel. |

The server remains the single authority, unchanged:

1. **Base-version validation** — a genuinely stale `base_version` is rejected
   with `STALE_VERSION` + `current_version`; the client re-anchors and retries.
2. **Idempotency** — `(whiteboard, operation_id)` unique; duplicate replies are
   `duplicate: true`. Offline retries and re-submits are safe.
3. **Authorization per request** — `FORBIDDEN` (revoked access) and
   `WHITEBOARD_ARCHIVED` reject rather than retry forever.
4. **`SYNC_REQUIRED`** — heavily diverged clients fall back to a full HTTP resync.

Terminal outcomes are `CONFIRMED` (compacted out of the queue) or `FAILED`
(preserved and surfaced as an error). Nothing is silently dropped and nothing is
parked for a manual decision.

## Legacy quarantine rows (healing only)

The `conflicts` IndexedDB store, the `CONFLICT`/`REJECTED` operation statuses,
and `SyncEngine.requeueConflicts()` / `resolveAllConflicts()` **still exist**, but
only to heal rows written by older buggy builds. On reopen, `requeueConflicts()`
moves any quarantined operation back into the pending queue, where the current
always-anchored flush resolves it: spurious conflicts (ops the server already
applied) confirm as duplicates; genuinely rejected ops (e.g. `FORBIDDEN`) become
explicit save errors. New code never writes to the conflicts store.

## The former matrix (historical reference)

For the record, the removed classifier, when the server had advanced past an
op's base, behaved as follows:

| Operation type | Former action |
|----------------|---------------|
| `create_stroke` | **rebase** (additive; replay on top of newer state) |
| `delete_object` (target known gone) | **rebase** (idempotent no-op) |
| `delete_object` (target state unknown) | **reject** → quarantine |
| `clear_canvas` | **reject** → quarantine (never auto-wipe a board that advanced) |
| `move_object` | **reject** → quarantine (kept consistent with resize) |
| `resize_object` | **reject** → quarantine (stale rebase can visibly distort) |
| `restore_version` | **reject** by default (never queued anyway) |
| Unknown type | **reject** → quarantine |

The `move_object` rebase-safety analysis (provably commutative, unlike resize's
anchor-relative math) and the "both reject for consistency" decision were the
considered alternatives. They became moot when the quarantine machinery itself
was removed; the server's no-op-on-absent-object convention (the `delete_object`
precedent, which Phase 9's move/resize follow) means a queued mutation of a
since-removed object confirms cleanly rather than needing any decision.

## Non-goals

- No automatic merge of divergent histories (full HTTP resync covers that).
- No per-op user prompt during a sync burst (and no batch review panel — see above).
- Destructive operations are never auto-applied *without a server decision*;
  the server's row-locked serialization is the only arbiter of what commits.