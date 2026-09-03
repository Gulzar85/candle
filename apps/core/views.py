from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def health_check(request: HttpRequest) -> JsonResponse:
    """Liveness + readiness check.

    ``/health/live/`` is the process-alive probe.
    ``/health/ready/`` verifies that required dependencies (PostgreSQL and, when
    Redis is configured, the cache/channel backend) are reachable.
    ``/health/`` keeps returning OK for backward compatibility.
    """
    parts = request.path.rstrip("/").split("/")
    probe = parts[-1] if parts else ""
    if probe == "live":
        return JsonResponse({"status": "ok", "version": "0.1.0"}, status=200)

    if probe == "ready":
        checks: dict[str, bool] = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = True
        except Exception:  # noqa: BLE001 - probe must not raise
            checks["database"] = False

        if getattr(settings, "CACHES", None):
            try:
                cache.set("_healthcheck", "1", timeout=5)
                cache.get("_healthcheck")
                checks["cache"] = True
            except Exception:  # noqa: BLE001 - probe must not raise
                checks["cache"] = False

        all_ok = all(checks.values())
        return JsonResponse(
            {"status": "ok" if all_ok else "degraded", "checks": checks},
            status=200 if all_ok else 503,
        )

    return JsonResponse({"status": "ok", "version": "0.1.0"}, status=200)


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/home.html", {"page_title": "Home"})


def service_worker(request: HttpRequest) -> FileResponse:
    """Serve the service worker at root scope (/sw.js).

    Serving from the root path gives the worker control over the whole app
    (needed for the offline app shell). WhiteNoise serves /static/, so this
    view must live in the URLconf, not in static files.
    """
    sw_path = Path(__file__).resolve().parent.parent.parent / "static" / "sw.js"
    response = FileResponse(
        open(sw_path, "rb"),
        content_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )
    return response
