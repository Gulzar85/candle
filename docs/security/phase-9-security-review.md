# Phase 9 Security Review

Every new endpoint/resource this phase reviewed against the same
authorization-question checklist used implicitly throughout this project:
who can create/read/modify/delete it, does offline mode affect permissions,
can the WebSocket expose it, can a crafted operation bypass it, can another
user access it by changing an id.

## Authorization path — unchanged, reused

Every new endpoint (`WhiteboardRestoreView`, `WhiteboardHistoryView`,
`WhiteboardImportView`) resolves the whiteboard via the exact same
`_get_whiteboard_and_partnership(request, public_id)` helper every existing
whiteboard endpoint uses (`apps/whiteboard/api.py`). No new authorization
primitive was introduced. This means:

- Only an active member of the owning partnership can reach any Phase 9
  endpoint (`can_access_whiteboard` policy check, unchanged).
- An ended/inactive partnership is rejected (403), matching every existing
  endpoint.
- A logged-out request is rejected by DRF's `IsAuthenticated` (403),
  matching every existing endpoint.

Verified by tests mirroring the existing pattern for every new endpoint:
`test_restore_requires_membership`, `test_restore_requires_active_partnership`,
`test_history_requires_membership`, `test_import_requires_membership`.

## `restore_version` — new findings and how they're closed

- **Who can create it?** Any active partnership member, via
  `RestoreService.restore()` — same population as who can submit any other
  operation. No elevated privilege.
- **Can a crafted operation bypass validation?** No — `RestoreService`
  builds the operation server-side and submits it through the unchanged
  `WhiteboardOperationService.submit_batch`, which always re-validates via
  `OperationValidator.validate()` before persisting, even for
  server-constructed operations. This is deliberate defense-in-depth,
  consistent with "never skip validation just because it's our own data."
- **Can the WebSocket expose more than intended?** This was the phase's
  main security-adjacent design problem: a naive implementation would put
  the full restore snapshot (`objects`) on the wire, both in the broadcast
  and in `sync.ops` catch-up. Resolved by never including `objects` in any
  WebSocket-bound representation (`realtime_signals.notify_restore` sends
  only `target_sequence` + metadata) and never including it in the
  history/operations-list HTTP responses either — the full snapshot is
  ORM-only. See `docs/architecture/whiteboard-history.md` for the full
  design.
- **Archived-board interaction**: restore is rejected on an archived board
  (`WHITEBOARD_READ_ONLY`, 409) — the same `_ensure_writable` check every
  other operation goes through, since restore is an ordinary operation as
  far as the service layer is concerned. Verified:
  `test_restore_on_archived_board_rejected`.
- **Rate limiting**: a distinct, tighter scope (`wb_restore`, 10/min,
  30s cooldown) than ordinary drawing (`wb_ops`, 60/min) — restore is rare
  in normal use and comparatively expensive to build (a full historical
  replay). Verified: `test_restore_rate_limited`.

## `import` — new findings and how they're closed

- **Can a crafted import file exploit anything?** Every imported object
  passes through the exact same `_validate_stroke_object_fields` validation
  a live `create_stroke` payload does (2–4000 points, hex color, width
  1–64, opacity 0–1) — the whole import is rejected atomically if any
  single object fails, nothing is partially applied at the validation
  stage. Verified: `test_whole_import_rejected_on_any_bad_object`.
- **Object identity spoofing**: client-supplied `object_id` values in an
  import file are **always discarded and regenerated server-side**
  (`uuid.uuid4()` per object) — an import file can never claim, collide
  with, or overwrite an existing object's identity. Verified:
  `test_object_ids_are_regenerated_never_trusted`.
- **Amplification risk**: `MAX_IMPORT_OBJECTS` (2,000) bounds a single
  import specifically. This is explicitly **not** a fix for the still-open
  SC-1 finding (unbounded per-board object count in general,
  `docs/architecture/phase-8-production-audit.md`) — it only prevents
  import from being a *novel* way to amplify that already-accepted risk
  faster than drawing would. Anyone reading the security posture later
  should not assume SC-1 was closed as a side effect of this bound.
- **Never inserted into Postgres directly.** Import converts validated
  objects into a real, version-chained sequence of `create_stroke`
  operations submitted through the unchanged
  `WhiteboardOperationService.submit_batch` — no parallel/raw write path
  exists.
- **Rate limiting**: `wb_import`, 5/hour, 300s cooldown — heavy and
  infrequent by nature. Verified: `test_import_rate_limited`.
- **Archived-board interaction**: same `WHITEBOARD_READ_ONLY` rejection as
  every other write. Verified: `test_import_on_archived_board_rejected`.
- **Accepted, stated tradeoff (not a vulnerability)**: a multi-chunk import
  (over `MAX_BATCH_OPERATIONS` = 50 objects) validates atomically upfront
  but commits chunk-by-chunk; a concurrent partner write between chunks can
  make a later chunk's assumed base version stale, raising
  `StaleVersionError` and leaving the import partially applied. This is the
  same class of outcome a human drawing the same content one stroke at a
  time would also leave behind on an interruption — not a security issue,
  and re-importing on top is safe (already-imported strokes are ordinary
  board content).

## `history` — new findings and how they're closed

- **Read-only, no new write surface.** `WhiteboardHistoryView` only reads;
  it cannot mutate anything.
- **Information disclosure check**: history entries expose humanized text,
  `sequence`, `operation_type`, `created_at`, and `can_restore` — never raw
  payload content (a `move_object`/`resize_object`/`create_stroke`
  payload's actual coordinates are not exposed through this endpoint, only
  through the state/operations-list endpoints a member could already read).
  Actor identity is exposed only as "You"/"Partner"/"Someone" — never a
  raw user id or the partner's real display name through this specific
  surface (the existing presence UI already shows the partner's real name
  elsewhere in the page; this is not a new leak, just a note that this
  endpoint specifically stays generic).
- **Pagination bounds**: `limit` capped at 200 server-side regardless of
  what a client requests, matching `OperationListView`'s existing pattern.

## Move/resize — reviewed, no new findings

Both operation types go through the same validator, same service, same
consumer path as every existing type — see
`docs/architecture/phase-9-operation-types.md` for the validation rules
(bounded, finite `dx`/`dy`/`scale_x`/`scale_y`/`anchor`). No new
authorization surface: a move/resize op is authorized exactly like any
other operation submission (active partnership member, board not
archived).

## IDOR review

Every new resource is reached exclusively through the partnership's
`public_id` (never a client-supplied whiteboard id directly, matching the
existing pattern across this whole app) and re-checks membership on every
request — there is no code path in any Phase 9 endpoint that resolves a
resource by an id without also checking `can_access_whiteboard` first.

## Client-side (frontend) review

- Clipboard/file input for import (`<input type="file" accept="application/json">`)
  never trusts the file's contents — `JSON.parse` failures are caught and
  surfaced as a clear error before anything is sent to the server, and the
  server re-validates everything regardless of what the client believes it
  parsed successfully.
- Export never includes `creator_id` or any other account-identifying
  metadata in the downloaded file (`export-json.ts::buildExport`) — no
  auth tokens, no internal infrastructure information, matching the
  original spec's explicit requirement. Verified:
  `export-json.test.ts::"never includes creator_id"`.
