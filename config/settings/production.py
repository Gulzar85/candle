import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401, F403

DEBUG = False

# ---------------------------------------------------------------------------
# SECRET_KEY — must be a real, random value.
# ---------------------------------------------------------------------------
_secret = os.environ.get("DJANGO_SECRET_KEY", "")
if not _secret:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")
if len(_secret) < 32:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be at least 32 characters.")
SECRET_KEY = _secret

# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()
]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")

# ---------------------------------------------------------------------------
# CSRF trusted origins — required behind proxies / WSS.
# ---------------------------------------------------------------------------
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
if not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured(
        "DJANGO_CSRF_TRUSTED_ORIGINS must list the production https origins "
        "(comma-separated) for CSRF validation."
    )

# ---------------------------------------------------------------------------
# WebSocket origins
# ---------------------------------------------------------------------------
if not WEBSOCKET_ALLOWED_ORIGINS:
    raise ImproperlyConfigured(
        "WEBSOCKET_ALLOWED_ORIGINS must list the production https origins "
        "(comma-separated) for WebSocket connections."
    )

# ---------------------------------------------------------------------------
# HTTPS / HSTS
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 86400  # 24 hours — shorter than Django's 2-week default.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# ---------------------------------------------------------------------------
# Request / upload size limits
# ---------------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

# ---------------------------------------------------------------------------
# Static files — hashed, compressed manifest storage (requires collectstatic).
# ---------------------------------------------------------------------------
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# SQLite does not benefit from Django's persistent connection pooling. This is
# also the safe default for PythonAnywhere's single WSGI web worker.
# ---------------------------------------------------------------------------
CONN_MAX_AGE = 0 if DATABASE_ENGINE == "sqlite" else 600
