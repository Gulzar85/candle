"""Tests for config/settings/production.py's own validation and values.

These import the settings module in a fresh subprocess (not via Django's
normal settings machinery) because production.py performs fail-fast
validation — raising ``ImproperlyConfigured`` — at *import* time based on
``os.environ``, and Django's settings object is a process-wide singleton that
cannot be safely reloaded with different environment variables mid-test-run.
A subprocess is slower than an in-process check but is the only reliable way
to exercise "does this module raise/produce these values under this exact
environment" repeatedly and independently within one test session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_REQUIRED_ENV = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "DJANGO_ALLOWED_HOSTS": "example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com",
    "WEBSOCKET_ALLOWED_ORIGINS": "https://example.com",
    "POSTGRES_PASSWORD": "a-real-password",
}


def _run_production_settings(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Import config.settings.production in a subprocess under the given env.

    On success, prints a JSON dict of the values these tests care about. On
    failure, ``ImproperlyConfigured`` propagates as a non-zero exit with the
    message on stderr.
    """
    env = {**os.environ, **_REQUIRED_ENV, **env_overrides}
    script = (
        "import django, json; "
        "import os; os.environ['DJANGO_SETTINGS_MODULE']='config.settings.production'; "
        "django.setup(); "
        "from django.conf import settings; "
        "print(json.dumps({"
        "'db_connect_timeout': settings.DATABASES['default']['OPTIONS'].get('connect_timeout'),"
        "'cache_connect_timeout': settings.CACHES['default']['OPTIONS'].get('socket_connect_timeout'),"
        "'cache_socket_timeout': settings.CACHES['default']['OPTIONS'].get('socket_timeout'),"
        "}))"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestProductionSettingsGuards:
    """Validation that production.py fails fast on missing required config."""

    @pytest.mark.parametrize(
        "missing_var",
        [
            "DJANGO_SECRET_KEY",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "WEBSOCKET_ALLOWED_ORIGINS",
            "POSTGRES_PASSWORD",
        ],
    )
    def test_raises_when_required_var_missing(self, missing_var: str) -> None:
        env = {**_REQUIRED_ENV, missing_var: ""}
        result = _run_production_settings(env)
        assert result.returncode != 0, f"expected failure with {missing_var} unset"
        assert "ImproperlyConfigured" in result.stderr

    def test_raises_when_secret_key_shorter_than_50_chars(self) -> None:
        result = _run_production_settings({"DJANGO_SECRET_KEY": "x" * 32})
        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert "50 characters" in result.stderr

    def test_loads_cleanly_with_all_required_vars_set(self) -> None:
        result = _run_production_settings({})
        assert result.returncode == 0, result.stderr


class TestProductionConnectionTimeouts:
    """Regression test for the connect-timeout fix (see phase-8 audit addendum).

    Before this fix, neither the PostgreSQL nor the Redis connection had a
    configured timeout, so a genuinely unreachable host could hang a request
    (observed: 14+ seconds against /health/ready/) instead of failing fast.
    """

    def test_database_connect_timeout_configured(self) -> None:
        result = _run_production_settings({})
        assert result.returncode == 0, result.stderr
        values = json.loads(result.stdout)
        assert values["db_connect_timeout"] == 5

    def test_database_connect_timeout_overridable(self) -> None:
        result = _run_production_settings({"POSTGRES_CONNECT_TIMEOUT": "3"})
        assert result.returncode == 0, result.stderr
        values = json.loads(result.stdout)
        assert values["db_connect_timeout"] == 3

    def test_cache_socket_timeouts_configured(self) -> None:
        result = _run_production_settings({})
        assert result.returncode == 0, result.stderr
        values = json.loads(result.stdout)
        assert values["cache_connect_timeout"] == 5
        assert values["cache_socket_timeout"] == 5
