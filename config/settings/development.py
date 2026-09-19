from .base import *  # noqa: F401, F403

import os  # noqa: E402

DEBUG = True

ALLOWED_HOSTS = ["*"]

# CSP is relaxed in development so Vite's dev server and HMR work without friction.
SECURE_CSP = None  # type: ignore[assignment]

# Serve static from source dirs via finders (no collectstatic needed in dev),
# which also avoids WhiteNoise warning about a missing staticfiles/ root.
# autorefresh is set explicitly because pytest-django forces DEBUG=False in
# tests, which would otherwise make WhiteNoise look for a collected root.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Local development runs a single ASGI process, so the in-memory channel layer
# is correct and avoids depending on a local Redis server. (Production inherits
# the Redis channel layer from base.py; the pre-installed Redis 3.2 here is not
# compatible with redis-py 8's HELLO handshake, so we deliberately do not use it
# for local collaboration.)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Local WebSocket origins: default to every origin so device/LAN testing (two
# machines reaching a dev box by IP) is not silently degraded to HTTP-only —
# the consumer treats "*" as "allow any" and only production forbids it (its
# allowlist is required and never "*"). Set WEBSOCKET_ALLOWED_ORIGINS to pin
# explicit origins in dev.
WEBSOCKET_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("WEBSOCKET_ALLOWED_ORIGINS", "*").split(",") if o.strip()
]

# Development always sends email to the console, regardless of environment values.
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    }
}

# Keep the console readable: don't flood logs with per-query SQL.
LOGGING["loggers"].setdefault("django.db", {"handlers": ["console"], "level": "INFO"})  # noqa: F405
LOGGING["loggers"]["django.db"]["level"] = "INFO"  # noqa: F405
LOGGING["loggers"]["apps"]["level"] = "DEBUG"  # noqa: F405
LOGGING["root"]["level"] = "INFO"  # noqa: F405
LOGGING["root"]["handlers"] = ["console"]  # noqa: F405
