"""Middleware to enforce HTTP request body size limits.

Django's ``DATA_UPLOAD_MAX_MEMORY_SIZE`` only applies to multipart/form-data
parsers.  For raw ``request.body`` reads (as the whiteboard API does for JSON
payloads), this middleware provides an explicit, early rejection based on the
``Content-Length`` header — before the full body is read into memory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

# Default cap: 5 MB.  Overridable via settings.
_DEFAULT_MAX_BODY_BYTES = 5 * 1024 * 1024


def get_max_body_bytes() -> int:
    return getattr(settings, "MAX_REQUEST_BODY_BYTES", _DEFAULT_MAX_BODY_BYTES)


class RequestSizeGuard:
    """Reject requests whose ``Content-Length`` exceeds the configured limit.

    This middleware runs early in the stack (after SecurityMiddleware) so
    oversized bodies never reach the view layer.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                size = int(content_length)
            except (ValueError, TypeError):
                pass
            else:
                if size < 0:
                    return self._reject()
                if size > get_max_body_bytes():
                    logger.warning(
                        "request.rejected size=%d limit=%d path=%s",
                        size,
                        get_max_body_bytes(),
                        request.path,
                    )
                    return self._reject()
        return self.get_response(request)

    @staticmethod
    def _reject() -> HttpResponse:
        return JsonResponse(
            {"error": "PAYLOAD_TOO_LARGE", "message": "Request body too large."},
            status=413,
        )
