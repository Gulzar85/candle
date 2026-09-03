# Realtime Collaboration — Architecture

**Phase 5 — How the whiteboard coordinates two editors at low latency**

Candle uses a **server-authoritative operation log** with **optimistic local
apply** over WebSockets. PostgreSQL remains the source of truth; the WebSocket
is a fast, reliable transport for committed operations and presence.

This document explains the end-to-end flow: how a local stroke reaches a
partner, and how two editors converge without every keystroke blocking on a
round trip.

---

## Architecture at a glance

```
                ┌────────────────────────────────────────────┐
                │                 Browser A                  │
   local draw   │  engine ── onServerOperation               │
        ──►     │  ├─► [ready?] ├─► controller.submit (WS)   │  ══╗  WebSocket
                │  └─► [else]    └─► SyncManager.enqueue(HTTP)│    ║
                │  ◄── applyRemoteOperation ◄── controller    │ ◄══╝
                └────────────────────────────────────────────┘    │
                                                                   v
                ┌────────────────────────────────────────────┐   channel layer
                │               Django / Channels            │   (Redis ≥ 6 prod;
                │   WhiteboardConsumer ── group whiteboard.x  │   in-memory dev/test)
                │     └─ WhiteboardOperationService (sync)    │
                │     └─ WhiteboardOperation ledger (Postgres)│
                └────────────────────────────────────────────┘
                                                                   ║
                                                                   v
                ┌────────────────────────────────────────────┐
                │                 Browser B                  │  ◄── receives
                │  controller.onMessage ──► engine            │      committed ops
                └────────────────────────────────────────────┘
```

## Write path (optimistic)

1. The user finishes a gesture. The **engine** folds it into the local board
   immediately (no waiting), then calls `onServerOperation`.
2. `index.ts::submitLocal` routes to the **`RealtimeController`** when the
   socket is `ready`; otherwise it falls back to the Phase 4 **`SyncManager`**
   (HTTP queue with retry/save-status). This makes collaboration low-latency and
   resilient: the socket is primary, HTTP is the safety net.
3. The **controller** assigns the submission's `base_version` from its confirmed
   server version plus how many of its own operations are still in flight
   (`base = confirmed + inFlight`). This produces the correct **version chain**
   for gestures that expand into several operations.
4. The **consumer** hands the frame to `WhiteboardOperationService`, which
   validates, row-locks, versions, sequences, and persists it — all in one
   transaction.
5. Only *after commit* the consumer broadcasts `operation.committed` to the
   group (`whiteboard.<partnership_public_id>`).

## Receive path (remote apply)

6. The partner's **controller** receives `operation.committed`. If it is not
   already applied (dedupe by `operation_id`) it calls `engine.applyRemoteOperation`,
   which maps the server operation to a local reversible `Op` and renders it.
   Ours are already applied optimistically, so they are skipped.
7. `operation.committed` also advances the confirmed version on both sides.

## Sync (catch-up & reconnect)

- On connect and re-connect the client sends `sync.request {version}` and
  applies the returned `sync.ops` in order. Applying is idempotent by
  `operation_id`, so replays never double-render.
- On `STALE_VERSION` the controller clears its pending set, adopts the server's
  `current_version`, requests a sync, and reports the condition to the UI.
- If a client is too far behind the server replies `sync.required`; the client
  falls back to a full HTTP state reload.

## Presence

The server broadcasts `presence.joined` / `presence.left` / `presence.update`
(cursor). The consumer **self-excludes** frames originating from the same user,
so you never see your own cursor. The frontend renders each partner's cursor
from normalized coordinates and a live connection indicator in the status bar.

---

## Frontend module map

| Module | Responsibility |
|---|---|
| `realtime/types.ts` | Wire contract (message shapes, error codes, states). |
| `realtime/transport.ts` | Raw WebSocket: connect, backoff reconnect, heartbeat, size cap, frame dispatch, state (`connecting/open/reconnecting/closed`). |
| `realtime/controller.ts` | Collaboration brain: state machine (`connecting→connected→syncing→ready`), confirmed version, dedupe by `operation_id`, base-version chaining, submit/apply, presence relay, stale recovery. |
| `engine.applyRemoteOperation` | Map a server op to a reversible local `Op` and render, **without** re-emitting it to the server. |
| `index.ts` | Glue: build the WS URL, route `onServerOperation` to WS-primary / HTTP-fallback, render connection status + cursors. |

## Backend module map

| Module | Responsibility |
|---|---|
| `routing.py` | `websocket_urlpatterns` for the consumer. |
| `realtime.py` | Protocol constants, safety limits, message builders. |
| `consumers.py` | Origin/authn/authz, per-message re-auth, size + rate limits, group relay, presence self-exclusion, `sync.ops` replay. |
| `service.py` | The authoritative sync write path (row lock, versioning, sequencing, idempotency). |

---

## Config

- `config/asgi.py` → `ProtocolTypeRouter`: `http` = Django app, `websocket` =
  `AuthMiddlewareStack(URLRouter(whiteboard_routing))`.
- `CHANNEL_LAYERS`: **in-memory** channel layer in dev/tests
  (`config/settings/development.py`); production must use Redis ≥ 6
  (`channels_redis`). Local Redis 3.2 is incompatible with redis-py 8
  (`HELLO` command) and is **not** supported.
- `WEBSOCKET_ALLOWED_ORIGINS` — origin allowlist (see `websocket-protocol.md`).

## Versus Phase 4 (HTTP)

| Concern | Phase 4 (HTTP) | Phase 5 (WebSocket) |
|---|---|---|
| Latency | Submit → ack → render | Optimistic local render; committed broadcast |
| Multi-partner | One at a time via polling/full reload | Group broadcast in real time |
| Presence | None | cursors + roster via socket |
| Source of truth | Postgres | Postgres (unchanged) |
| Fallback | — | HTTP sync on socket loss; WS on HTTP-less room |

## Explicitly deferred

- Collaborative undo / CRDT / OT (Yjs) — deferred; the operation ledger + replay
  is the chosen model (see `conflict-strategy.md`).
- Offline-first / IndexedDB — Phase 6.
- Full eraser-path CRDT fidelity across clients — remote apply maps by
  `object_id`; eraser-suffixed segment reconciliation on heavily-diverged
  boards is covered by full HTTP resync.