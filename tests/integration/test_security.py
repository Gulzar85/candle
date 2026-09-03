from django.conf import settings
from django.test import SimpleTestCase


class SecurityHeadersTest(SimpleTestCase):
    def test_x_frame_options(self):
        response = self.client.get("/health/")
        self.assertIn(response["X-Frame-Options"], ("DENY", "SAMEORIGIN"))

    def test_content_type_nosniff(self):
        response = self.client.get("/health/")
        self.assertEqual(response.get("X-Content-Type-Options"), "nosniff")


class CookieSecurityTest(SimpleTestCase):
    def test_csrf_cookie_httponly(self):
        self.client.get("/")
        csrftoken = self.client.cookies.get("csrftoken")
        if csrftoken is not None:
            self.assertEqual(csrftoken["httponly"], settings.CSRF_COOKIE_HTTPONLY)

    def test_session_cookie_secure_setting(self):
        self.assertIsInstance(settings.SESSION_COOKIE_SECURE, bool)
