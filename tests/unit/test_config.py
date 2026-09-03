from django.conf import settings
from django.test import SimpleTestCase


class InstalledAppsTest(SimpleTestCase):
    def test_required_apps_present(self):
        required = [
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "channels",
            "rest_framework",
            "daphne",
        ]
        for app in required:
            self.assertIn(app, settings.INSTALLED_APPS)


class MiddlewareTest(SimpleTestCase):
    def test_required_middleware_present(self):
        required = [
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ]
        for mw in required:
            self.assertIn(mw, settings.MIDDLEWARE)


class ASGIApplicationTest(SimpleTestCase):
    def test_asgi_application(self):
        self.assertEqual(settings.ASGI_APPLICATION, "config.asgi.application")


class DatabaseTest(SimpleTestCase):
    def test_default_engine(self):
        self.assertEqual(
            settings.DATABASES["default"]["ENGINE"],
            "django.db.backends.postgresql",
        )
