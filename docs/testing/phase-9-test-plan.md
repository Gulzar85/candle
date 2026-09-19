# Phase 9 Test Plan

**Status: describes what was actually written and actually run, verified by
running the suites — not a plan for tests that don't exist yet.** Genuine
gaps are listed honestly in their own section below, not glossed over.

## Backend — new and extended (pytest, real Postgres)

Collected counts verified via `pytest --collect-only -q` on 2026-09-04:

| File | Tests | What's covered |
|---|---:|---|
| `tests/whiteboard/test_replay.py` | 24 (+11 new) | Move/resize point math, no-op-on-absent-object, z-order unchanged, restore snapshot reset/to-empty, **the shallow-copy regression test** for both move and resize. |
| `tests/whiteboard/test_operations.py` | 52 (+17 new) | Validator boundaries for `move_object`/`resize_object` (missing fields, non-finite deltas, zero/absurd/non-finite scale, boundary-accepted scale), `restore_version` validator unit tests (negative `target_sequence`, missing/malformed `objects`, oversized snapshot, the type-aware higher byte cap actually taking effect). |
| `tests/whiteboard/test_restore.py` | 12 (new file) | Never deletes rows, reflects target state correctly, restore-to-zero, restore-of-a-restore (flatten), idempotent retry, stale `base_version` rejection, out-of-range `target_sequence` rejection, archived-board rejection, authorization, rate limiting. |
| `tests/whiteboard/test_history.py` | 16 (new file) | Humanization mapping for all 6 operation types, viewer-relative "You"/"Partner" framing, newest-first ordering, consecutive-run collapsing (and where it correctly does *not* collapse), `can_restore` correctness, pagination, the HTTP endpoint's authorization/validation. |
| `tests/whiteboard/test_import.py` | 16 (new file) | Schema-version rejection (missing/unsupported), atomic whole-import rejection on any bad object, object-id regeneration, chunk-boundary behavior (an import larger than `MAX_BATCH_OPERATIONS`), `MAX_IMPORT_OBJECTS` boundary, `clear_first` composing `clear_canvas`, additive-by-default behavior, the HTTP endpoint's authorization/rate-limiting/archived-board rejection, and a regression guard that an ordinary operation submit still works correctly after an import. |
| `tests/whiteboard/test_concurrency.py` | 10 (+1 new) | A real `ThreadPoolExecutor` race between a restore and an ordinary `create_stroke` submitted concurrently at the same base version — confirms restore inherits the exact same row-locked concurrency safety net as every other operation, not just that the code looks like it should. |

**Total whiteboard backend tests: 198**, all passing in a clean sequential
run (count re-verified 2026-09-04; contamination from overlapping parallel
test runs was observed and ruled out during development — see the note on
running tests sequentially in `docs/production-readiness-report.md`'s
Testing section from Phase 8, the same lesson applied here).

## Frontend — new and extended (vitest)

| File | Status | What's covered |
|---|---|---|
| `frontend/src/whiteboard/geometry.test.ts` | Extended | `translatePoints`, `scalePointsFromAnchor`, `safeScaleFactor` (including the degenerate-source-length case), `handlePositions`/`oppositeHandle`, `visibleHandles` (normal box, vertical-line box, horizontal-line box, zero-area box), `nearestHandle`, `computeResizeTransform` for corner/edge handles and a degenerate source box. |
| `frontend/src/whiteboard/state.test.ts` | Extended | Move/resize commit + `invertOp` round-trip (commit → undo → exact original stroke → redo), confirms other strokes are unaffected. |
| `frontend/src/whiteboard/sync-engine.test.ts` | Rewritten | Post-bugfix flush: stale destructive ops re-anchor and submit (no quarantine), permanent rejection → `FAILED` without a conflict record, bounded backoff → `FAILED`, idempotent-confirm no-op, STALE re-sync, and legacy-quarantine healing via `requeueConflicts`/`resolveAllConflicts`. (`conflict-resolver.test.ts` was deleted along with the quarantine machinery.) |
| `frontend/src/whiteboard/export-json.test.ts` | New file | `buildExport`'s versioned envelope shape, that it carries every field the backend's import validator requires, and — the one security-adjacent assertion — that `creator_id` is never included in an export. |

**Total frontend tests: 114 across 11 files**, all passing (the Phase-9
count was briefly 117; the conflict-fix completed this phase removed
`conflict-resolver.test.ts` and rewrote `sync-engine.test.ts` for the
always-anchored flush, netting 114). `tsc --noEmit` and `vite build` both
clean.

**Deliberately not unit-tested** (consistent with this codebase's existing,
stated convention — `renderer.ts`/`input.ts`/`engine.ts` are DOM-coupled and
smoke-tested only, not unit-tested): the new Host methods on `Engine`
(`hitTestHandle`, `onBeginResize`, etc.), the new `input.ts` gesture modes,
`Renderer.renderWithOverride`, and `history.ts`'s DOM rendering/wiring. As
much of the actually-tricky new math as possible was deliberately pushed
into `geometry.ts` instead, specifically so it would land in the one module
that's genuinely unit-tested rather than only smoke-tested.

## Backend static analysis / checks

`ruff check`, `ruff format --check`, `manage.py check`, `manage.py
makemigrations --check --dry-run` all run clean on every changed file.
`mypy` was run on every new/changed backend file; the shallow-copy
variable-naming bug it caught during development (see
`docs/architecture/phase-9-operation-types.md`) is a concrete example of it
finding a real bug, not just satisfying a lint gate. Zero new mypy errors
were introduced — the errors present after this phase are the same
pre-existing `apps/accounts/models.py` typing debt already documented in
the Phase 8 production-readiness report.

## Manual/smoke verification actually performed

- `whiteboard_home` view rendering with the updated template
  (`tests/whiteboard/test_views.py`, all 9 passing) — confirms the new
  "Board" popover, history panel, and import file input HTML render without
  a Django template error, via the real test client, not just
  `manage.py check`'s static analysis.
- A full `vite build` production build succeeds cleanly with the new
  modules bundled (`whiteboard.js`: 65.86 → 72.47 KB, gzipped 18.29 →
  20.13 KB — final measured build, which includes the added History panel
  and export/import code minus the removed conflict machinery; not a red
  flag).

## Genuine gaps — listed honestly, not implemented this phase

- **No live-browser manual smoke pass.** This environment has no browser
  automation tooling readily available in-session. The following are
  **not verified beyond code review and unit tests**, and should be done
  before considering this phase's UI work fully verified:
  - Dragging an object via touch (mobile) as well as mouse.
  - Resizing from all 8 handles on a real rendered stroke, including a
    genuinely degenerate near-straight-line stroke, to visually confirm
    the handle-hiding logic looks right (not just that the math is
    correct in isolation).
  - The history panel's live rendering, "Load earlier history" pagination,
    and the Revert confirmation flow end-to-end in a real two-browser
    session (one partner reverting while the other is live-connected,
    confirming the full-refetch behavior visually).
  - PNG export's actual visual output (bbox fit, padding, color fidelity)
    opened as a real image file.
  - JSON export → clear board → JSON import round-trip, confirmed visually
    identical.
  - Keyboard navigation and screen-reader labeling on the new history
    panel and Board menu (ARIA attributes were written following the
    existing style-popover/help-dialog patterns, but not verified with an
    actual screen reader).
- **No E2E test automation** — inherited, unchanged gap from Phase 8's own
  test plan; still true, still honestly listed there rather than silently
  dropped here.
- **No load-testing code** — same inherited gap; nothing here claims a
  concurrent-throughput number it didn't measure (see
  `docs/performance/phase-9-benchmarks.md`'s explicit "Deferred to actual
  deployment" section).
- **`sync-engine.ts` has no move/resize-specific tests.** The engine is
  fully generic over `operation_type` (zero code changes were needed for
  the new types), and its dedicated `sync-engine.test.ts` suite now covers
  the post-bugfix flush directly: stale destructive ops re-anchor and
  submit (no quarantine), permanent rejection → `FAILED`, bounded backoff
  → `FAILED`, and legacy-quarantine healing. Type-specific duplicates of
  those tests would add coverage-count without adding confidence.

## Mandatory regression (per the original spec's requirement)

Run and confirmed as part of finishing this phase — see
`docs/phase-9-completion-report.md`'s Testing section for the actual
executed commands and results: full `pytest` suite (all apps, not just
whiteboard), full `vitest` suite, `ruff check`/`ruff format --check`/`mypy`
across the whole touched surface, `manage.py check` (including
`--deploy`), and `manage.py makemigrations --check --dry-run`.
