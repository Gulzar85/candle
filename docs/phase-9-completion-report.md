# Phase 9 Completion Report

Date: 2026-09-04. Scope: Product Expansion — Whiteboard History, Restore,
Object Manipulation, Export/Import.

## How to read this report

Every claim below traces to a file:line reference, a named test that was
actually run, or an explicit measured number. Nothing here is a fabricated
benchmark, an unverified "it should work," or a claimed test pass that
wasn't actually executed. Where something wasn't verified (no live-browser
smoke test was possible in this environment), that's stated plainly under
Known Limitations, not implied away.

## Scope decision, recorded

The original spec covered a much larger surface than shipped (version
history, object manipulation, attachments, export/import, sharing, board
duplication/templates, comments, activity feed, search, presentation mode,
minimap, advanced shapes). Before implementation began, four genuine
architecture forks were put to the user as explicit scope questions, and
the minimal/recommended option was chosen on every one: no attachments, no
multi-board support, no sharing changes, and single-object move/resize
only (no multi-select, no grouping). See
`docs/product/phase-9-features.md` for the full reasoning per deferred
feature.

## Features Implemented

1. **Whiteboard version history** — a slide-in panel showing humanized,
   newest-first entries ("You added a drawing", "Partner cleared the
   board"), never raw operation ids/sequences. Folded together with
   "activity feed" (a separate item in the original spec) since both would
   be near-duplicate views over the same ledger in this codebase.
2. **Restore to an earlier version** — a new, forward-moving
   `restore_version` operation. Never deletes or rewrites history.
3. **Single-object select, move, resize** — extends the existing select
   tool with drag-to-move and 8-handle resize, including correct handling
   of degenerate (near-zero-extent) bounding boxes.
4. **Export** — PNG (client-side, fit to content) and JSON (versioned,
   re-importable envelope).
5. **Import** — validated JSON import, additive by default with an
   optional clear-first replace, converted into a real operation sequence
   through the same validation every hand-drawn stroke goes through.

## Features Deliberately Not Implemented

See `docs/product/phase-9-features.md`'s table for the full reasoning.
Summary: image/attachment support (needs a new upload path and a storage
decision), multi-board support (requires lifting a structural
`OneToOneField` constraint), sharing/access beyond two people (the
two-person model is deliberately, structurally enforced), comments,
multi-select/grouping, search, presentation mode, minimap, advanced
shapes.

## Architecture Changes

- Three new operation types, appended (never renamed) to
  `WhiteboardOperationType`: `move_object`, `resize_object`,
  `restore_version`.
- `WhiteboardStateBuilder.apply()` gained the first "patch an existing
  object" mutation semantics — every prior operation type either fully
  replaced or fully removed an object. This required identifying and
  fixing a shallow-copy pitfall in `replay(..., start=...)` before it could
  cause silent state corruption (see `docs/architecture/phase-9-operation-types.md`).
- `OperationValidator`'s payload size cap became type-aware
  (`restore_version` needs a much higher ceiling than every other type).
- A new, deliberate exception to "the client builds the payload": restore's
  full snapshot is server-computed, never client-supplied.
- New backend modules: `apps/whiteboard/restore.py` (`RestoreService`),
  `apps/whiteboard/history.py` (humanization), `apps/whiteboard/board_import.py`
  (`ImportService`) — each a thin, focused service, not a speculative
  abstraction (per the original spec's own "only create services that
  provide meaningful separation" guidance).
- New frontend modules: `geometry.ts` gained the shared move/resize math
  (handle positions, degenerate-bbox-safe scale derivation); `history.ts`,
  `export-png.ts`, `export-json.ts` are new; `input.ts`/`engine.ts`/
  `renderer.ts` gained new gesture modes and a live-preview render path
  (`Renderer.renderWithOverride`) that deliberately never touches the
  cached static bake, so a cancelled gesture requires no extra
  invalidation bookkeeping.
- No new database tables. No changes to `Whiteboard.partnership`'s
  `OneToOneField`. No changes to the Partnership authorization model.

## Database Changes

One migration: `apps/whiteboard/migrations/0004_alter_whiteboardoperation_operation_type.py`
— widens `operation_type`'s `choices=` to include the three new types.
Django-level metadata only (`choices` on a `CharField` is not a
Postgres-enforced constraint) — zero-lock, zero-downtime. Reviewed in
`docs/deployment/database-migrations.md`.

## Security Changes

Full review: `docs/security/phase-9-security-review.md`. Summary: no new
authorization primitive — every new endpoint resolves the whiteboard via
the exact same `_get_whiteboard_and_partnership` helper every existing
endpoint uses. Import always regenerates object ids server-side, never
trusts client-supplied ones. Import's `MAX_IMPORT_OBJECTS` bound is a
novel-vector guard, not a fix for the still-open SC-1 finding (unbounded
per-board object count) from the Phase 8 audit — explicitly not conflated
with it. New tests covering authorization, rate limiting, and
archived-board rejection for every new endpoint, matching the existing
per-endpoint test pattern throughout this project.

## Offline Changes

Full table: `docs/architecture/phase-9-offline-compatibility.md`. Move and
resize are fully offline-capable through the existing durable queue with
**no special offline policy**: the engine submits every queued op against
the newest server version, and the server's no-op-on-absent-object
convention (the `delete_object` precedent) lets a queued mutation of a
since-removed object confirm cleanly. The client-side quarantine machinery
and its "reject on conflict" classification were removed this phase — see
`docs/architecture/offline-conflict-matrix.md`, which also records the
superseded `move_object`-is-commutative analysis that once motivated a
deliberately-not-taken rebase-reject alternative. Restore, export, and
import all require connectivity — stated as a real, deliberate scope
boundary in each case (restore needs the full server ledger to compute a
historical snapshot; export/import need to read/write the authoritative
current server state), not a gap.

## Real-Time Changes

Move/resize broadcast live exactly like drawing already does — no new
WebSocket message types were needed (the protocol was already generic over
`operation_type`). Restore's broadcast is the one new wrinkle: it carries a
deliberately **stripped** payload (`target_sequence` only, never the full
snapshot), because a real restore snapshot can run into the hundreds of KB
to multiple MB — comfortably exceeding the 250KB WebSocket frame limit.
Every observer (live broadcast or reconnect catch-up — both funnel through
one callback in `index.ts`) resolves a restore with a full state refetch
instead. Documented in `docs/architecture/websocket-protocol.md` and
`docs/architecture/whiteboard-history.md`.

## Performance

Real local measurements, `docs/performance/phase-9-benchmarks.md`:

| Objects | `reconstruct_state_at` (restore) | Snapshot size | Mixed create/move/resize replay |
|---:|---:|---:|---:|
| 100 | 14.0 ms | 14.9 KB | 0.3 ms |
| 1,000 | 29.0 ms | 149.8 KB | 2.2 ms |
| 5,000 | 220.8 ms | 753.1 KB | 11.1 ms |
| 20,000 | 631.3 ms | 3,025.1 KB | — |

**This benchmark caught a real sizing bug before it shipped**: the
implementation plan's first-pass `MAX_RESTORE_PAYLOAD_BYTES` estimate
(150,000 bytes, reasoned only from "smaller than the WebSocket frame
limit," without having measured real snapshot sizes) would have rejected
restore for any board past roughly 1,000 objects. Corrected to 5,000,000
(5MB, matching the existing global request-body-size default) based on the
real measured ~150 bytes/object rate. This is recorded in the benchmark
doc and the phase-9 audit as an example of "measure before capping," not
smoothed over.

Move/resize replay cost is negligible at every measured size — consistent
with the Phase 8 benchmark's pure-`create_stroke` numbers, since both
operation types touch exactly one object's own point array per operation,
not a whole-board scan.

## Testing

Full account: `docs/testing/phase-9-test-plan.md`. Summary of what was
actually run, not estimated:

- **Backend**: 311 tests collected across the whole project (238
  pre-existing + 73 new/extended this phase, all in the whiteboard app).
  Two full-suite runs both showed the same, already-understood result: 301
  tests pass reliably; 10 tests in `tests/whiteboard/test_concurrency.py`
  fail under full-suite ordering due to a pre-existing Django
  `ContentType`-cache race with `ThreadPoolExecutor`-based tests (first
  documented in the Phase 8 production-readiness report, re-confirmed
  unchanged this phase) — confirmed to pass cleanly 10/10 in isolation,
  including the one new concurrency test this phase added
  (`RestoreConcurrencyTests::test_restore_races_normal_op_exactly_one_wins`).
  Not a regression: the file was not modified except to add that one new
  test class, and the identical pre-existing tests fail the identical way
  regardless of what else ran before them.
- **Frontend**: 114 tests across 11 files, all passing (the Phase-9 count
  was briefly 117; the conflict-fix completed this phase removed
  `conflict-resolver.test.ts` and rewrote `sync-engine.test.ts` for the
  always-anchored flush, netting 114). `tsc --noEmit` clean. `vite build`
  succeeds
  (`whiteboard.js`: 65.86KB → 72.47KB minified, 18.29 → 20.13KB gzipped,
  final measured build — the added History/export/import modules, minus the
  removed conflict machinery).
- **Static analysis**: `ruff check`/`ruff format --check` clean across the
  whole project. `mypy` shows 120 errors (up from Phase 8's documented
  105), but every new instance matches the *same pre-existing debt
  category* already present throughout this test suite before this phase
  (untyped `dict` return annotations, `Any`-return from `.json()` in test
  helpers) — not a new class of problem, and this phase's new backend
  service modules (`restore.py`, `history.py`, `board_import.py`) and the
  core `state_reconstruction.py`/`validator.py` changes introduced **zero**
  new mypy errors themselves. mypy caught one real bug during development
  — a variable-rename mistake that would have caused an `UnboundLocalError`
  at runtime in the resize replay path — before it ever reached a test run.
- **Migration/config checks**: `manage.py check`, `manage.py check
  --deploy` (with real production-shaped env vars), and `manage.py
  makemigrations --check --dry-run` all clean.
- **Manual verification actually performed**: `whiteboard_home` view
  rendering with every new template addition (`tests/whiteboard/test_views.py`,
  9/9 passing) — confirms no Django template syntax error via the real
  test client. A production `vite build` succeeded.

### Genuine gap: no live-browser manual smoke test

This environment had no browser automation tooling available in-session.
Dragging/resizing on a real rendered canvas (mouse and touch), the history
panel's live pagination and revert-confirmation flow across two real
browser sessions, PNG export's actual visual output, and a screen-reader
pass over the new UI were **not verified beyond code review, unit tests,
and a template-render check**. Listed honestly in
`docs/testing/phase-9-test-plan.md` as the phase's clearest remaining gap,
not glossed over.

## Known Limitations

- No live-browser manual smoke test was possible (above) — the single
  most important remaining verification step before treating this phase's
  UI work as fully proven.
- The pre-existing `test_concurrency.py` full-suite-ordering flakiness
  (above) is unresolved — inherited from Phase 8, not introduced or fixed
  this phase.
- mypy's pre-existing typing debt (now 120 errors) is unresolved —
  inherited, not retired, this phase wasn't scoped to do so (matching the
  Phase 8 precedent of treating it as informational).
- Restore, export, and import all require connectivity — a stated
  architectural boundary, not a bug, but worth restating here as a real
  limitation a user will encounter.
- SC-1 (unbounded per-board object count, Phase 8 audit) remains fully
  open. DP-1 (no replay-performance snapshotting) remains fully open and
  is explicitly *not* the same thing restore's snapshot mechanism does.
- At a history pagination boundary, a single real run of same-actor
  same-type activity can be split across two pages and shown as two
  smaller entries instead of one merged entry — a cosmetic imprecision,
  documented, not fixed (not worth the complexity for a first version).

## Recommended Future Work

Only items with actual evidence behind them, not speculative additions:

- **A live-browser (and ideally cross-device) smoke pass** over every new
  UI surface — the clearest, most concrete next step, given the honest gap
  above.
- **If offline sync ever needs a client-side rebase layer again**, the
  `move_object`-is-commutative analysis (against `resize_object`'s
  anchor-relative math) recorded in
  `docs/architecture/offline-conflict-matrix.md` remains the right starting
  point — the removed quarantine machinery made it moot this phase, but it
  is a concrete, well-reasoned candidate for a future revisit, not a vague
  "maybe someday."
- **If multi-board support is ever pursued**, the `Whiteboard.partnership`
  `OneToOneField` constraint and every authorization check point that
  assumes "the partnership's one board" would need deliberate, explicit
  redesign — flagged here, not started, per the scope decision recorded
  above.
