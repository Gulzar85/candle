# Phase 7 Test Matrix

Mapping Phase 7 changes to the tests that cover them and how to run each suite.

## How to run

```powershell
# Frontend (cwd: frontend)
cmd /c "npx tsc --noEmit"
cmd /c "npx vitest run"
cmd /c "npx vite build"          # must not error on CSS @apply-of-component

# Backend (cwd: project root) — run per-file with the cache disabled
python -m pytest tests/whiteboard/test_rename.py -p no:cacheprovider
python -m pytest tests/accounts/test_dashboard.py -p no:cacheprovider
# … plus the pre-existing whiteboard + partnerships suites
```

The full backend suite can exceed the shell timeout; run per-file (or per
`<dir>`) with `-p no:cacheprovider`.

## Dashboard (product home)

| Behaviour | Test |
| --- | --- |
| Anonymous redirected to login | `tests/accounts/test_dashboard.py::DashboardTests::test_anonymous_redirected_to_login` |
| Empty state (no partnership) CTA | `…::test_no_partnership_shows_empty_state_cta` |
| Active partnership shows board + open CTA | `…::test_active_partnership_shows_board` |
| Pending partnership shows waiting state | `…::test_pending_partnership_shows_waiting_state` |

## Whiteboard rename (service + API)

| Behaviour | Test |
| --- | --- |
| Rename trims + persists | `tests/whiteboard/test_rename.py::RenameServiceTests::test_rename_returns_and_persists_trimmed_title` |
| Blank / too-long rejected, not saved | `…::test_rename_blank_title_rejected_and_not_saved`, `…::test_rename_too_long_rejected` |
| Rename never bumps version or writes ops | `…::test_rename_does_not_bump_version_or_create_operations` |
| Anonymous / non-member denied | `…::RenameApiTests::test_anonymous_is_denied`, `…::test_non_member_gets_403` |
| Member renames; invalid body rejected | `…::test_active_member_can_rename`, `…::test_non_string_title_rejected` |
| Inactive partnership forbidden | `…::test_inactive_partnership_is_forbidden` |

## Selection + delete

| Behaviour | Coverage |
| --- | --- |
| Select-click hit-tests the top stroke | `frontend/src/whiteboard/input.ts` click-vs-pan threshold; verified by tsc + `vite build` + browser smoke test (DOM-dependent, no unit suite in-repo for engines) |
| Delete emits `delete_object`, undoable | `frontend/src/whiteboard/engine.ts::deleteSelection` → `_emitServerOps` (`delete_object`); existing `tests/whiteboard/test_operations.py`, `test_replay.py` cover the server `delete_object` path |
| Escape/empty-click clears selection; auto-clear on remote delete | `engine.ts` `dropStaleSelection` + keydown branch |

The engine/renderer/input are DOM-dependent and, per repo convention, verified by
`tsc` + `vite build` + the browser smoke test rather than a jsdom unit suite.

## a11y / shell

| Behaviour | Coverage |
| --- | --- |
| Password toggle switches input `type` | `accounts/_field.html` + `form_helpers.inputclass_alpine`; render covered by existing account form tests |
| Sidebar/drawer/account-menu ARIA + focus | `tests/integration/test_shell.py` renders the shell; focus behaviour is exercised in the browser |

## Regression gates

Before merging Phase 7, all must pass:

- `npx tsc --noEmit`, `npx vitest run`, `npx vite build`
- Per-file backend: `tests/whiteboard/*`, `tests/accounts/*`, `tests/partnerships/*`,
  `tests/integration/test_shell.py`
- `python manage.py check`
- `python manage.py makemigrations --check --dry-run` (no new migrations)