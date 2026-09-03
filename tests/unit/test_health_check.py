from django.test import SimpleTestCase


class HealthCheckTest(SimpleTestCase):
    def test_health_check_returns_200(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)

    def test_health_check_status_ok(self):
        response = self.client.get("/health/")
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_health_check_has_version(self):
        response = self.client.get("/health/")
        data = response.json()
        self.assertIn("version", data)
