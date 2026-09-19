# Phase 9 Operation Types

Reference for the three operation types added this phase. Follows the same
documentation style as `docs/architecture/whiteboard-operations.md` (Phase
4) — see that doc for the three pre-existing types (`create_stroke`,
`delete_object`, `clear_canvas`), which are unchanged.

Per `apps/whiteboard/enums.py`'s own documented rule: these are **appended**
to `WhiteboardOperationType`, never renamed or renumbered — persisted rows
and future sync clients depend on the stable string value.

## `move_object`

Translates one object by a relative delta.

```json
{"object_id": "s-12", "dx": 14.5, "dy": -3.0}
```

- `dx`/`dy`: finite numbers (rejects NaN/Infinity), any magnitude.
- Deliberately a **relative delta**, not new absolute points — O(1)
  payload size regardless of stroke point count, and composes correctly
  under sequential replay (two translations sum to the same final position
  regardless of order).

**Replay** (`state_reconstruction.py`): translates every point of the
target object by `(dx, dy)`. No-op if the target object no longer exists
(matches `delete_object`'s existing no-op-on-absent convention). Does
**not** move the object to the top of z-order (unlike `create_stroke`'s
recreate-moves-to-top behavior) — moving an object shouldn't bring an old
drawing to the front.

## `resize_object`

Scales one object's points from a fixed anchor.

```json
{"object_id": "s-12", "anchor": {"x": 100.0, "y": 40.0}, "scale_x": 1.4, "scale_y": 1.0}
```

- `anchor`: the fixed point of the resize (opposite corner/edge from the
  dragged handle) — finite `{x, y}`.
- `scale_x`/`scale_y`: finite numbers in `[0.001, 1000]` (validator-enforced
  bounds; matches `frontend/src/whiteboard/geometry.ts`'s `MIN_SCALE`/
  `MAX_SCALE`).

Math: `new_p = {x: anchor.x + (p.x - anchor.x) * scale_x, y: anchor.y +
(p.y - anchor.y) * scale_y}`.

**Stroke width is never scaled** — resizing changes geometry, not line
thickness. A deliberate design choice, not an oversight.

**Replay**: same no-op-on-absent and no-z-order-change rules as
`move_object`.

**Degenerate bounding box** (a perfectly vertical/horizontal stroke has
zero extent on one axis): the *server* never derives `scale_x`/`scale_y`
from geometry — it trusts the client-supplied values directly and only
bounds-checks them, so the epsilon-divide risk is entirely client-side.
Frontend handling (`geometry.ts`):

- `visibleHandles(box)` never offers a handle whose drag would require
  dividing by a ~zero bbox dimension — a vertical stroke's selection shows
  only its top/bottom-center handles, not the left/right-edge or corner
  handles.
- `safeScaleFactor(newLength, oldLength)` floors `oldLength` to
  `MIN_BBOX_DIMENSION` (1e-3 world units) before dividing, so even a
  defensively-called degenerate resize produces a finite result, never
  NaN/Infinity.

## `restore_version`

See `docs/architecture/whiteboard-history.md` for the full design
rationale (why a snapshot, the size-cap correction, the wire-vs-DB
distinction). Payload shape:

```json
{"target_sequence": 137, "objects": [{"object_type": "stroke", "object_id": "...", "points": [...], "color": "#...", "width": 3, "opacity": 1, "creator_id": null}]}
```

- `target_sequence`: non-negative integer, must not exceed the board's
  current version (`RestoreService` rejects out-of-range values before
  ever touching the validator).
- `objects`: the full z-ordered snapshot at `target_sequence`,
  **server-computed** — the one operation type whose payload the client
  does not construct itself.
- Bounded by `limits.MAX_RESTORE_PAYLOAD_BYTES` (5,000,000 bytes / 5MB) and
  `limits.MAX_RESTORE_OBJECTS` (20,000) — both DB/JSONField-only ceilings,
  never wire-format ones (the payload is never sent over WebSocket).

**Replay**: full in-place reset of `state.objects`/`state.order` to the
embedded snapshot — a logical operation, like `clear_canvas`, never a
per-row delete of history.

## The shallow-copy pitfall (why `move`/`resize` are implemented the way they are)

`WhiteboardStateBuilder.replay(operations, start=None)` copies `start`
**shallowly**: `dict(start.objects)` copies the top-level dict, but the
per-object dict *values* are shared references with the caller's `start`
state. Every operation type before this phase was safe against this
because none of them ever mutated an existing object dict's fields in
place — `create_stroke` always assigns a brand-new dict from
`_stroke_object()`.

`move_object`/`resize_object` are the **first** operation types that patch
a subset of an existing object's fields rather than replacing or removing
it wholesale. Implemented naively (`obj["points"] = [...]`), this would
silently mutate the shared object dict and corrupt the caller's `start`
state through the shared reference — breaking the documented contract
"`start` is not mutated." Both implementations always rebind a **new**
dict instead:

```python
state.objects[existing["object_id"]] = {
    **existing,
    "points": [...],
}
```

This has a dedicated regression test
(`tests/whiteboard/test_replay.py::test_move_does_not_mutate_start_state`,
`test_resize_does_not_mutate_start_state`): build a state, pass it as
`start=`, apply a move/resize via a second `replay()` call, assert the
original `start` object is byte-for-byte unchanged (including an identity
check on the unrelated `points` list reference).

## Client-side `Op` shapes (frontend, not persisted — see `types.ts`)

```ts
| { type: "move"; from: Stroke; to: Stroke }
| { type: "resize"; from: Stroke; to: Stroke; anchor: Point; scaleX: number; scaleY: number }
```

Both carry the **concrete** before/after `Stroke`, matching this file's
existing principle ("carries the concrete data it affected so it can be
inverted exactly, without consulting other state") — `invertOp` swaps
`from`/`to` (and inverts `scaleX`/`scaleY` to their reciprocal, which is
exactly correct since the anchor point is invariant under scaling: the
inverse of "scale by S from anchor A" is "scale by 1/S from the same
anchor A").

## Validation summary (`apps/whiteboard/validator.py`)

- `_validate_stroke_object_fields` factored out of `_validate_create_stroke`
  — the shared shape (`object_id`, `points`, `color`, `width`, `opacity`,
  `creator_id`) used by `create_stroke` payloads, each entry in a
  `restore_version` snapshot, and each entry in a JSON import.
- The payload size cap became **type-aware**: `operation_type` is
  identified before either size check runs, since `restore_version` needs
  a different (much higher) cap than every other type.
