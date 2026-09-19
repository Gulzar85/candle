"""Password reset flow tests."""

from django.core import mail

from apps.accounts.models import User

from .base import AccountTestCase, extract_reset_uid_token

PASSWORD = "S3curePass!23"


class PasswordResetTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="bob@example.com", password=PASSWORD)
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

    def test_request_reset_sends_email(self):
        response = self.client.post("/accounts/password-reset/", {"email": "bob@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response, "/accounts/password-reset/done/", fetch_redirect_response=False
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("password", mail.outbox[0].subject.lower())

    def test_unknown_email_no_error(self):
        # Enumeration-resistant: no failure even for unknown addresses.
        response = self.client.post("/accounts/password-reset/", {"email": "nobody@example.com"})
        self.assertRedirects(
            response, "/accounts/password-reset/done/", fetch_redirect_response=False
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_flow(self):
        self.client.post("/accounts/password-reset/", {"email": "bob@example.com"})
        uid, token = extract_reset_uid_token(mail.outbox[0].body)
        url = f"/accounts/password-reset/{uid}/{token}/"

        # A valid token redirects (302) to a token-free URL to avoid leaking the
        # token via the HTTP Referer header.
        first = self.client.get(url)
        self.assertEqual(first.status_code, 302)
        self.assertTrue(first["Location"].endswith("/set-password/"))
        form_url = first["Location"]

        get_resp = self.client.get(form_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Choose a new password")

        post_resp = self.client.post(
            form_url,
            {"new_password1": "NewS3curePass!1", "new_password2": "NewS3curePass!1"},
        )
        self.assertRedirects(
            post_resp, "/accounts/password-reset/complete/", fetch_redirect_response=False
        )

        user = User.objects.get(email="bob@example.com")
        self.assertTrue(user.check_password("NewS3curePass!1"))

        login = self.client.post(
            "/accounts/login/",
            {"username": "bob@example.com", "password": "NewS3curePass!1"},
        )
        self.assertRedirects(login, "/accounts/dashboard/", fetch_redirect_response=False)

    def test_invalid_link_shows_invalid(self):
        response = self.client.get("/accounts/password-reset/not-a-uid/bad-token/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reset link invalid")

    def test_reset_rate_limited_after_four_requests(self):
        for _ in range(3):
            self.client.post("/accounts/password-reset/", {"email": "bob@example.com"})
        response = self.client.post("/accounts/password-reset/", {"email": "bob@example.com"})
        self.assertEqual(response.status_code, 429)

    def test_reset_confirm_rate_limited_after_six_attempts(self):
        self.client.post("/accounts/password-reset/", {"email": "bob@example.com"})
        uid, token = extract_reset_uid_token(mail.outbox[0].body)
        first = self.client.get(f"/accounts/password-reset/{uid}/{token}/")
        form_url = first["Location"]

        # Mismatched passwords fail form validation without consuming the
        # token, so the same link can be POSTed repeatedly to exercise the
        # rate limiter without ever completing a real reset.
        for _ in range(6):
            response = self.client.post(
                form_url,
                {"new_password1": "a", "new_password2": "b"},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            form_url,
            {"new_password1": "a", "new_password2": "b"},
        )
        self.assertEqual(response.status_code, 429)
