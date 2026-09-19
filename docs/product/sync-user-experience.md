# Sync User Experience

How the app communicates the offline-first sync engine (Phase 6) and the reactive
realtime layer (Phase 5) to the user, and what each status means.

## Online + realtime (the happy path)

The status bar shows a live dot + "Live" with presence count. Operations you
draw are submitted over the WebSocket immediately and acknowledged by the
server. Your partner's edits apply in near real time; their cursor initials
follow their pointer.

## Saving / syncing states

The save indicator has four visible states:

| Save state | Status text | Dot |
| --- | --- | --- |
| Saved | "Saved" / "All changes synced" | green |
| Saving | "Saving…" | amber (pulse) |
| Pending | "Changes waiting to sync" | muted |
| Error | "Save failed" + **Retry** | red |

The label never claims "Saved" while unsynced local changes exist — accuracy over
reassurance. The **Retry** button appears only when an error left operations
pending.

## Offline (write-ahead, never silent loss)

When the network is down, the server returns **503** and the SyncManager treats
the connection as offline:

- Your strokes are written to IndexedDB **before** submission (durable).
- The status shows "Saved on this device · Waiting for connection" (amber).
- On reconnect, `SyncManager` flushes the durable queue in order.

Strokes you make offline still replay immediately onto your canvas, so you can
keep working.

## Sync errors (no conflict panel)

Candle does **not** show a "conflict" panel. Offline work is pushed against the
newest server version, so a stale baseline is impossible by construction; if a
partner's edit races ahead mid-submit, the engine re-anchors and retries
automatically. Genuinely rejected operations (e.g. access revoked, board
archived) surface only as a save error in the status bar — never a blocking
review flow:

- The error names the remedy (Retry) rather than just what failed.
- Nothing is silently dropped: permanently rejected operations stay preserved as
  `FAILED` until a retry succeeds against fresh state.
- Reopening the board heals any quarantined rows left by older builds idempotently
  by `operation_id`.

## Realtime connection states

| State | Meaning |
| --- | --- |
| Connecting | WebSocket handshaking |
| Connected | Open, before ready |
| Syncing | Replaying/forwarding |
| Ready | Live ("Live · N online") |
| Reconnecting | Backoff after disconnect |
| Offline | Closed (no realtime) |

Presence is **ephemeral** — cursors exist only in-memory on the connected group
and are never persisted.

## Copy guidelines

- Never claim success while unsure ("Saved" only when acked).
- Destructive actions (clear board) always confirm.
- Error states name the remedy (Retry) rather than just what failed.