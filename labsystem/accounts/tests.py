from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Hospital, SubscriptionPlan, User


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


@override_settings(SESSION_IDLE_TIMEOUT_SECONDS=60, SESSION_COOKIE_AGE=60)
class SessionIdleTimeoutTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="Standard",
            price_monthly="0.00",
            price_yearly="0.00",
        )
        hospital = Hospital.objects.create(
            name="Lumina Session Hospital",
            subdomain="lumina-session",
            subscription_plan=plan,
        )
        self.user = User.objects.create_user(
            username="sessionreception",
            password="pass12345",
            role=User.ROLE_RECEPTIONIST,
            hospital=hospital,
            is_active=True,
        )

    def test_active_user_stays_logged_in(self):
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("reception_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", client.session)

    def test_idle_user_is_logged_out_on_next_request(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session["_session_last_activity_ts"] = int((timezone.now() - timedelta(minutes=5)).timestamp())
        session.save()

        response = client.get(reverse("reception_dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.headers["Location"])
        self.assertNotIn("_auth_user_id", client.session)
