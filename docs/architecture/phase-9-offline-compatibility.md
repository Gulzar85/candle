# Phase 9 Offline Compatibility

Every Phase 9 feature's offline behavior, stated explicitly rather than
assumed. Verified against the actual implementation, not aspirational.

| Feature | Offline supported | Queueable | Conflict strategy | Notes |
|---|---|---|---|---|
| Move object | Yes | Yes | Re-anchored flush | Same durable IndexedDB queue as every operation. No IndexedDB schema change was needed — move doesn't introduce a new object type, only a new mutation op on an existing stroke object. |
| Resize object | Yes | Yes | Re-anchored flush | Same as move. Degenerate-bbox handling (`geometry.ts`) is entirely client-side math, works identically offline. |
| History (viewing) | Yes, from cache only | N/A (read-only) | N/A | The history *panel* can be opened offline, but `WhiteboardRepository.loadHistory()` requires a network round-trip — offline, the fetch simply fails and the panel shows whatever was already loaded (or nothing, if opened for the first time while offline). Not a queued/retried read; the user can just reopen it once back online. |
| Restore | **No** | No | N/A — always submitted online, never queued | See below. A real, stated scope boundary, not a gap. |
| Export (PNG/JSON) | **No** | No | N/A | Both export paths fetch fresh server state first (`repository.loadState()`), deliberately — exporting stale local/cached state instead of the true current server state would be a correctness problem, not a convenience worth preserving offline. |
| Import | **No** | No | N/A | Requires an authenticated round-trip to `POST /api/whiteboards/<id>/import/`; not designed to be queued (an import can be up to `MAX_IMPORT_OBJECTS` = 2,000 objects — queuing that behind the same durable-operation-queue machinery built for one-stroke-at-a-time drawing was judged not worth the complexity for a rare, deliberate, already-online-gated action). |

## Why restore has no offline path

Restore fundamentally cannot be computed client-side while offline: it
requires replaying the full server-side ledger up to a historical
`target_sequence`, which an offline client does not have cached.
IndexedDB's `whiteboards` object store caches only the **current**
flattened stroke list (`LocalWhiteboard.strokes`), not the operation
history needed to reconstruct an arbitrary earlier point.

**UI behavior**: the history panel's "Revert" button is disabled while
offline (`HistoryPanel.renderEntries`, checked again defensively in
`revertTo()` before actually submitting), with a visible note in the panel
("Revert requires an internet connection"). This is an inherent boundary of
the feature as designed, not a bug to fix later — queuing a restore request
offline would mean submitting it against a `base_version`/`target_sequence`
pair that could be arbitrarily stale by the time connectivity returns, with
no good way for the client to know if the resulting revert still makes
sense to the user.

## Interaction with the offline submission policy

`move_object`/`resize_object`/`restore_version` need **no special offline
policy**: the engine's always-anchored flush is type-agnostic. Every queued op —
including Phase 9's three new types — is submitted against the newest known
server version; a `STALE_VERSION` reply re-anchors and the pass re-runs, and
nothing is classified, rebased, or quarantined client-side (`conflict-resolver.ts`
was removed during Phase 9). The deliberately-not-taken alternative from earlier
builds — a client-side `reject` for `move_object`/`resize_object`, reasoned
because a stale rebase of the resize anchor can produce a visibly distorted
result — became moot when the quarantine machinery itself was removed: the
server's no-op-on-absent-object convention (the `delete_object` precedent, which
Phase 9's move/resize follow) means a queued mutation of a since-removed object
confirms cleanly rather than needing a decision.

`restore_version` has no offline path by design — restore is always submitted via
its own dedicated, online-only endpoint (`POST /api/whiteboards/<id>/restore/`),
never queued in `SyncEngine`.

## Reconnect / catch-up behavior

An offline client reconnecting after a restore happened while it was away
recognizes the `restore_version` operation in the `sync.ops` catch-up gap
(the stripped/lightweight payload is enough to identify the type — see
`docs/architecture/whiteboard-history.md`) and does one full state refetch
instead of trying to incrementally replay it. This check lives in exactly
one place — `frontend/src/whiteboard/index.ts`'s `onApplyRemote`
callback — since both the live-broadcast path and the reconnect-catch-up
path in `RealtimeController` funnel through that same callback.

The client's own locally-queued pending ops (drawn while offline, unrelated
to the restore) are unaffected by any of this and flush through the regular
`SyncEngine` path: `create_stroke` lands on top of the restored board
(correct, since restore never deletes ledger rows); `move_object`/
`resize_object`/`delete_object` targeting an object the restore removed
confirm as server-side no-ops (the same idempotency convention as
`delete_object` on a missing target). Zero restore-specific frontend sync
code was needed for this case.
