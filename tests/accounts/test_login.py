"""Login flow tests: verification gate, remember-me, and rate limiting."""

from unittest.mock import patch

from django.core import mail
from django.utils import timezone

from apps.accounts.models import User

from .base import AccountTestCase

PASSWORD = "S3curePass!23"


class LoginTest(AccountTestCase):
    def _user(self, verified: bool = True) -> User:
        user = User.objects.create_user(email="bob@example.com", password=PASSWORD)
        if verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        return user

    def test_verified_user_logs_in(self):
        self._user()
        response = self.client.post(
            "/accounts/login/",
            {"username": "bob@example.com", "password": PASSWORD},
        )
        self.assertRedirects(response, "/accounts/dashboard/", fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)

    def test_unverified_user_blocked_and_email_sent(self):
        self._user(verified=False)
        response = self.client.post(
            "/accounts/login/",
            {"username": "bob@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Verify your Candle email", mail.outbox[0].subject)

    def test_wrong_password_rejected(self):
        self._user()
        response = self.client.post(
            "/accounts/login/",
            {"username": "bob@example.com", "password": "wrongpassword"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_remember_me_sets_long_session(self):
        self._user()
        self.client.post(
            "/accounts/login/",
            {"username": "bob@example.com", "password": PASSWORD, "remember": "on"},
        )
        self.assertGreaterEqual(self.client.session.get_expiry_age(), 1209600 - 60)

    def test_login_rate_limited_after_seven_attempts(self):
        self._user(verified=False)
        # The ratelimiter keys its counter by the current minute, so pin the
        # clock to one instant to keep all seven attempts in the same bucket
        # (otherwise a minute rollover mid-test makes this flaky under a long run).
        fixed_now = timezone.now()
        with patch("apps.accounts.ratelimit.timezone.now", return_value=fixed_now):
            # 6 attempts are allowed; the 7th trips the limiter -> 429.
            for _ in range(6):
                self.client.post(
                    "/accounts/login/",
                    {"username": "bob@example.com", "password": "bad"},
                )
            response = self.client.post(
                "/accounts/login/",
                {"username": "bob@example.com", "password": "bad"},
            )
        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "accounts/rate_limited.html")

    def test_authenticated_user_redirected_away(self):
        self._user()
        self.client.post(
            "/accounts/login/",
            {"username": "bob@example.com", "password": PASSWORD},
        )
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, "/accounts/dashboard/", fetch_redirect_response=False)
