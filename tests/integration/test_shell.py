from django.test import TestCase


class HomePageTest(TestCase):
    def test_home_returns_200(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_home_contains_candle(self):
        response = self.client.get("/")
        self.assertContains(response, "Candle")

    def test_home_has_main_content_id(self):
        response = self.client.get("/")
        self.assertContains(response, 'id="main-content"')

    def test_home_content_type_is_html(self):
        response = self.client.get("/")
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")

    def test_home_has_csrf_token_meta_tag(self):
        response = self.client.get("/")
        self.assertContains(response, '<meta name="csrf-token"')
