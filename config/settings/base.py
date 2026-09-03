import os
from pathlib import Path
from typing import Any

from django.utils.csp import CSP  # type: ignore[import-untyped]
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load `.env` into the process environment (no-op if the file is absent, e.g.
# in production where real environment variables are set directly). Existing
# environment variables always take precedence over the file.
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")

DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.partnerships",
    "apps.whiteboard",
]

AUTH_USER_MODEL = "accounts.User"

# Authentication flow endpoints.
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "core:home"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "apps.core.middleware.RequestSizeGuard",
]

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.csp",
                "apps.core.context_processors.theme",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "candle"),
        "USER": os.environ.get("POSTGRES_USER", "candle"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

# Production channel layer: Redis used for cross-process, cross-instance
# collaboration messaging (transient — the authoritative whiteboard history
# always lives in PostgreSQL). Requires a Redis server that supports the RESP2
# ``HELLO`` handshake used by redis-py 8 / channels-redis 4.3, i.e. Redis >= 6.
# Local single-process development overrides this with the in-memory layer.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

# Trusted WebSocket origins. A browser WebSocket carries an ``Origin`` header;
# we reject connections whose origin is not listed here (see the whiteboard
# consumer). Values are literal scheme://host[:port] strings (no wildcards).
# Development injects sensible localhost defaults; production requires this to
# be set explicitly (see production.py).
WEBSOCKET_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("WEBSOCKET_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Static files: use the plain storage by default so `{% static %}` tags and
# development `runserver` work without a prior `collectstatic` run. Production
# overrides this with WhiteNoise's compressed, hashed manifest storage.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Public origin used to build absolute links in transactional emails.
# Must have no trailing slash (e.g. https://candle.example.com).
SITE_URL = os.environ.get("DJANGO_SITE_URL", "http://localhost:8000").rstrip("/")

# Partnership invitation validity window (hours). Overridable per environment.
PARTNERSHIP_INVITATION_EXPIRY_HOURS = int(
    os.environ.get("PARTNERSHIP_INVITATION_EXPIRY_HOURS", "168")  # 7 days
)

# Rate limits (requests per window seconds, optional cooldown) for sensitive
# partnership actions. Configurable so operators can tune without code edits.
PARTNERSHIP_RATE_LIMITS = {
    "invite": {"limit": 5, "window": 3600, "cooldown": 3600},
    "invite_resend": {"limit": 5, "window": 3600, "cooldown": 1800},
    "invite_accept": {"limit": 10, "window": 900, "cooldown": 900},
    "invite_reject": {"limit": 10, "window": 900, "cooldown": 900},
    "invite_revoke": {"limit": 5, "window": 3600, "cooldown": 3600},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Strong session/CSRF cookie baseline. HttpOnly protects cookies from script
# access; the CSRF token remains available to the frontend via the HTML meta
# tag rendered from the template context. `*_SECURE` and SameSite are hardened
# further in production.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

SECURE_CSP = {
    "default-src": ["'self'"],
    # 'unsafe-eval' is required by Alpine.js, which compiles its directive
    # expressions with the Function constructor at runtime. This is a
    # documented exception; no other source allows unsafe-eval/unsafe-inline.
    "script-src": ["'self'", CSP.NONCE, CSP.UNSAFE_EVAL],
    "style-src": ["'self'", CSP.NONCE],
    "img-src": ["'self'", "data:", "blob:"],
    "connect-src": ["'self'", "ws:", "wss:"],
    "worker-src": ["'self'"],
    "font-src": ["'self'"],
    "object-src": ["'none'"],
    "frame-ancestors": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
}

MAILERS = {
    "default": {
        "BACKEND": os.environ.get(
            "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
        ),
        "OPTIONS": {
            "host": os.environ.get("DJANGO_EMAIL_HOST", "localhost"),
            "port": int(os.environ.get("DJANGO_EMAIL_PORT", "587")),
            "username": os.environ.get("DJANGO_EMAIL_HOST_USER", ""),
            "password": os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", ""),
            "use_tls": os.environ.get("DJANGO_EMAIL_USE_TLS", "True").lower()
            in ("true", "1", "yes"),
            "timeout": int(os.environ.get("DJANGO_EMAIL_TIMEOUT", "10")),
        },
    },
}

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
