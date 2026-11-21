import os
import tempfile
import unittest

os.environ.setdefault("FLASK_SECRET_KEY", "ci-test-secret-key-that-is-long-enough-123456")
os.environ.setdefault("ADMIN_PASSWORD", "ci-test-admin-password")
os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.gettempdir(), "nayadrishti-ci-tests.sqlite3"))
os.environ.setdefault("COOKIE_SECURE", "false")

import app as application

class DashboardTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        self.client = application.app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_login_and_dashboard(self):
        response = self.client.post("/login", data={"password": os.environ["ADMIN_PASSWORD"]})
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NayaDrishti AI", response.data)

    def test_bad_login_does_not_authenticate(self):
        self.client.post("/login", data={"password": "incorrect"})
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)

if __name__ == "__main__":
    unittest.main()
