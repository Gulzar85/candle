from django.test import SimpleTestCase
from django.urls import resolve, reverse


class HealthURLTest(SimpleTestCase):
    def test_health_url_resolves(self):
        match = resolve("/health/")
        self.assertEqual(match.func.__name__, "health_check")

    def test_health_url_reverse(self):
        self.assertEqual(reverse("core:health-check"), "/health/")


class HomeURLTest(SimpleTestCase):
    def test_home_url_resolves(self):
        match = resolve("/")
        self.assertEqual(match.func.__name__, "home")

    def test_home_url_reverse(self):
        self.assertEqual(reverse("core:home"), "/")


class AdminURLTest(SimpleTestCase):
    def test_admin_url_resolves(self):
        match = resolve("/admin/")

        self.assertEqual(match.func.__name__, "index")
