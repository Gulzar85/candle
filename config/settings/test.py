"""Test settings — security-hardened defaults that mirror production.

Tests run against this module so security regressions are caught early.
Import from base to get the full app/DB/Redis config; then override
security settings to their production values.  Channel layer stays
InMemoryChannelLayer (no Redis dependency for unit tests).
"""

from .development import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Security hardening — mirror production so tests exercise real settings.
# ---------------------------------------------------------------------------
DEBUG = False
SECRET_KEY = "test-only-not-a-real-secret-key-32chars-min"
ALLOWED_HOSTS = ["testserver"]
CSRF_TRUSTED_ORIGINS = ["https://testserver"]

SECURE_SSL_REDIRECT = False  # Test client doesn't do TLS.
SECURE_PROXY_SSL_HEADER = None  # type: ignore[assignment]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 86400
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True

# Keep InMemoryChannelLayer from development.py (no Redis in tests).
# Keep LocMemCache from development.py (no Redis in tests).
# Keep console email backend from development.py.

DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5 MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
