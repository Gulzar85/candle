"""Registration flow tests."""

from django.core import mail

from apps.accounts.models import User

from .base import AccountTestCase

VALID_DATA = {
    "email": "alice@example.com",
    "first_name": "Alice",
    "last_name": "Smith",
    "display_name": "Alice",
    "password1": "S3curePass!23",
    "password2": "S3curePass!23",
    "terms": "on",
}


class RegistrationTest(AccountTestCase):
    def test_creates_unverified_user_and_profile(self):
        response = self.client.post("/accounts/register/", VALID_DATA)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/verify_sent.html")

        user = User.objects.get(email="alice@example.com")
        self.assertFalse(user.email_verified)
        self.assertTrue(user.check_password("S3curePass!23"))
        self.assertEqual(user.profile.display_name, "Alice")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Verify your Candle email", mail.outbox[0].subject)

    def test_email_is_normalized(self):
        data = dict(VALID_DATA, email="Alice@Example.COM")
        self.client.post("/accounts/register/", data)
        self.assertTrue(User.objects.filter(email="alice@example.com").exists())

    def test_password_mismatch_rejected(self):
        data = dict(VALID_DATA, password2="Different!23")
        response = self.client.post("/accounts/register/", data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertEqual(User.objects.count(), 0)

    def test_duplicate_email_rejected(self):
        User.objects.create_user(email="alice@example.com", password="SomePass!23")
        response = self.client.post("/accounts/register/", VALID_DATA)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertEqual(User.objects.count(), 1)

    def test_terms_required(self):
        data = dict(VALID_DATA)
        data.pop("terms")
        response = self.client.post("/accounts/register/", data)
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertEqual(User.objects.count(), 0)

    def test_weak_password_rejected(self):
        data = dict(VALID_DATA, password1="password", password2="password")
        response = self.client.post("/accounts/register/", data)
        self.assertTemplateUsed(response, "accounts/register.html")
        self.assertEqual(User.objects.count(), 0)

    def test_authenticated_user_redirected(self):
        user = User.objects.create_user(email="bob@example.com", password="SomePass!23")
        self.client.force_login(user)
        response = self.client.get("/accounts/register/")
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, "/accounts/dashboard/", fetch_redirect_response=False)
