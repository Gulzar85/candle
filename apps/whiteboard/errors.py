"""Structured error contract for the whiteboard API.

Every API failure is represented as a ``WhiteboardAPIError`` carrying a stable
machine code (safe to surface to clients) and an HTTP status. Only a curated,
human-safe ``message`` is ever exposed — never exception internals, stack
traces, or raw DB errors.

Codes and their HTTP status:

    UNAUTHORIZED            401  (not logged in / session expired)
    FORBIDDEN               403  (authenticated but not an active member)
    WHITEBOARD_NOT_FOUND    404  (unknown public_id)
    INVALID_OPERATION       400  (operation structurally invalid)
    INVALID_PAYLOAD         400  (payload failed schema/range validation)
    OPERATION_TOO_LARGE     413  (request/payload over the configured limit)
    WHITEBOARD_READ_ONLY    409  (operation on an archived board)
    STALE_VERSION           409  (client base_version behind server version)
    DUPLICATE_OPERATION     200  (idempotent retry of an already-applied op;
                                  carries ``duplicate: true`` and the original ack)
    RATE_LIMITED            429  (too many write requests)

``UNAUTHORIZED``/``FORBIDDEN``/``RATE_LIMITED`` may also be raised by DRF /
middleware guards; the service raises the domain cases (read-only, stale, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def error_payload(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build the canonical error envelope."""
    body: dict[str, Any] = {"error": code, "message": message}
    body.update(extra)
    return body


@dataclass
class WhiteboardAPIError(Exception):
    """A domain error that maps to one HTTP error response."""

    code: str
    status: int
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return error_payload(self.code, self.message, **self.data)

    def __str__(self) -> str:  # pragma: no cover - debug aid only
        return f"{self.code}: {self.message}"


class UnauthorizedError(WhiteboardAPIError):
    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__("UNAUTHORIZED", 401, message)


class ForbiddenError(WhiteboardAPIError):
    def __init__(self, message: str = "You do not have access to this whiteboard.") -> None:
        super().__init__("FORBIDDEN", 403, message)


class WhiteboardNotFoundError(WhiteboardAPIError):
    def __init__(self, message: str = "Whiteboard not found.") -> None:
        super().__init__("WHITEBOARD_NOT_FOUND", 404, message)


class InvalidOperationError(WhiteboardAPIError):
    def __init__(self, message: str = "Invalid operation.") -> None:
        super().__init__("INVALID_OPERATION", 400, message)


class InvalidPayloadError(WhiteboardAPIError):
    def __init__(self, message: str = "Invalid payload.", **data: Any) -> None:
        super().__init__("INVALID_PAYLOAD", 400, message, data=data)


class OperationTooLargeError(WhiteboardAPIError):
    def __init__(self, message: str = "Operation payload too large.") -> None:
        super().__init__("OPERATION_TOO_LARGE", 413, message)


class WhiteboardReadOnlyError(WhiteboardAPIError):
    def __init__(self, message: str = "This whiteboard is archived and read-only.") -> None:
        super().__init__("WHITEBOARD_READ_ONLY", 409, message)


class StaleVersionError(WhiteboardAPIError):
    def __init__(self, current_version: int, client_version: int) -> None:
        super().__init__(
            "STALE_VERSION",
            409,
            "This whiteboard has newer changes. Refresh to continue.",
            data={"current_version": current_version, "client_version": client_version},
        )


class RateLimitedError(WhiteboardAPIError):
    def __init__(self, retry_after: int = 0, message: str = "Too many requests.") -> None:
        super().__init__("RATE_LIMITED", 429, message, data={"retry_after": retry_after})
