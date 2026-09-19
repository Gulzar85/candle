# Whiteboard Performance Benchmarks

## Status

**Real numbers, measured locally on 2026-09-04 — not production capacity
figures.** These were captured on a single developer machine against the
local dev PostgreSQL instance, single connection, no concurrent load, no
network latency between application and database. They tell you the shape
of the cost curve (does replay time grow linearly? where does it start to
matter?) — they do not tell you what a production deployment under real
concurrent load will measure. Concurrent-user throughput and WebSocket
fan-out latency at scale require an actual deployed target to measure
honestly and are explicitly **not** included here (see
`docs/production-readiness-report.md` for what remains deferred).

## Method

A throwaway partnership/whiteboard/user set was created, `N` synthetic
`create_stroke` operations were bulk-inserted directly via the ORM (isolating
DB + replay cost from HTTP/validation overhead, which is already covered
separately by `tests/whiteboard/test_operations.py`), then the exact
production code path was timed: fetch all operations ordered by `sequence`,
then `WhiteboardStateBuilder.replay()` (`apps/whiteboard/
state_reconstruction.py` — the same function `WhiteboardStateView` calls to
serve `GET /api/whiteboards/<id>/`). All test data was deleted immediately
after each run and verified gone (`SELECT count(*) FROM accounts_user WHERE
email LIKE 'bench-%'` → 0).

## Results

| Operations | Bulk insert | Fetch (ordered query) | Replay (state reconstruction) | Objects reconstructed |
|---:|---:|---:|---:|---:|
| 100 | 27.9 ms | 11.4 ms | 0.1 ms | 100 |
| 1,000 | 104.2 ms | 19.5 ms | 1.6 ms | 1,000 |
| 5,000 | 608.9 ms | 147.1 ms | 7.9 ms | 5,000 |

(Insert time is not representative of real usage — real operations arrive
one at a time via the HTTP/WebSocket submission path, not as a single bulk
insert; it's included for completeness of what was measured, not as a
capacity claim.)

## What this tells us

- **Replay itself is fast and stays fast.** 5,000 operations replay in under
  8ms — `WhiteboardStateBuilder` is a simple in-memory dict/list walk (see
  `docs/architecture/whiteboard-operations.md`), and that cost scales
  linearly with operation count with a small constant factor. This is not
  the bottleneck at these sizes.
- **The fetch query is the larger, and more variable, cost** — 147ms at
  5,000 rows on local hardware, growing faster than linearly between the
  1,000 and 5,000 data points (19.5ms → 147ms is more than 5×, for a 5×
  increase in row count). This is the number worth watching as boards grow,
  not replay time.
- Both numbers are still small in absolute terms at 5,000 operations — a
  whiteboard would need to grow well past what's typical for a
  two-person collaborative surface before this became a user-visible delay,
  but "well past typical" isn't the same as "will never happen," which is
  exactly why the existing indexes (`idx_op_board_seq` — wait, corrected:
  the `(whiteboard, sequence)` unique constraint's backing index, since the
  redundant explicit one was removed this pass — see
  `docs/architecture/phase-8-production-audit.md` DB-5) matter for keeping
  the fetch query's growth curve as flat as possible.

## Snapshotting / compaction decision (DP-1, SC-1)

**Decision: not implemented, and the threshold for revisiting this is
concrete, not vague.** `MAX_RETAINED_OPERATIONS = 0` (unlimited) remains the
default. Based on the measurements above:

- Replay cost is not the reason to add snapshotting at any board size these
  numbers suggest is realistic for this product (a private two-person
  whiteboard, not a large team canvas).
- The fetch query cost is the metric to actually watch. If a real
  deployment's monitoring (`docs/operations/monitoring.md`) shows the
  `WhiteboardStateView` (`GET /api/whiteboards/<id>/`) response time growing
  noticeably for any board — a concrete, observable trigger, not a
  round-number guess — that specific board is the one to consider
  snapshotting first (not a blanket migration), given the operation model
  already supports it cleanly: `WhiteboardStateBuilder.replay(operations,
  start=<snapshot state>)` already accepts an optional starting state
  (`apps/whiteboard/state_reconstruction.py`), so adding a snapshot table
  later doesn't require rewriting the replay logic — only adding a
  `WhiteboardSnapshot` model and changing what gets passed as `start`.
- No object-count cap per board (SC-1) has been added either, for the same
  reason: nothing measured here suggests it's currently the bottleneck, and
  adding an arbitrary cap risks limiting legitimate use before there's
  evidence it's needed. Worth adding if the fetch-query-growth signal above
  is ever observed in practice.

## Deferred to actual deployment

- Concurrent-user throughput (multiple simultaneous WebSocket connections
  drawing at once) — requires a real deployed target and multiple real
  client connections, not something a single local process can honestly
  measure.
- Network latency's contribution to perceived draw-to-broadcast latency —
  local loopback has effectively zero network latency, which is not
  representative of any real deployment.
- Database performance under realistic production hardware/configuration
  (connection pooling behavior, disk I/O characteristics) — this repo's
  local dev Postgres instance is not a stand-in for a production database
  server's actual capacity.
