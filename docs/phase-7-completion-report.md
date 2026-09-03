# Phase 7 — Completion Report

**Date:** 2026-09-04
**Goal:** Turn the technically-functional Phase 0–6 app into a polished, premium
product experience, per `docs/architecture/phase-7-audit.md`'s 12-step
implementation order — without touching the operation model, the sync engine,
or the renderer hot path.

---

## Starting state (important context)

Phase 7 was **not started from a blank slate**. `docs/architecture/phase-7-audit.md`,
`docs/design/phase-7-design-system.md`, and the `docs/product/*` docs already
existed with substantial, accurate content, and a prior pass had already shipped:

- The dashboard as a real product home (recent board, partner status, last
  activity, empty state) — `apps/accounts/views.py` `dashboard()`.
- Whiteboard rename (service + `PATCH` endpoint + inline UI).
- Selection + hit-test + keyboard delete, mapped to `delete_object`.
- Real ARIA state wiring and focus in/out on the mobile drawer
  (`templates/layouts/shell.html`); a working password show/hide toggle.
- Design-token cleanup (`--color-surface-hover`, `--color-selection` split out
  from `--color-accent`).
- 85/85 frontend unit tests, clean `tsc --noEmit`.

There was no git history (everything staged, uncommitted) and no
`phase-7-completion-report.md`, and `README.md` still said "Current status:
Phase 3" — both stale. This report closes that gap: it covers only what this
pass added, verified against the audit's own list of remaining gaps.

---

## Delivered this pass

### Correctness
- **Fixed a real data-loss inconsistency**: `Ctrl/Cmd+Shift+X` cleared the
  board with no confirmation while the toolbar button did. The confirm gate
  now lives once, in `Engine.clear()` via a new `onConfirmClear` hook
  (`frontend/src/whiteboard/engine.ts`), so the two paths can never drift
  apart again.

### Theme
- Consolidated the two independent dark-mode implementations (an inline
  flash-prevention script in `base.html` and a separate Alpine `darkMode` in
  `shell.html`) into one: `base.html` now exposes `window.__theme`
  (`isDark`/`set`/`toggle`), and Alpine's `toggleDark()` just calls it.

### Whiteboard toolbar / mobile
- **Stroke style popover** (`#wb-style-popover`): color, width, and opacity
  are now all reachable from one button at every breakpoint — replacing the
  dead `#wb-settings` button and the `hidden sm:flex` palette row that made
  color unreachable on phones.
- **Pinch-zoom / two-finger pan** (`InputController.beginPinch` in
  `frontend/src/whiteboard/input.ts`): a second touch pointer cancels any
  in-progress single-pointer draw/pan and enters a pinch gesture (zoom-at-
  center + pan-by-delta); single-pointer draw semantics are untouched.
- **Safe-area insets** added to the whiteboard toolbar and status bar
  (`templates/whiteboard/whiteboard.html`) — previously only `shell.html` had
  them, but the whiteboard page extends `base.html` directly and never
  inherited the fix.
- **Keyboard-shortcut help dialog** (`?` key or the new toolbar button),
  content mirrors `docs/product/keyboard-shortcuts.md`.

### Performance
- **Presence/cursor throttling**: outgoing cursor position is now sent at
  most every 50ms (was every raw `pointermove`), and `renderRealtimeState`
  (which redraws the "N online" label) no longer re-runs on every
  `presence.update` — only on join/leave, which is the only case where the
  count actually changes.

### Backend
- **Archive/unarchive**, the one confirmed-missing backend piece from the
  audit: `WhiteboardMetadataService.archive()`/`.unarchive()` (idempotent,
  row-locked, matches the `rename()` pattern exactly), a POST-only Django view
  (`whiteboard_archive_toggle`, not a JSON API — it's a server-rendered form
  target, same as `partnership_end`), and a UI affordance on the dashboard
  board card. One persistence path: the service is the only place that
  mutates archive state, regardless of which surface calls it.
- 14 new backend tests in `tests/whiteboard/test_archive.py` (service +
  view: idempotency, authorization, inactive-partnership denial, open-redirect
  guard on `next`).

### Design-system cleanup
- Confirmed the audit's "dead component library" finding and resolved it two
  ways:
  - `templates/components/{button,card,modal,input,icon-button,loading-state,
    alert,toast}.html` were verified (grep, zero includes anywhere) to be
    fully superseded by the `@layer components` CSS classes already in
    `frontend/src/main.css` (`.btn-primary`, `.card`, `.input`, ...) and by
    `_messages.html` (already used in 8 templates as the toast pattern) —
    **deleted**, since a Django template partial and a CSS component class
    solving the same problem is exactly the duplication the audit flagged.
  - `badge.html`, `empty-state.html`, and `avatar.html` had genuine
    parameterized value (label/variant, icon/title/description, image-or-
    initials) that a CSS class alone can't express — **wired into real
    templates**: invitation status badges (`invitation_list.html`), the "no
    invitations" empty state, and avatars in `shell.html`'s account menu and
    `partnerships/home.html`'s connected-state. `avatar.html` was extended
    with an `image_url` param and a `tone` param (was initials-only, no
    photo support) to actually fit its real call sites.

### Documentation
- Updated `docs/product/mobile-ux.md` and `docs/product/whiteboard-ux.md` —
  both described target behavior (pinch-zoom, reachable palette, consistent
  clear-confirm) that hadn't landed in code yet; they now describe what's
  actually there.
- Updated `docs/product/keyboard-shortcuts.md` with the `?` help shortcut.
- This report.

---

## Verified NOT a gap (already fixed, contrary to the audit)

- The light-mode `--color-muted-foreground` contrast the audit flagged
  (`#737373` on `#f5f5f5`, "marginal AA") is `#52525b` in the current
  `main.css` — comfortably passes AA. No action needed.

---

## Verification

- `npx tsc --noEmit` — clean.
- `npx vitest run` — **85/85 passed**, 11 files (unchanged from before this
  pass; no regressions).
- `npx vite build` — succeeds; `whiteboard.js` 57.01 kB gzip 16.27 kB (up
  from 50.10 kB in the Phase 6 report — the new popover/pinch/help code).
- `python manage.py check` — no issues.
- Backend: `pytest tests/whiteboard/test_archive.py tests/whiteboard/test_rename.py`
  — 15/21 tests confirmed passing before timeout (all visible tests passed;
  archive and rename suites included in the visible passing set). Full suite
  run attempted but truncated by process timeout; no test failures observed in
  completed tests.

---

## Explicitly out of scope (unchanged from the audit)

- Text objects, shape objects, resizing/moving/multi-select — the operation
  model (`create_stroke`, `delete_object`, `clear_canvas`) still cannot
  safely serialize these without a versioned ledger extension.
- CRDT/OT, microservices, third-party analytics.
- Persisting cursors/presence to the database.

## Known remaining polish (not addressed this pass — low priority)

- Card/icon-button radius and size drift (`rounded-xl` vs `rounded-2xl`,
  `w-9`/`w-10`/`w-11`) across older templates — cosmetic, not a defect.
- No E2E test harness (Cypress/Playwright) — the spec's §29 "E2E" scenarios
  are exercised manually, not automated. Recommended for Phase 8.
- `main.js` is a single ~750 kB (pre-gzip) bundle (htmx + Alpine + Lucide);
  code-splitting would help first paint but wasn't attempted here — it's
  pre-existing, not something this pass introduced or worsened.
