# Phase 7 — Pre-Implementation Audit

**Scope:** Product-quality pass over the existing private two-partner whiteboard.
This audit catalogues what already works, what must be preserved, what should
improve, what should **not** change, and the concrete debt/UX/a11y/perf/mobile
issues found. It is the written basis for the Phase 7 implementation order.

Every finding references `file:line` where a concrete location exists.

---

## 1. Executive summary

The Phase 0–6 architecture is **strong and intact**. The frontend is not a
"generic Django admin": there is already a custom product shell, a full-viewport
whiteboard with a document model + operation ledger + undo/redo + deterministic
renderer, offline-first IndexedDB sync with idempotency, real-time collaboration
with ephemeral presence, and a token-based Tailwind design system with light/dark.

The gaps that make it still feel "engineered, not shipped" are:

1. **The dashboard is empty and generic** — the signed-in home passes only
   `page_title`, showing two static profile cards instead of "your board".
2. **The whiteboard has no identity** — `Whiteboard.title` exists but is never
   surfaced or editable; there is no rename endpoint.
3. **Selection is only pan** — the select tool does not select, so there is no
   delete-by-selection, no hit-testing, no keyboard delete.
4. **Mobile whitespace** — the stroke color palette is hidden on mobile, there is
   no pinch-zoom / two-finger pan, and no top notch safe-area handling.
5. **The whiteboard toolbar hard-codes stroke color** with no width/opacity
   controls exposed (engine methods exist but are unbound).
6. **Every product brand already exists** (toasts, empty states, badges,
   modals) as **dead component templates** — nothing actually uses them; the
   live pages re-type the same Tailwind strings everywhere.
7. **A11y specifics are broken**, not merely missing: a dangling `aria-controls`,
   a static `aria-expanded`, a non-functional password show/hide toggle, a
   color-only palette selection, and hard-coded canvas dark color.
8. **No keyboard-shortcut help** and an inconsistent clear-shortcut (button
   confirms, `Ctrl+Shift+X` does not).

The recommended posture for Phase 7: **polish and complete the seams rather than
rebuild the engine.** Do not touch the operation model, the sync engine, the
renderer hot path, or the state machine. Improve the surface and the product UX.

---

## 2. What already works (preserve)

### Backend / data
- Robust authorization via service-layer boolean policies
  (`apps/partnerships/policies.py`: `can_access_whiteboard`,
  `can_manage_partnership`, etc.); the server is authoritative and the client
  never trusts itself.
- Append-only, versioned, idempotent-by-`operation_id` operation ledger
  (`apps/whiteboard/models.py`), with strict payload validation
  (`apps/whiteboard/validator.py`).
- Three operation types are fully wired end-to-end (create/delete/clear) and
  replay deterministically (`state_reconstruction.py`).
- Partnership lifecycle with invitations, tokens, and expiry is complete.
- All Phase 6 offline-first machinery (IndexedDB store, sync engine with
  backoff/retry, conflict quarantining, PWA/SW) is implemented and tested.

### Frontend (preserve, do not rewrite)
- **`engine.ts`** — a clean command surface (`undo`, `redo`, `clear`, `zoomBy`,
  `fit`, `setTool`, `setColor`, `setWidth`, `setOpacity`, `loadServerState`,
  `applyRemoteOperation`). Human-friendly. Do not rewrite.
- **`state.ts`** — pure, reversible operation ledger with bounded undo/redo,
  linear-history rule. The backbone for offline + real-time parity.
- **`renderer.ts`** — offscreen static layer + `version`-keyed re-bake; this is
  the performance backbone (no full redraw per pointer move). Preserve.
- **`tools.ts`** — deterministic pen smoothing / eraser clipping, pure math.
- **`input.ts`** — unified Pointer Events (mouse/touch/stylus), single-pointer
  gesture machine, `touch-action: none` already set on the stage.
- **Viewport** with zoom/pan/fit and correct DPR handling.
- **`index.ts` bootstrap** — offline restore + live state fetch + realtime
  wiring; already re-plays pending envelopes on reopen (Phase 6 fix).
- Realtime presence is **ephemeral** (in-memory, group broadcast) and correctly
  excludes self — keep it that way. Do not persist cursors.

### Design system core
- Token-based Tailwind v4 (`@theme` CSS custom properties), class-based dark
  mode with `prefers-color-scheme` fallback. This is a good foundation.
- Consistent Lucide icons (`data-lucide`) throughout.

---

## 3. What should be preserved explicitly (do NOT change)

1. **The operation model** — `Op` and the three server op types. Do not add
   text/shape op types in Phase 7; the spec's guidance is to document an
   architectural limitation rather than mutate the versioned ledger.
2. **The sync engine + conflict strategy** — server-authoritative versions +
   `operation_id` idempotency. No CRDT/OT.
3. **The renderer's static-layer optimization** and the DPR transform.
4. **Server-authoritative authorization.** Never weaken it for UI convenience.
5. **The offline-first durability guarantee** — every local op is durably
   persisted before submission. Never silently discard local work.
6. **The ephemeral presence model** — presence/cursor position is not a
   whiteboard operation.
7. **Django Templates + HTMX + Alpine as the shell**; TypeScript stays the owner
   of complex canvas behavior. No React/Vue.

---

## 4. What should be improved (Phase 7 targets)

| Area | Gap found | Plan |
|---|---|---|
| Dashboard | Empty (only `{"page_title"}`), two static cards | Product home: recent board, partner status, last activity, quick-open board, empty state |
| Whiteboard identity | `title` exists, never surfaced/editable, no rename endpoint | Rename endpoint (server-authoritative) + inline editable title in toolbar |
| Selection | Select tool only pans; no hit-test/delete | Select + hit-test + keyboard delete (maps to existing `delete_object` op). Document move/resize as unsupported by the op model. |
| Toolbar | No width/opacity controls bound; no Secondary menu; clear shortcut not confirmed | Bind width/opacity; group secondary tools into a popover; confirm clear always |
| Mobile | Palette hidden on mobile; no pinch-zoom / two-finger pan; no top safe-area; single-pointer lock | Palette via popover; multi-touch pinch + two-finger pan; safe-area top |
| Design system | 11 component templates unused; duplication everywhere; radius/size drift | Reuse components; standardize radius/elevation/sizes; fix `muted-foreground` contrast |
| A11y | Broken `aria-controls`/`aria-expanded`; no drawer/menu focus mgmt; no-op password toggle; color-only swatch state; canvas not `role="img"` | Fix each concrete finding; add focus return, visible focus, `prefers-reduced-motion` |
| Sync UX | Already good; needs a small confirm on destructive clear + clearer saved-offline wording | Light copy + confirm; keep technical details out |
| Docs | No design-system, shortcuts, UX, mobile, test-matrix docs | Write the Phase 7 docs |

---

## 5. Technical debt found

1. **Dead component library** — `templates/components/{button,card,modal,toast,
   alert,badge,avatar,empty-state,icon-button,input,loading-state}.html` are
   never included (grep for `components/` returns nothing). Either wire them up
   or delete them. Phase 7: wire `toast`, `badge`, `empty-state`, `avatar` where
   they add value; keep the set small.
2. **Message/alert block duplicated verbatim in ~7 templates** (auth, dashboard,
   profile, settings, partnership home, invitation detail/list). Extract to a
   partial and include it.
3. **Primary-button class string re-typed in dozens of files**; card pattern
   (`bg-card border border-border rounded-xl p-6`) re-typed everywhere. Centralize
   via Tailwind component classes / a macro.
4. **Card radius drift** — `rounded-xl` vs `rounded-2xl` for the same concept;
   icon-button sizes of `w-9/w-10/w-11`; `rounded-md` vs `rounded-lg` on toolbar.
5. **`--color-accent` is overloaded** — used as both a light fill (hero badge)
   and a hover surface (toolbar). Two roles, one token. Introduce a distinct
   hover/surface token.
6. **`text-danger` is overloaded** — error text *and* destructive-action affordance.
7. **Two theme implementations** — a head flash-prevention script (base.html)
   and a separate Alpine `darkMode` in shell.html that can drift. Consolidate on
   one source of truth (CSS `[data-theme]`/class from a single initializer),
   keep the flash-prevention inline script, remove the Alpine duplicate.
8. **Base `theme-color` is static** (`#0a0a0a`) and won't reflect the active theme.
9. `Whiteboard.title` is a dead field — no reader/writer. Also `PartnershipStatus`
   `PartnershipMemberStatus.REMOVED` and invitation `CANCELLED`/`REVOKED` some
   branches are dead in the UI.
10. **No whiteboard archive/unarchive endpoint** despite model support.

---

## 6. UX problems discovered

1. Dashboard is a CRUD-card grid, not a product home (no quick path to the board).
2. Whiteboard page gives the user no context: no board title, no partner name in
   the toolbar, no way to know whose board this is.
3. No way to change stroke **width** or **opacity** from the UI (methods exist).
4. Color palette fully hidden below `sm` — mobile users cannot change color.
5. "Clear board" is reachable via `Ctrl+Shift+X` **without** confirmation while
   the toolbar button confirms — inconsistent.
6. No selection → no delete-by-keyboard; the select tool offers no feedback.
7. Presence shows an avatar dot but no name/partnership clarity; acceptable but
   subtle is good. Keep subtle.
8. Empty/loading/error states exist in parts (empty-state template dead, and
   the dashboard has no empty state when there is no active partnership yet).
9. `auth.html` footer `{{ now|date:"Y"|default:"" }}` silently renders empty if a
   `now` context var is absent. Not harmful, but sloppy.

---

## 7. Performance concerns

1. **Presence cursor move** re-renders realtime state label on every
   `presence.update` (`index.ts:202` → `renderRealtimeState`). Cursor updates are
   frequent; this causes a DOM text write per cursor move per remote user. Throttle.
2. **No multi-touch** means zoom/pan on touch requires buttons only; wheel-only
   on desktop. Acceptable but a real mobile limitation.
3. Canvas static-layer optimization is correct; **do not** move per-stroke
   redraw into a hot loop.
4. `getBoundingClientRect()` on every pointermove (`input.ts:54`) — minor; fine.
5. Undo/redo history is bounded by op count (linear), not by stroke count — a
   very large single board's `undo` of a multi-hundred-stroke batch is one op.
   Good.
6. Realm: no WebSocket cursor floods persisted; coarsening is client-side.

---

## 8. Accessibility concerns

Concrete findings (all fixable without architecture change):

- `shell.html:83-84` — `aria-controls="desktop-sidebar"` points at no element and
  `aria-expanded="true"` is a static value not updated by `toggleSidebar()`.
- `shell.html:98-165` — mobile drawer `role="dialog"`/`aria-modal` but **no focus
  trap, no entry focus, no focus return**, no Escape-to-close, and focus is
  never managed.
- `shell.html:218-250` — account `role="menu"`/`menuitem` without arrow-key
  keyboard navigation (WAI-ARIA menu pattern violated).
- `accounts/_field.html:7-14` — the **password show/hide input never changes
  `type`**, so the toggle is functionally a no-op (only icon/label change).
- `accounts/_field.html` — help text is placed inside the `<label>` for checkbox
  fields, and `aria-describedby` association is inconsistent.
- `whiteboard/whiteboard.html:48-53` — the active color swatch is conveyed **only**
  by a `ring` class on the first swatch; no `aria-pressed` and no feedback as the
  user switches.
- `whiteboard/whiteboard.html:83` — `<canvas aria-label>` is not `role="img"`, and
  the drawing surface has no keyboard alternative (a documented limitation, not
  a new feature gap).
- `hard-coded dark` canvas `dark:bg-[#1c1c1e]` does not match any token.
- Light-mode `muted-foreground #737373` on `bg-muted #f5f5f5` is ~4:1 — marginal
  AA for normal text; nudge darker in light mode.

---

## 9. Mobile concerns

1. Stroke palette `hidden sm:flex` removes color control on phones entirely.
2. No pinch-zoom / two-finger pan (single-pointer lock in `input.ts:71`).
3. **Top notch safe-area is unhandled** — `base.html` sets `viewport-fit=cover`
   but the shell header and the whiteboard toolbar/footer have no
   `pt-[env(safe-area-inset-top)]` / bottom padding on the whiteboard footer.
4. Bottom nav handles bottom safe-area correctly (`shell.html:271`).
5. The whiteboard is a genuine full-viewport surface on mobile; PWA standalone
   layout should be verified with the notch insets.

---

## 10. Security review (no Phase-7 regression)

Confirmed sound and to be preserved:
- Server-authoritative object/partnership/whiteboard authorization.
- `operation_id` idempotency is the final defense against duplicate submission
  and multi-tab races.
- CSP (`worker-src 'self'`, nonces on scripts). The service worker never caches
  `/api/*` or authenticated HTML.
- IndexedDB scoped by `owner_key`, reads owner-filtered — no cross-account leak.
- Rate limiting on auth endpoints; request-size validation on operations.

Phase-7 additions must preserve these: the rename endpoint must validate against
the same membership policy and run through a service; the new UI must never send
raw user-entered strings into XSS-prone contexts (use Django escaping / text nodes
only); no private data into unsafe caches.

---

## 11. Recommended implementation order

1. **Design-system grounding** (`docs/design/phase-7-design-system.md` + token
   fixes + shell/theme consolidation + shared partials for alerts/buttons/badges).
   This de-risks everything downstream.
2. **Application-shell + a11y fixes** (aria/control, drawer/menu focus, mobile
   safe-area, theme source of truth, home page mobile feature section).
3. **Dashboard as product home** (backend data: recent board, partner, last
   activity; new dashboard template + empty state).
4. **Whiteboard identity** — rename endpoint + service + tests, inline title in
   toolbar, board/partner context in the header.
5. **Toolbar + drawing controls** — bind width/opacity, secondary-tools popover,
   mobile color/width control, consistent clear confirmation.
6. **Selection + delete** — select-hit-test + keyboard delete mapped to
   `delete_object`; document move/resize limitation.
7. **Presence + sync UX polish** — throttle cursor label updates; refine copy;
   conflict/confirmation copy for destructive actions.
8. **Mobile input** — pinch-zoom + two-finger pan (extend `input.ts` carefully,
   preserve single-pointer draw semantics); safe-area top.
9. **States** — loading/empty/error across pages; wire empty-state component.
10. **Keyboard shortcuts** — help overlay + `docs/product/keyboard-shortcuts.md`;
    keep engine's existing bindings, confirm clear.
11. **Docs** — design-system, keyboard-shortcuts, whiteboard-ux, mobile-ux,
    sync-user-experience, test-matrix, README.
12. **Tests + verification** — backend rename/authorization/dashboard; frontend
    selection/delete, mobile input, sync indicators; full suite + build.

---

## 12. Explicit non-goals for Phase 7

- Text objects, shape objects, resizing/moving objects, multi-select — the
  current operation model (`create_stroke`, `delete_object`, `clear_canvas`)
  cannot safely serialize these without a versioned ledger extension that is a
  separate effort. **Documented limitation, not silently half-built.**
- CRDT/OT, merge servers, microservices, background worker/OOM for "scalability".
- Persisting cursors or presence to the database.
- Third-party analytics/telemetry.