"""Password change flow tests."""

from apps.accounts.models import User

from .base import AccountTestCase

PASSWORD = "S3curePass!23"


class PasswordChangeTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="bob@example.com", password=PASSWORD)
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        self.client.force_login(self.user)

    def test_change_password_success(self):
        response = self.client.post(
            "/accounts/password-change/",
            {
                "old_password": PASSWORD,
                "new_password1": "NewS3curePass!1",
                "new_password2": "NewS3curePass!1",
            },
        )
        self.assertRedirects(
            response, "/accounts/password-change/done/", fetch_redirect_response=False
        )
        user = User.objects.get(email="bob@example.com")
        self.assertTrue(user.check_password("NewS3curePass!1"))

    def test_change_password_keeps_and_rotates_session(self):
        before_hash = self.client.session.get("_auth_user_hash")
        self.client.post(
            "/accounts/password-change/",
            {
                "old_password": PASSWORD,
                "new_password1": "NewS3curePass!1",
                "new_password2": "NewS3curePass!1",
            },
        )
        # The auth hash is rotated (old sessions invalidated) but the user
        # stays signed in because the current session is rekeyed to the new hash.
        self.assertIn("_auth_user_id", self.client.session)
        self.assertIn("_auth_user_hash", self.client.session)
        self.assertNotEqual(self.client.session["_auth_user_hash"], before_hash)

    def test_wrong_old_password_rejected(self):
        response = self.client.post(
            "/accounts/password-change/",
            {
                "old_password": "wrong-old",
                "new_password1": "NewS3curePass!1",
                "new_password2": "NewS3curePass!1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/password_change.html")
        user = User.objects.get(email="bob@example.com")
        self.assertTrue(user.check_password(PASSWORD))
