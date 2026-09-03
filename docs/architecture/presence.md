# Presence & Connection Status

**Phase 5 — Who is here, where they are, and the socket health indicator**

Presence is delivered over the same whiteboard WebSocket as operations, so it is
stale only when the socket is (no extra channel). Connection health and roster
are surfaced in the status bar.

---

## Presence data model

| Frame | `user` | extra |
|---|---|---|
| `presence.joined` | `{public_id, display_name}` | — |
| `presence.left` | `{public_id, display_name}` | — |
| `presence.update` | `{public_id, display_name}` | `cursor: {x, y}` (normalized 0..1) |

`public_id` is `User.public_id` (stable, non-`pk`); `display_name` is the
partnership-safe name from `safe_display_name` — never an email.

## Who appears to whom

- On connect, the server broadcasts `presence.joined` to the group.
- On disconnect (including the browser closing the tab), it broadcasts
  `presence.left`.
- The consumer **self-excludes** frames where `user.public_id == self`, so a
  client never renders its own cursor or roster entry. Only the *partner*
  appears.

## Cursor coordinates

Coordinates are **normalized to the stage** (0..1 across width/height), not world
or screen pixels. The receiving client positions its partner cursor at
`x% / y%` of its own stage, so the cursor lands at the same *relative* place
regardless of either partner's zoom or pan. The client coalesces cursor sends to
~20 Hz.

## Frontend UI

- **Connection indicator** (`#wb-realtime-status`): dot + label reflecting the
  derived connection state:
  `connecting → connected → syncing → ready` (green "Live · N online"),
  `reconnecting`, `closed` (red "Offline").
- **Partner cursors**: an absolutely-positioned avatar dot per partner,
  created on `presence.joined`, moved on `presence.update`, removed on
  `presence.left`.

## State machine

```
connecting ──(transport open)──► connected ──(sync.request sent)──► syncing
syncing ──(sync.ops applied)──► ready
ready ──(transport drop)──► reconnecting ──(open)──► connected ──► syncing ──► ready
any ──(max retries reached / manual close)──► closed
```

`transport.state` is coarse (`connecting/open/reconnecting/closed`); the
`RealtimeController` derives the richer `RealTimeConnectionState` by adding the
syncing-outcome. `open + syncing == "syncing"`, `open + !syncing == "ready"`.

## Privacy & limits

- Presence is only visible to the two authorized partners on the active
  partnership.
- `display_name` is sanitized; raw emails/ids are never emitted.
- Cursor traffic is bounded client-side (~20 Hz) and server-side by the general
  message rate limit (600 frames / 60 s per connection).