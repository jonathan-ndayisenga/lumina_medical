from django.test import Client, TestCase
from django.urls import reverse


class LoginCsrfTests(TestCase):
    def test_login_page_sets_csrf_cookie(self):
        client = Client(enforce_csrf_checks=True)

        response = client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", client.cookies)

    def test_missing_csrf_uses_friendly_failure_page(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            reverse("login"),
            {"username": "ghost", "password": "ghost"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "We need you to refresh and try again", status_code=403)
        self.assertContains(response, "Open login again", status_code=403)
