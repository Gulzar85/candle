"""Whiteboard API views.

Translates HTTP into service/selector/policy calls. Uses DRF's ``APIView`` for
explicit control over the request lifecycle without inheriting generic behaviour
that does not match our operation-append model.

Endpoints:

    POST   /api/whiteboards/<public_id>/operations/   Submit operation(s)
    GET    /api/whiteboards/<public_id>/               Load reconstructed state
    GET    /api/whiteboards/<public_id>/operations/    Load operations (paginated)

All endpoints require authentication + partnership membership. CSRF is enforced
via Django's middleware (session auth); the frontend reads the CSRF token from a
<meta> tag and sends it as a header.
"""

from __future__ import annotations

import json
from typing import Any

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

# --- rate limiting -----------------------------------------------------------
from apps.accounts import ratelimit as rl
from apps.accounts.models import User
from apps.partnerships.policies import can_access_whiteboard, safe_display_name

from . import history, realtime_signals, selectors
from .board_import import ImportService
from .errors import (
    InvalidOperationError,
    RateLimitedError,
    WhiteboardAPIError,
)
from .limits import MAX_BATCH_OPERATIONS
from .models import Whiteboard
from .restore import RestoreService
from .service import WhiteboardMetadataService, WhiteboardOperationService, normalize_batch
from .validator import OperationValidator

_RATE_SCOPE = "wb_ops"
_RATE_LIMIT = 60  # operations POSTs per window
_RATE_WINDOW = 60  # seconds
_RATE_COOLDOWN = 10  # seconds cooldown after burst

# Restore is rate-limited more tightly than ordinary drawing: it's rare in
# normal use and comparatively expensive to build (a full historical replay).
_RESTORE_RATE_SCOPE = "wb_restore"
_RESTORE_RATE_LIMIT = 10
_RESTORE_RATE_WINDOW = 60
_RESTORE_RATE_COOLDOWN = 30

# Import is heavy (up to MAX_IMPORT_OBJECTS strokes) and infrequent by
# nature -- a tight, coarse limit is appropriate.
_IMPORT_RATE_SCOPE = "wb_import"
_IMPORT_RATE_LIMIT = 5
_IMPORT_RATE_WINDOW = 3600
_IMPORT_RATE_COOLDOWN = 300


def _rate_key(user: User) -> str:
    return f"{user.pk}"


# --- shared helpers ----------------------------------------------------------


def _get_whiteboard_and_partnership(request: Request, public_id: str) -> tuple[Whiteboard, Any]:
    """Resolve the whiteboard by partnership public_id, enforcing authz."""

    partnership = selectors.whiteboard_by_partnership_public_id(public_id)
    if partnership is None:
        raise _not_found("Partnership not found.")

    if not can_access_whiteboard(request.user, partnership):
        raise _forbidden()

    if not partnership.is_active:
        raise _forbidden("This partnership is not active.")

    whiteboard = selectors.whiteboard_for_partnership(partnership)
    return whiteboard, partnership


def _error_response(exc: WhiteboardAPIError) -> Response:
    return Response(exc.to_payload(), status=exc.status)


def _not_found(msg: str = "Not found.") -> WhiteboardAPIError:
    from .errors import WhiteboardNotFoundError

    return WhiteboardNotFoundError(msg)


def _forbidden(msg: str = "You do not have access.") -> WhiteboardAPIError:
    from .errors import ForbiddenError

    return ForbiddenError(msg)


# --- API views ---------------------------------------------------------------


class OperationSubmitView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Submit one or more operations to a whiteboard.

    POST /api/whiteboards/<public_id>/operations/

    Request body (either form is accepted):

        Single:  { "operation_id": "...", "operation_type": "...", ... }
        Batch:   { "operations": [ ... ] }
        Array:   [ ... ]

    The server determines: actor, sequence, resulting_version, created_at.
    Duplicate operations (same ``operation_id``) are idempotent: the existing
    ack is returned and no new row is created.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, public_id: str) -> Response:
        # Rate limit.
        rlim = rl.check(
            _RATE_SCOPE,
            _rate_key(request.user),
            _RATE_LIMIT,
            _RATE_WINDOW,
            cooldown=_RATE_COOLDOWN,
        )
        if rlim.blocked:
            return _error_response(RateLimitedError(rlim.retry_after))

        try:
            whiteboard, _partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        # Parse body.
        try:
            raw_body: Any = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _error_response(InvalidOperationError("Request body must be valid JSON."))

        operations = normalize_batch(raw_body)
        if not operations:
            return _error_response(
                InvalidOperationError(
                    'Provide a single operation object or {"operations": [...]} '
                    "or a JSON array of operations."
                )
            )

        if len(operations) > MAX_BATCH_OPERATIONS:
            return _error_response(
                InvalidOperationError(
                    f"Batch size {len(operations)} exceeds maximum of {MAX_BATCH_OPERATIONS}."
                )
            )

        # Compute per-operation wire sizes for the validator.
        wire_sizes: list[int | None] = [
            len(json.dumps(op, separators=(",", ":")).encode("utf-8")) for op in operations
        ]

        # Submit via the service (atomic, locked, validated).
        svc = WhiteboardOperationService(validator=OperationValidator())
        try:
            result = svc.submit_batch(
                whiteboard,
                request.user,
                operations,
                wire_sizes=wire_sizes,
            )
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        # Broadcast every committed operation to the whiteboard group so a
        # partner on a live WebSocket sees writes that arrived over HTTP (e.g.
        # while the author's socket was reconnecting). Same post-commit rule and
        # same envelope as the WebSocket consumer — peers deduplicate by
        # operation_id, so this is safe even when both transports carry the same
        # op. Never fails the response if the channel layer is unavailable.
        actor_public = {
            "public_id": str(request.user.public_id),
            "display_name": safe_display_name(request.user),
        }
        ops_by_id = {str(op["operation_id"]): op for op in operations}
        for ack in result.acks:
            op = ops_by_id.get(ack.operation_id)
            if op is None:  # pragma: no cover - acks only reference this batch
                continue
            realtime_signals.notify_operation_committed(
                public_id,
                operation_id=ack.operation_id,
                sequence=ack.sequence,
                version=ack.resulting_version,
                operation_type=op["operation_type"],
                payload=op.get("payload", {}),
                actor=actor_public,
                duplicate=ack.duplicate,
            )

        # Build response.
        acks = [ack.to_dict() for ack in result.acks]
        resp_data: dict[str, Any] = {
            "acks": acks,
            "version": result.version,
            "applied": result.applied,
        }

        # If all were duplicates, still 200 with duplicate:true in each ack.
        return Response(resp_data, status=status.HTTP_200_OK)


class WhiteboardRestoreView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Restore a whiteboard to an earlier point in its own history.

    POST /api/whiteboards/<public_id>/restore/
    Body: {"operation_id": "...", "base_version": <int>, "target_sequence": <int>}

    This creates a new, forward-moving ``restore_version`` operation through
    the same locked/versioned/idempotent path as every other operation — it
    never deletes or rewrites history (see
    ``docs/architecture/whiteboard-history.md``). On success, every other
    connection on this board is notified via a lightweight WebSocket
    broadcast (no snapshot payload — see ``realtime_signals.notify_restore``)
    so it can refetch full state.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, public_id: str) -> Response:
        rlim = rl.check(
            _RESTORE_RATE_SCOPE,
            _rate_key(request.user),
            _RESTORE_RATE_LIMIT,
            _RESTORE_RATE_WINDOW,
            cooldown=_RESTORE_RATE_COOLDOWN,
        )
        if rlim.blocked:
            return _error_response(RateLimitedError(rlim.retry_after))

        try:
            whiteboard, partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        try:
            raw_body: Any = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _error_response(InvalidOperationError("Request body must be valid JSON."))

        if not isinstance(raw_body, dict):
            return _error_response(InvalidOperationError("Request body must be a JSON object."))

        operation_id = raw_body.get("operation_id")
        base_version = raw_body.get("base_version")
        target_sequence = raw_body.get("target_sequence")

        if not isinstance(operation_id, str) or not operation_id:
            return _error_response(InvalidOperationError("operation_id is required."))
        if not isinstance(base_version, int) or isinstance(base_version, bool) or base_version < 0:
            return _error_response(
                InvalidOperationError("base_version must be a non-negative integer.")
            )
        if (
            not isinstance(target_sequence, int)
            or isinstance(target_sequence, bool)
            or target_sequence < 0
        ):
            return _error_response(
                InvalidOperationError("target_sequence must be a non-negative integer.")
            )

        try:
            result = RestoreService().restore(
                whiteboard,
                request.user,
                operation_id=operation_id,
                base_version=base_version,
                target_sequence=target_sequence,
            )
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        ack = result.acks[0]
        if not ack.duplicate:
            realtime_signals.notify_restore(
                str(partnership.public_id),
                operation_id=ack.operation_id,
                sequence=ack.sequence,
                version=ack.resulting_version,
                target_sequence=target_sequence,
                actor={
                    "public_id": str(request.user.public_id),
                    "display_name": safe_display_name(request.user),
                },
            )

        acks = [ack.to_dict() for ack in result.acks]
        return Response(
            {"acks": acks, "version": result.version, "applied": result.applied},
            status=status.HTTP_200_OK,
        )


class WhiteboardStateView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Load the reconstructed state of a whiteboard.

    GET /api/whiteboards/<public_id>/

    Returns the deterministic object-level state produced by replaying all
    operations. The response includes the current server version so the client
    can track sync progress.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, public_id: str) -> Response:
        try:
            whiteboard, _partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        state = selectors.get_whiteboard_state(whiteboard)

        # Convert to a JSON-friendly structure.
        objects_list = []
        for oid in state.order:
            obj = state.objects.get(oid)
            if obj:
                objects_list.append(obj)

        return Response(
            {
                "version": whiteboard.version,
                "objects": objects_list,
                "count": len(objects_list),
            },
            status=status.HTTP_200_OK,
        )


class WhiteboardRenameView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Rename a whiteboard (metadata only; not an operation).

    PATCH /api/whiteboards/<public_id>/
    Body: {"title": "..."}

    Renaming is a presentation change stored on ``Whiteboard.title``. It does not
    create an operation and does not touch the version ledger, so it stays fully
    offline-safe: the client can optimistically update its label and reconcile on
    the next load. Authorization matches every other board endpoint (membership +
    active partnership).
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request: Request, public_id: str) -> Response:
        try:
            whiteboard, _partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        try:
            raw_body: Any = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _error_response(InvalidOperationError("Request body must be valid JSON."))

        title = raw_body.get("title") if isinstance(raw_body, dict) else None
        if not isinstance(title, str):
            return _error_response(InvalidOperationError("`title` must be a string."))

        try:
            title = WhiteboardMetadataService().rename(whiteboard, title)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        return Response(
            {"title": title, "status": "ok"},
            status=status.HTTP_200_OK,
        )


class OperationListView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Load operations for a whiteboard, with optional version range filtering.

    GET /api/whiteboards/<public_id>/operations/

    Query parameters:
        after_sequence: int  — return ops with sequence > this value (default 0)
        limit: int           — max ops to return (default 100, max 500)

    Returns operations in deterministic sequence order. This is the foundation
    for incremental sync (Phase 5/6).
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    _DEFAULT_LIMIT = 100
    _MAX_LIMIT = 500

    def get(self, request: Request, public_id: str) -> Response:
        try:
            whiteboard, _partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        after_sequence = _parse_int(request.query_params.get("after_sequence"), default=0)
        if after_sequence < 0:
            return _error_response(InvalidOperationError("after_sequence must be non-negative."))

        raw_limit = _parse_int(request.query_params.get("limit"), default=self._DEFAULT_LIMIT)
        limit = max(1, min(raw_limit, self._MAX_LIMIT))

        ops = selectors.operations_for_whiteboard(
            whiteboard, after_sequence=after_sequence, limit=limit
        )

        items = [selectors.operation_to_dict(op) for op in ops]

        return Response(
            {
                "operations": items,
                "version": whiteboard.version,
                "count": len(items),
            },
            status=status.HTTP_200_OK,
        )


class WhiteboardImportView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Import a previously-exported JSON board (see ``export-json.ts``).

    POST /api/whiteboards/<public_id>/import/
    Body: {"schema_version": 1, "objects": [...], "clear_first": bool}

    Imported objects are never inserted into Postgres directly — they are
    converted into a real, version-chained sequence of ``create_stroke``
    operations submitted through the same locked/validated path as every
    other operation (see ``board_import.py``). Additive by default;
    ``clear_first`` composes the existing ``clear_canvas`` primitive rather
    than inventing new server-side replace semantics.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, public_id: str) -> Response:
        rlim = rl.check(
            _IMPORT_RATE_SCOPE,
            _rate_key(request.user),
            _IMPORT_RATE_LIMIT,
            _IMPORT_RATE_WINDOW,
            cooldown=_IMPORT_RATE_COOLDOWN,
        )
        if rlim.blocked:
            return _error_response(RateLimitedError(rlim.retry_after))

        try:
            whiteboard, _partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        try:
            raw_body: Any = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _error_response(InvalidOperationError("Request body must be valid JSON."))

        clear_first = bool(raw_body.get("clear_first")) if isinstance(raw_body, dict) else False

        try:
            result = ImportService().import_board(
                whiteboard, request.user, raw_body, clear_first=clear_first
            )
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        return Response(
            {"imported": result.imported, "version": result.version},
            status=status.HTTP_200_OK,
        )


class WhiteboardHistoryView(APIView):  # type: ignore[misc]  # DRF ships no stubs; APIView is Any
    """Load human-readable history entries for a whiteboard.

    GET /api/whiteboards/<public_id>/history/

    Query parameters:
        before_sequence: int  — page backward from just before this sequence
                                 (omitted = start from the current version)
        limit: int             — max entries to return (default 50, max 200)

    Returns newest-first, humanized entries (see ``history.py``) — never raw
    operation ids/sequences beyond what's needed to page and to restore.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]

    _DEFAULT_LIMIT = 50
    _MAX_LIMIT = 200

    def get(self, request: Request, public_id: str) -> Response:
        try:
            whiteboard, _partnership = _get_whiteboard_and_partnership(request, public_id)
        except WhiteboardAPIError as exc:
            return _error_response(exc)

        before_sequence = _parse_int(request.query_params.get("before_sequence"), default=0)
        if before_sequence < 0:
            return _error_response(InvalidOperationError("before_sequence must be non-negative."))

        raw_limit = _parse_int(request.query_params.get("limit"), default=self._DEFAULT_LIMIT)
        limit = max(1, min(raw_limit, self._MAX_LIMIT))

        entries = history.build_history(
            whiteboard,
            request.user,
            before_sequence=before_sequence or None,
            limit=limit,
        )

        return Response(
            {
                "entries": [history.entry_to_dict(e) for e in entries],
                "version": whiteboard.version,
                "count": len(entries),
            },
            status=status.HTTP_200_OK,
        )


def _parse_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
