"""Human-readable whiteboard history (the "History" panel / restore source).

Presents the append-only ``WhiteboardOperation`` ledger as short,
human-readable sentences ("You added a drawing", "Partner cleared the
board") rather than exposing raw operation types, ids, or sequence numbers
to users. This is a pure read/presentation layer over the same rows
``selectors.py`` already exposes for sync — it introduces no new persisted
state and creates no second source of truth.

Also serves as this app's "activity feed": both would be humanized,
newest-first views over the same ``WhiteboardOperation`` rows in this
codebase, so building them as two separate features would just be
duplication (see ``docs/product/phase-9-features.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.accounts.models import User

from .enums import WhiteboardOperationType
from .models import Whiteboard, WhiteboardOperation

# {actor} is resolved per-entry to "You" / "Partner" / "Someone" (a deleted
# account, actor=SET_NULL) -- never an actual display name, matching this
# product's existing "You"/"Partner" framing (see
# templates/whiteboard/whiteboard.html's presence avatars) rather than
# introducing a new naming convention just for this feature.
_TEMPLATES: dict[str, str] = {
    WhiteboardOperationType.CREATE_STROKE: "{actor} added a drawing.",
    # The ledger cannot distinguish a whole-stroke selection-delete from an
    # eraser-driven clip-delete -- both produce delete_object rows, so the
    # copy stays generic rather than guessing a distinction the data
    # doesn't support.
    WhiteboardOperationType.DELETE_OBJECT: "{actor} removed a drawing.",
    WhiteboardOperationType.CLEAR_CANVAS: "{actor} cleared the board.",
    WhiteboardOperationType.MOVE_OBJECT: "{actor} moved a drawing.",
    WhiteboardOperationType.RESIZE_OBJECT: "{actor} resized a drawing.",
    WhiteboardOperationType.RESTORE_VERSION: "{actor} restored an earlier version.",
}

# Operation types collapsed together when the same actor produces several in
# a row with nothing from the other actor in between -- keeps a single
# eraser swipe (which can emit many delete_object/create_stroke pairs) from
# flooding the feed with near-identical lines. CLEAR_CANVAS and
# RESTORE_VERSION are deliberately excluded: these are rare, significant
# events that should never be silently folded together.
_COLLAPSIBLE = frozenset(
    {
        WhiteboardOperationType.CREATE_STROKE,
        WhiteboardOperationType.DELETE_OBJECT,
        WhiteboardOperationType.MOVE_OBJECT,
        WhiteboardOperationType.RESIZE_OBJECT,
    }
)


@dataclass(frozen=True)
class HistoryEntry:
    sequence: int
    operation_type: str
    text: str
    count: int  # >1 when this entry represents collapsed consecutive ops
    created_at: str
    can_restore: bool


def _actor_label(op: WhiteboardOperation, viewer: User) -> str:
    if op.actor_id is None:
        return "Someone"
    if op.actor_id == viewer.pk:
        return "You"
    return "Partner"


def _describe(op: WhiteboardOperation, viewer: User) -> str:
    template = _TEMPLATES.get(op.operation_type, "{actor} made a change.")
    return template.format(actor=_actor_label(op, viewer))


def _entry_for_run(
    run: list[WhiteboardOperation], viewer: User, current_version: int
) -> HistoryEntry:
    # ``run`` is newest-first (see build_history); the newest op in the run
    # represents "when this batch of activity happened" for display/restore.
    newest = run[0]
    text = _describe(newest, viewer)
    if len(run) > 1:
        count_suffix = f" (×{len(run)})"
        text = f"{text[:-1]}{count_suffix}." if text.endswith(".") else f"{text}{count_suffix}"
    return HistoryEntry(
        sequence=newest.sequence,
        operation_type=newest.operation_type,
        text=text,
        count=len(run),
        created_at=newest.created_at.isoformat() if newest.created_at else "",
        can_restore=newest.sequence != current_version,
    )


def build_history(
    whiteboard: Whiteboard,
    viewer: User,
    *,
    before_sequence: int | None = None,
    limit: int = 50,
) -> list[HistoryEntry]:
    """Newest-first, human-readable history entries for ``whiteboard``.

    Unlike ``selectors.operations_for_whiteboard`` (built for incremental
    sync, always ascending from a known point), a history panel wants the
    *most recent* activity first. ``before_sequence`` pages backward in
    time: omitted (``None``) starts from the board's current version;
    passing the oldest ``sequence`` seen on a previous page fetches the
    next, older page.

    Consecutive same-actor same-type operations collapse into one entry
    (see ``_COLLAPSIBLE``). Known, accepted limitation: at a pagination
    boundary, a single real run of activity can be split across two pages
    and shown as two separate (smaller) entries instead of one -- a minor
    cosmetic imprecision, not a data-correctness issue, and not worth the
    complexity of boundary-run-merging for a first version of this feature.
    """
    upper = before_sequence - 1 if before_sequence is not None else whiteboard.version
    if upper < 0:
        return []

    ops = list(
        WhiteboardOperation.objects.filter(whiteboard=whiteboard, sequence__lte=upper).order_by(
            "-sequence"
        )[:limit]
    )

    current_version = whiteboard.version
    runs: list[list[WhiteboardOperation]] = []
    for op in ops:
        if (
            runs
            and op.operation_type in _COLLAPSIBLE
            and runs[-1][-1].operation_type == op.operation_type
            and runs[-1][-1].actor_id == op.actor_id
        ):
            runs[-1].append(op)
        else:
            runs.append([op])

    return [_entry_for_run(run, viewer, current_version) for run in runs]


def entry_to_dict(entry: HistoryEntry) -> dict[str, object]:
    return {
        "sequence": entry.sequence,
        "operation_type": entry.operation_type,
        "text": entry.text,
        "count": entry.count,
        "created_at": entry.created_at,
        "can_restore": entry.can_restore,
    }
