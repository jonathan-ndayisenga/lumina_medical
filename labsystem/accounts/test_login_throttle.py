from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import login_throttle
from accounts.models import LoginThrottle, User


class LoginThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="throttle-user", password="Correct-pass-123", role=User.ROLE_SUPERADMIN, is_active=True,
        )

    def _login(self, password, username="throttle-user", url_name="login", ip="10.0.0.1"):
        return self.client.post(
            reverse(url_name), {"username": username, "password": password}, REMOTE_ADDR=ip,
        )

    def test_username_locks_after_five_failures_even_with_the_right_password(self):
        for _ in range(login_throttle.MAX_USER_FAILURES):
            self._login("wrong")
        response = self._login("Correct-pass-123")
        self.assertContains(response, "Too many failed sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_locked_attempts_never_check_the_password(self):
        for _ in range(login_throttle.MAX_USER_FAILURES):
            self._login("wrong")
        with mock.patch("django.contrib.auth.forms.authenticate") as authenticate:
            self._login("Correct-pass-123")
        authenticate.assert_not_called()

    def test_lock_expires(self):
        for _ in range(login_throttle.MAX_USER_FAILURES):
            self._login("wrong")
        LoginThrottle.objects.update(locked_until=timezone.now() - timedelta(seconds=1))
        response = self._login("Correct-pass-123")
        self.assertEqual(response.status_code, 302)

    def test_success_clears_the_username_counter_but_not_the_ip_counter(self):
        self._login("wrong")
        self._login("wrong")
        self._login("Correct-pass-123")
        self.assertFalse(LoginThrottle.objects.filter(key="user:throttle-user").exists())
        self.assertEqual(LoginThrottle.objects.get(key="ip:10.0.0.1").failures, 2)

    def test_one_address_trying_many_usernames_is_locked(self):
        for i in range(login_throttle.MAX_IP_FAILURES):
            self._login("wrong", username=f"guess-{i}")
        response = self._login("Correct-pass-123")
        self.assertContains(response, "Too many failed sign-in attempts")
        # A different address can still sign in to the account.
        response = self._login("Correct-pass-123", ip="10.0.0.2")
        self.assertEqual(response.status_code, 302)

    def test_spoofed_forwarded_for_header_is_ignored(self):
        for i in range(login_throttle.MAX_USER_FAILURES):
            self.client.post(
                reverse("login"), {"username": "throttle-user", "password": "wrong"},
                REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR=f"1.2.3.{i}",
            )
        self.assertEqual(LoginThrottle.objects.get(key="ip:10.0.0.1").failures, login_throttle.MAX_USER_FAILURES)

    def test_books_and_admin_logins_are_throttled_too(self):
        for _ in range(login_throttle.MAX_USER_FAILURES):
            self._login("wrong", url_name="books:login")
        self.assertContains(self._login("Correct-pass-123", url_name="books:login"), "Too many failed sign-in attempts")
        self.assertContains(
            self._login("Correct-pass-123", url_name="admin:login"), "Too many failed sign-in attempts",
        )

    def test_login_page_has_the_password_eye(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "data-password-field")
        self.assertContains(response, "js/password-field.js")
