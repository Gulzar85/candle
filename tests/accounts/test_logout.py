"""Logout flow tests."""

from apps.accounts.models import User

from .base import AccountTestCase


class LogoutTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="bob@example.com", password="S3curePass!23")
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

    def test_post_logout_signs_out(self):
        self.client.force_login(self.user)
        self.assertIn("_auth_user_id", self.client.session)
        response = self.client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_get_logout_not_allowed(self):
        self.client.force_login(self.user)
        response = self.client.get("/accounts/logout/")
        self.assertEqual(response.status_code, 405)

    def test_logout_redirects_home(self):
        self.client.force_login(self.user)
        response = self.client.post("/accounts/logout/")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
