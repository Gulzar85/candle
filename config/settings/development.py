from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

SECURE_CSP = None

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

LOGGING["loggers"]["django"]["level"] = "DEBUG"  # noqa: F405
LOGGING["loggers"]["django"]["handlers"] = ["console"]  # noqa: F405
LOGGING["root"]["level"] = "DEBUG"  # noqa: F405