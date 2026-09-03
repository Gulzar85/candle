# Phase 3 Audit — Local Whiteboard Domain & Engine

**Auditor:** Candle engineering (Senior Django Architect / Frontend engineer)
**Date:** 2026-09-03
**Applies to:** the codebase after the Phase 3 whiteboard work.
**Purpose:** Record what Phase 3 implemented, what is correct, what is risky, what
is deliberately deferred, and how the design cleanly admits future persistence,
real-time sync, mobile, and PWA without a rewrite. This audit pins down the exact
frontend/backend boundary and the verification facts so future phases do not
re-derive them.

> Verification note: every claim below was verified in this environment on the
> audit date (backend suite, frontend unit tests, `tsc`, production build), not
> assumed.

---

## 1. Scope and non-goals (Phase 3)

Implemented:

- A **local-only** whiteboard: a deterministic canvas drawing engine (native HTML
  Canvas + strict TypeScript + Pointer Events) with pen/eraser/select, undo/redo,
  clear, zoom/pan/fit, and a server-authorized domain boundary tied to the
  `Partnership` model.
- A modular, DOM-free core (pure logic modules) unit-tested with Vitest, with a
  thin engine/renderer/input/bootstrap layer verified by typecheck + build + smoke.
- Four architecture docs capturing the engine, state, rendering, and future-sync
  design.
- Eight backend authorization tests; full backend suite at **130 passing**;
  frontend suite at **35 passing** (5 files).

Deliberately **NOT** in scope (per the brief, deferred, not stubbed):

- **No WebSockets** and no real-time room state.
- **No server persistence / autosave.** The drawing lives in the browser only.
- **No `localStorage`-as-source-of-truth.** The board state is an in-memory
  module — no reliance on browser storage for durability.
- No mobile-touch beyond Pointer Events (which already unify mouse + touch + pen),
  no PWA offline canvas, no multi-user cursors.

---

## 2. Architecture boundary (backend)

The backend is intentionally a thin, authoritative **domain boundary**, not a
drawing engine.

- `apps/whiteboard/models.py` — `Whiteboard`: `id`, `public_id` (unique UUID,
  `db_index`), `OneToOne` `partnership` (`related_name="whiteboard"`),
  `created_at`/`updated_at`. **No drawing blob.** The drawing is deliberately not
  persisted server-side in this phase.
- `apps/whiteboard/selectors.py` — `whiteboard_for_partnership` (get-or-create)
  and `whiteboard_by_public_id`.
- `apps/whiteboard/policies.py` — re-exports `can_access_whiteboard` from
  `apps.partnerships.policies` as `can_view_whiteboard`.
- `apps/whiteboard/views.py` — `whiteboard_home`, `@login_required`; 403 render on
  policy denial; redirects to `partnerships:partnership_home` when the partnership
  is not `is_active`; computes `get_partner` + `safe_display_name` and signs /
  `creator_initials` for context.
- `apps/whiteboard/urls.py` (`app_name="whiteboard"`) — route
  `partnership/<uuid:public_id>/whiteboard/`.
- `apps/whiteboard/admin.py` — read-only; `has_add_permission` /
  `has_delete_permission` `False`; `ModelAdmin` generic-typed with
  `# type: ignore[type-arg]` per project convention.
- Migration `apps/whiteboard/migrations/0001_initial.py` (deps `partnerships 0002`).
- Registered in `config/settings/base.py` INSTALLED_APPS and
  `config/urls.py` (`"" include("apps.whiteboard.urls")` before core).
- `templates/partnerships/home.html` — the dead `href="#"` "Enter your shared
  space" now points at `whiteboard:whiteboard_home` with `active.public_id`.

**Authorization model.** Logging in is necessary but **not sufficient**: the
partnership is resolved only from its `public_id` in the URL, then membership is
checked server-side. Anything other than an active member of that partnership is
denied with a 403 before any page renders. Unknown `public_id` → 404. Tests cover:
anon redirect, non-member 403, member 200 + `<canvas` present, lazy create,
row-reuse, pending-partnership redirect, LEFT-member 403, unknown-id 404.

---

## 3. Frontend architecture (engine, deterministic)

The frontend lives in `frontend/src/whiteboard/`. It is strictly TypeScript
(`noEmit`, ES2022, DOM), module-only, no canvas libraries. Pure logic modules are
DOM-free and Unit-tested; DOM modules are verified via typecheck + build + smoke.

Pure modules (node-tested):

- `types.ts` — `Point`, `BBox`, `StrokeStyle`, `Stroke`, `Tool`, `Viewport`,
  `Size`, and `Op` (add/remove/clear carrying strokes).
- `geometry.ts` — `distSq`, `distance`, `midpoint`, `decimate`, `chaikin`,
  `distanceToSegment`/`Polyline`, `boundsOf`, `unionBounds`.
- `viewport.ts` — center-based `{cx, cy, zoom}`, `MIN_ZOOM`/`MAX_ZOOM` (0.2 / 4),
  `worldToScreen`/`screenToWorld`, `zoomAt` (anchor-kept), `panBy`, `fitToShow`
  (non-null with a single-point → zoom 1).
- `color.ts` — `DEFAULT_PALETTE`, `normalizeWidth` (1–64), `normalizeOpacity`
  (0–1), `isHexColor`, `resolveColor`.
- `tools.ts` — `finalizePenStroke` (decimate + smooth), `hitsStroke`,
  `clipToEraser`.
- `state.ts` — `createBoard`, `commit`/`undo`/`redo` with a version counter,
  `invertOp`, `hasStroke`/`strokeById`, empty-op no-ops.

DOM / bootstrap (typecheck + build + smoke):

- `renderer.ts` — DPR-aware `resize`; an offscreen static canvas cached by
  `{version, size, viewport}` (`sameState`); live-stroke overlay; round
  caps/joins; `globalAlpha` reset.
- `input.ts` — `InputController` + `Host` interface; structural event types
  (`PointerEvent | WheelEvent`); primary-pointer only; pan via middle-drag /
  select / ctrl / space; wheel zoom.
- `engine.ts` — pure-fit `Board`/`BBox`; public `viewport`/`size`/`tool` getters
  over private fields; toolbar commands; `window` keydown (Space, P/E/V, Ctrl+Z/Y,
  Ctrl+Shift+X); eraser preview via in-memory clip + commit remove-then-add; live
  stroke rendering; `ResizeObserver`; `destroy()`.
- `index.ts` — idempotent mount/teardown on `DOMContentLoaded`; toolbar wiring;
  status rendering (zoom %, stroke count, tool); palette highlight. It is the
  dedicated `whiteboard` Vite entry, so no changes are needed in `main.ts` — HTMX
  idempotency is satisfied by the standalone entry + re-entrant `destroy()`.
- `vite.config.ts` — added `whiteboard: resolve(__dirname, "src/whiteboard/index.ts")`
  second input → `../static/dist/assets/whiteboard.js` (15.36 kB built).
- `main.css` — `@layer components` `.wb-icon-btn` and
  `.wb-tool-btn[aria-pressed="true"]`.

The template `templates/whiteboard/whiteboard.html` is a full-viewport surface
(`base.html` directly, not the dashboard shell): toolbar, stage with
`data-wb-user`/`data-wb-partner`, `<canvas id="wb-canvas">`, status bar with
"Local only" indicator. Inline scripts use `{% csp_nonce_attr %}` /
`nonce="{{ csp_nonce }}"` to satisfy the project CSP.

---

## 4. What is correct (keep as-is)

- **Server-authorized domain boundary** is clean: the backend owns authorization
  and the identity of "whose whiteboard," while the drawing is a client concern.
  This is exactly the seam future persistence plugs into.
- **No drawing blob on the model** — the DB stays knowledge-light, which matches
  the "local-only" scope and avoids premature schema.
- **URL = resource**: the partnership `public_id` is the only resolution key; no
  client-supplied partnership/user is trusted.
- **Lazy row creation** (`get_or_create`) keeps the domain object but never
  blocks a view; row-reuse is tested.
- **Deterministic pure core.** `state`, `geometry`, `viewport`, `tools`, `color`
  are side-effect-free and invertible (undo/redo via inverted ops). This is what
  makes future sync (operation/CRDT) tractable.
- **Strict TS + no canvas libs** keeps the dependency surface minimal and the
  bundle small (whiteboard entry 15.36 kB).
- **CSP-safe** template (nonce) and locally bundled assets only.
- **Admin is read-only** and additive migration `0001_initial` with no rewrite.
- **Ruff clean** on `apps/whiteboard` / `tests/whiteboard`; the whiteboard app is
  mypy-clean (the only remaining repo mypy errors are pre-existing
  `apps/accounts/models.py` overrides — not introduced by Phase 3).

---

## 5. Risks / things to be aware of (documented, mostly deferred)

### 5.1 No durability — drawing is lost on navigation/refresh (Risk: HIGH — UX)
Intentional for this phase and clearly labelled "Local only" in the UI. It is the
single biggest product gap and the reason the architecture was built
op-to-sync-ready. See `whiteboard-future-sync.md` for the migration path.

### 5.2 Eraser uses in-memory clip (Risk: LOW)
`clipToEraser` computes intersections and commits remove+add. Deterministic and
reversible, but the clipped geometry is derived per erase action; fine for
local, but the sync chapter notes that erase should become a first-class
operation for CRDT compatibility.

### 5.3 No persistence of tool/viewport preference (Risk: LOW)
The canvas state is ephemeral; a future phase may persist tool/color/viewport as
user preference (product, not correctness).

### 5.4 Single bit-depth stroke model (Risk: LOW)
Stroke style is width + color + opacity, round-cap/join. No opacity-blend or
eraser-as-alpha model yet; the renderer resets `globalAlpha` to avoid state
leakage. Adequate for the current toolset.

### 5.5 Legacy repo mypy debt (Risk: MEDIUM — tooling)
`apps/accounts/models.py` retains pre-existing mypy errors (LSP overrides). Not
introduced by Phase 3; the whiteboard app itself is clean. Tracker already
records this as deferred.

---

## 6. Toolchain / verification facts (audit date)

- Backend suite: **`130 passed`** (`pytest`), incl. 8 new whiteboard tests.
- Frontend: **`35 passed`** across 5 Vitest files (`vitest.config.ts` with
  `@`→src alias, node env, include `src/**/*.test.ts`).
- `tsc --noEmit` exit 0; `npm run build` succeeds → `whiteboard.js` 15.36 kB
  (gzip 5.01 kB).
- Build output: `../static/dist/assets/whiteboard.js` (confirmed exists).
- Ruff clean: `apps/whiteboard tests/whiteboard`.
- Existing build warnings only: htmx `eval` (pre-existing, vendored) and a
  `>500 kB` `main.js` chunk (pre-existing, HTMX/Alpine bundle) — neither is
  Phase-3-introduced.

---

## 7. Definition of done for the future (what Phase 3 explicitly enabled)

The architecture was reviewed against the brief's forward requirements and is
**non-blocking** for:

- **Persistence** — swap the in-memory `state` for a loader/saver over the
  existing `Op` log; the `Whiteboard` model gets a `blob`/`ops` field; the
  `Host` interface and `Board` API stay unchanged.
- **Real-time sync** — the op log + version counter are the natural payload for
  WebSockets/Channels; deterministic local modules keep OT/CRDT feasible;
  `whiteboard-future-sync.md` details ordering/authority.
- **Mobile** — Pointer Events already unify touch/mouse/pen; the input layer
  consumes only structural event types; DPR-aware rendering is present.
- **PWA** — module-only bundle + full-viewport shell; offline canvas is a
  storage-layer concern, not an engine change.

---

## 8. Deferred technical debt (tracked, not blocking)

| Item | Why deferred |
|---|---|
| Server persistence / autosave | Explicitly out of phase scope; op-log keeps it additive |
| Real-time sync (WebSockets/Channels) | Follow-up phase; state model CTF-ready |
| Erase as first-class CRDT op | Local correctness fine; revisit with sync |
| Persisted tool/viewport preferences | Product preference, not correctness |
| Legacy accounts mypy cleanup | Pre-existing; separate from Phase 3 |
| Main `main.js` bundle split / htmx eval | Pre-existing build hygiene |

## 9. Testing gaps (future phases address)

- No persistence round-trip tests (nothing persists yet).
- No multi-client sync / conflict tests (no sync yet).
- No touch/pen-specific gesture tests (Pointer Events tested at type/logic level;
  canvas drawing itself is verified by build + smoke).

## 10. Cross-references

- `docs/architecture/whiteboard-engine.md` — engine/component layout & lifecycle.
- `docs/architecture/whiteboard-state.md` — state module, ops, undo/redo.
- `docs/architecture/whiteboard-rendering.md` — renderer, DPR, static/live layers.
- `docs/architecture/whiteboard-future-sync.md` — migration to persistence + sync.
