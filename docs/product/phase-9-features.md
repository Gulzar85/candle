# Phase 9 Features

## What was built, and why

The original Phase 9 spec covered a much larger surface (version history,
object manipulation, attachments, export/import, sharing, board
duplication/templates, comments, activity feed, search, presentation mode,
minimap, advanced shapes...) but its own stated principle was: **don't add
features merely because they're common elsewhere — every feature must solve
a real problem, and features that fail that test should be removed.**

Given that, four scope questions were put to the user before implementation
began (recorded in the approved plan), and the minimal/recommended option
was chosen on every one. What shipped:

### Version history + restore

**Problem it solves**: there was previously no way to see what changed on
the board, when, or by whom, and no way to recover from an unwanted change
(other than manually undoing your own recent local edits, which doesn't
help against a partner's changes or anything beyond the current session's
undo stack).

- A history panel showing humanized entries ("You added a drawing",
  "Partner cleared the board") — never raw operation ids or sequence
  numbers.
- "Revert" on any past entry, creating a new, forward-moving
  `restore_version` operation. Previous history is never deleted or
  rewritten.
- Folded together with "activity feed" (a separate item in the original
  spec) — in this codebase both would be near-duplicate humanized views
  over the same operation ledger, so building two would itself be the kind
  of bloat the spec's own principle warns against.

### Object manipulation: select, move, resize

**Problem it solves**: previously, a drawn stroke could only be selected
and deleted — there was no way to reposition or resize anything without
erasing and redrawing it. This is a basic expectation of any drawing tool.

Scoped to single-object move/resize only. Multi-select and grouping were
explicitly deferred (see below) — they're a substantially larger frontend
build (rubber-band selection, group relationship modeling, new sync-policy
corner cases) for a benefit that doesn't clearly outweigh the
added complexity in a two-person board where simultaneous edits to the
exact same object are rare.

### Export (PNG, JSON) and import (JSON)

**Problem it solves**: there was previously no way to get a board's content
out of the app (for sharing outside Candle, or as a personal backup) or to
bring content back in.

- PNG: a shareable image of the board, fit to its content.
- JSON: a portable, versioned representation that can be re-imported —
  into the same board or a different one — through the exact same
  validation pipeline as any hand-drawn stroke.
- Import is additive by default (adds to the current board); an explicit
  "clear the board first" option is available for a full replace, composed
  from the existing clear operation rather than inventing new server-side
  replace semantics.

## What was deliberately not implemented, and why

| Feature | Why deferred |
|---|---|
| Image/attachment support | Needs a new multipart upload endpoint (the whiteboard API is JSON-only today), a storage-backend decision (production media storage is still local disk, a known gap from the Phase 8 audit), and new re-encoding security work. Genuinely valuable, but a large enough scope increase that it deserves its own pass with an explicit decision on where uploaded images are stored in production. |
| Multi-board support (duplication, organization/favorite/search, templates) | `Whiteboard.partnership` is a `OneToOneField` — exactly one board per partnership, enforced structurally. Supporting multiple boards means lifting that constraint: new models/URLs, a new dashboard UI (which currently assumes exactly one board card), and touching every authorization check point. A genuine architecture change, not a feature bolt-on, with no demonstrated product need for it today. |
| Sharing/access beyond two people, public links | The two-person Partnership model is deliberately, structurally enforced (a database trigger plus a partial unique index — not just an app-level default), and the product's own UI copy says "Candle is made for two." No evidence anywhere in the codebase or its history suggests this has been requested. The original spec itself says not to weaken this without genuine justification. |
| Comments/annotations | Not clearly justified as more valuable than the history feature already shipped for "understanding what happened" — a comments system that avoids becoming a chat platform (the spec's own explicit warning) needs its own careful scoping, better done as a deliberate future decision than folded into an already-large phase. |
| Multi-select, grouping | See "Object manipulation" above — real added complexity (rubber-band selection, group relationship modeling, new sync-policy corner cases) for a benefit that's genuinely unclear given how rare true simultaneous multi-object edits are in a two-person board. |
| Search | Nothing in the product currently has enough distinct, searchable content (one board per partnership, no comments, no multiple boards) to make search meaningful yet — it becomes relevant only if/when multi-board support or comments exist. |
| Presentation mode, minimap, advanced shapes | Explicitly Tier 3 in the original spec ("do not automatically implement every Tier 3 feature") and none has a demonstrated need distinct from what history/export/move-resize already provide. |

## Product quality review

Asked honestly, per the original spec's own final-review checklist:

- **Is the board still simple?** Yes — no new drawing tools were added; the
  existing pen/eraser/select toolbar is unchanged. The one new toolbar
  element is a single "Board" menu consolidating History/Export/Import
  behind one trigger, matching the existing style-popover consolidation
  pattern rather than adding three more inline buttons.
- **Did the toolbar become cluttered?** No — see above; net one new icon.
- **Are new features discoverable?** History/Export/Import live behind a
  clearly-labeled menu; move/resize work through the existing select tool
  without needing new instructions (press a handle to resize, press the
  body to drag) — matching how selection already worked.
- **Is mobile still comfortable?** The history panel is a full-height
  slide-in drawer (works at any viewport width); move/resize gestures use
  the same pointer-event pipeline as existing drawing/panning, which
  already handles touch. A live-device smoke pass is still recommended
  (see `docs/testing/phase-9-test-plan.md`'s honest testing-gaps section).
- **Is performance still fast?** Real local measurements
  (`docs/performance/phase-9-benchmarks.md`) confirm move/resize replay
  cost is negligible and restore's `reconstruct_state_at` scales in line
  with the existing replay benchmark.
- **Is offline behavior understandable?** Every feature's offline behavior
  is explicitly documented (`docs/architecture/phase-9-offline-compatibility.md`),
  including the one hard boundary (restore requires connectivity), shown
  clearly in the UI rather than silently failing.
- **Is collaboration still effortless?** Move/resize and restore both
  broadcast live to the partner exactly like drawing already does; no new
  collaboration model was introduced.

Nothing was found in this review that warranted removal.
