# Offline Conflict Matrix

**Date:** 2026-09-03
**Scope:** Phase 6 — how a locally-queued operation is reconciled against a server
that advanced while the client was offline.

This is the offline complement to `docs/architecture/conflict-strategy.md` (which
covers *realtime* concurrent writes). Offline reconciliation reuses the same
principles — server-authoritative versions, idempotency by `operation_id`,
deterministic replay — but adds a **deferral** layer: an operation authored
offline is not merely retried, it is **classified first** by a per-type policy.

The server remains the final authority. The client only decides whether to
submit, rebase, or quarantine. The policy lives in
`whiteboard/conflict-resolver.ts` (`decideReconciliation`).

---

## The decision function

```
decideReconciliation({ operation_type, base_version, server_version, ...extras })
```

Returns one of:

- **`submit`** — `server_version === base_version`. No divergence; submit as-is.
- **`rebase: { newBaseVersion }`** — the op is safe to replay on top of the newer
  server state; its `base_version` is bumped to the current server version before
  submission.
- **`reject`** — unsafe to auto-replay; **quarantined** for explicit review
  (never silently discarded, never blindly applied).

---

## Matrix

Base cases first: if `server_version === base_version`, every op type is
`submit` (no conflict). The matrix below is only for when the server has advanced
past the op's base (i.e. `server_version > base_version`).

| Operation type | Behavior when server advanced | Action | Rationale |
|----------------|-------------------------------|--------|-----------|
| `create_stroke` | Additive, commutes with concurrent strokes | **rebase** | New strokes do not invalidate an added stroke; replay on top and bump base. |
| `delete_object` (target known gone) | Target no longer exists (`cached_object_exists === false`) | **rebase** | Server deletion is idempotent; replaying is a safe no-op. |
| `delete_object` (target state unknown) | May still exist; we cannot verify | **reject** | Deleting something a partner modified would lose their edits; defer to review. |
| `clear_canvas` | Destructive; could wipe newer changes | **reject** | Never auto-wipe a board that advanced while offline. |
| Unknown type | Cannot reason about safety | **reject** | Conservatively defer. |

---

## Why each unsafe case is quarantined, not retried

- **`clear_canvas`** — replaying onto a board that advanced while the partner was
  offline could erase their newer work. The product explicitly forbids
  auto-applying a destructive clear. The user confirms the clear in the conflict
  panel, or the op is discarded.
- **`delete_object` with unknown target state** — if the partner edited the same
  object, re-applying the delete would drop their edits silently. Without a
  cached snapshot confirming the object is gone, we default to review.

Both are stored in the `conflicts` store and surfaced in the reconcile UI. The
underlying operation is marked `CONFLICT` (removed from the pending queue but
never deleted), so it can be re-submitted or discarded later.

---

## Server-side guard rails that hold regardless

Even when the client *does* submit or rebase, the server enforces:

1. **Base-version validation** — a stale `base_version` is rejected with
   `STALE_VERSION` + `current_version`; the engine re-runs reconciliation.
2. **Idempotency** — `(whiteboard, operation_id)` unique; duplicate replies are
   `duplicate: true`. Offline retries are safe.
3. **Authorization per request** — `FORBIDDEN` (revoked access) and
   `WHITEBOARD_ARCHIVED` quarantine rather than retry forever.
4. **`SYNC_REQUIRED`** — heavily diverged clients fall back to a full HTTP resync.

## Human-facing conflict messages

`describeConflict` renders a non-technical, per-type message for the UI:

| Type | Message shape |
|------|---------------|
| `create_stroke` | "A stroke you drew offline can be added on top of N newer change(s)." |
| `delete_object` | "A deletion you made offline needs review because the board changed N time(s)…" |
| `clear_canvas` | "You cleared the board offline, but it has changed N time(s). Confirm before applying the clear." |

## Non-goals

- No automatic merge of divergent histories (full HTTP resync covers that).
- No per-op user prompt *during* a sync burst; conflicts batch into a panel
  reviewed after reconnect.
- Destructive ops are never auto-accepted based on a heuristic when server
  state advanced.