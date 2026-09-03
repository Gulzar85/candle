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
from apps.partnerships.policies import can_access_whiteboard

from . import selectors
from .errors import (
    InvalidOperationError,
    RateLimitedError,
    WhiteboardAPIError,
)
from .limits import MAX_BATCH_OPERATIONS
from .models import Whiteboard
from .service import WhiteboardMetadataService, WhiteboardOperationService, normalize_batch
from .validator import OperationValidator

_RATE_SCOPE = "wb_ops"
_RATE_LIMIT = 60  # operations POSTs per window
_RATE_WINDOW = 60  # seconds
_RATE_COOLDOWN = 10  # seconds cooldown after burst


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

        # Build response.
        acks = [ack.to_dict() for ack in result.acks]
        resp_data: dict[str, Any] = {
            "acks": acks,
            "version": result.version,
            "applied": result.applied,
        }

        # If all were duplicates, still 200 with duplicate:true in each ack.
        return Response(resp_data, status=status.HTTP_200_OK)


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


def _parse_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
