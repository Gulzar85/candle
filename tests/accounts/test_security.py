"""CSRF protection for authentication/account endpoints."""

from django.test import Client

from apps.accounts.models import User

from .base import AccountTestCase


class AuthCSRFTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.csrf_client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(email="bob@example.com", password="S3curePass!23")
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

    def test_register_requires_csrf(self):
        response = self.csrf_client.post("/accounts/register/", {"email": "a@b.com"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 1)  # only setUp's user

    def test_login_requires_csrf(self):
        response = self.csrf_client.post(
            "/accounts/login/", {"username": "bob@example.com", "password": "S3curePass!23"}
        )
        self.assertEqual(response.status_code, 403)

    def test_logout_requires_csrf(self):
        self.csrf_client.force_login(self.user)
        response = self.csrf_client.post("/accounts/logout/")
        self.assertEqual(response.status_code, 403)
        # Session still authenticated because the POST was rejected.
        self.assertIn("_auth_user_id", self.csrf_client.session)

    def test_password_reset_requires_csrf(self):
        response = self.csrf_client.post("/accounts/password-reset/", {"email": "bob@example.com"})
        self.assertEqual(response.status_code, 403)
