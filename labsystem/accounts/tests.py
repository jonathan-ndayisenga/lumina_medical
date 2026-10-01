from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Hospital, SubscriptionPlan, User


class HospitalReportCodeTests(TestCase):
    """report_code_display drives the short code printed on patient/visit
    numbers -- must never fall back to the raw subdomain, which can be a
    long deploy-generated slug."""

    def test_falls_back_to_initials_when_report_code_blank(self):
        hospital = Hospital.objects.create(name="Lumina Medical Services", subdomain="shark-app-7ssb2")
        self.assertEqual(hospital.report_code_display, "LMS")

    def test_explicit_report_code_wins_and_is_uppercased(self):
        hospital = Hospital.objects.create(
            name="Lumina Medical Services", subdomain="shark-app-7ssb2", report_code="lms",
        )
        self.assertEqual(hospital.report_code_display, "LMS")


class LoginCsrfTests(TestCase):
    def test_login_page_sets_csrf_cookie(self):
        client = Client(enforce_csrf_checks=True)

        response = client.get("/")

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
        # LOGIN_URL = "/" so the redirect goes to /?next=... (the root custom login page)
        self.assertIn("/?next=", response.headers["Location"])
        self.assertNotIn("_auth_user_id", client.session)


class HomeStatusBoardTests(TestCase):
    """Home screen redesign: live per-department tiles for the hospital admin."""

    def setUp(self):
        from django.core.cache import cache
        from accounts.models import HospitalModuleSubscription, Module

        cache.clear()
        self.hospital = Hospital.objects.create(name="Lumina Medical", subdomain="lumina-home")
        self.other_hospital = Hospital.objects.create(name="Other Clinic", subdomain="other-home")
        for hospital in (self.hospital, self.other_hospital):
            for code in ("hospital_mgmt", "reception", "doctor", "inventory", "finance"):
                module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
                HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})
        self.admin = User.objects.create_user(
            username="lumina_admin", password="StrongPass123!", first_name="Jonathan",
            role=User.ROLE_HOSPITAL_ADMIN, hospital=self.hospital,
        )
        self.client.force_login(self.admin)

    def _queue_patient(self, hospital, queue_type, minutes_ago=0):
        from reception.models import Patient, QueueEntry, Visit

        patient = Patient.objects.create(hospital=hospital, name="Test Patient", age="30YRS", sex="M")
        visit = Visit.objects.create(patient=patient, hospital=hospital, created_by=self.admin)
        entry = QueueEntry.objects.create(hospital=hospital, visit=visit, queue_type=queue_type)
        if minutes_ago:
            QueueEntry.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
        return entry

    def test_greeting_uses_first_name_and_falls_back_to_role(self):
        response = self.client.get(reverse("app_home"))
        self.assertContains(response, "Jonathan")
        self.assertNotContains(response, "lumina_admin</h1>")

        self.admin.first_name = ""
        self.admin.save()
        response = self.client.get(reverse("app_home"))
        self.assertEqual(response.context["greeting_name"], "Hospital Admin")

    def test_sidebar_and_tiles_list_only_accessible_modules(self):
        response = self.client.get(reverse("app_home"))
        keys = [tile["key"] for tile in response.context["tiles"]]
        self.assertEqual(keys, ["reception", "doctor", "inventory", "finance", "hospital_admin"])
        self.assertEqual([item["label"] for item in response.context["home_nav"]],
                         ["Reception", "Doctor", "Inventory", "Finance", "Hospital admin"])
        self.assertContains(response, reverse("enter_nav_section", args=["doctor"]))
        self.assertNotContains(response, "Laboratory")

    def test_tiles_start_in_loading_state_and_poll_their_endpoint(self):
        response = self.client.get(reverse("app_home"))
        self.assertContains(response, "tile-skeleton")
        self.assertContains(response, f'hx-get="{reverse("home_tile", args=["doctor"])}"')
        self.assertContains(response, "every 30s")
        self.assertContains(response, 'id="patientSearch"')

    def test_doctor_tile_warns_past_hospital_threshold(self):
        from reception.models import QueueEntry

        self._queue_patient(self.hospital, QueueEntry.TYPE_DOCTOR, minutes_ago=42)
        self._queue_patient(self.hospital, QueueEntry.TYPE_DOCTOR)
        response = self.client.get(reverse("home_tile", args=["doctor"]))
        self.assertContains(response, "patients waiting")
        self.assertContains(response, "42 min")
        self.assertContains(response, "needs attention")

        from django.core.cache import cache
        cache.clear()
        self.hospital.doctor_wait_warn_minutes = 60
        self.hospital.save()
        response = self.client.get(reverse("home_tile", args=["doctor"]))
        self.assertContains(response, "42 min")
        self.assertNotContains(response, "needs attention")

    def test_counts_are_per_hospital(self):
        from reception.models import QueueEntry

        self._queue_patient(self.other_hospital, QueueEntry.TYPE_RECEPTION)
        self._queue_patient(self.other_hospital, QueueEntry.TYPE_RECEPTION)
        self._queue_patient(self.hospital, QueueEntry.TYPE_RECEPTION)
        response = self.client.get(reverse("home_tile", args=["reception"]))
        self.assertEqual(response.context["n"]["value"], 1)

    def test_inventory_tile_warns_on_low_stock(self):
        from decimal import Decimal
        from admin_dashboard.models import InventoryItem

        InventoryItem.objects.create(
            hospital=self.hospital, name="Paracetamol 500mg", category=InventoryItem.CATEGORY_DRUG,
            unit="strip", base_unit="tablet", units_per_pack=Decimal("10"),
            current_quantity=Decimal("2"), reorder_level=Decimal("5"),
        )
        response = self.client.get(reverse("home_tile", args=["inventory"]))
        self.assertEqual(response.context["n"]["value"], 1)
        self.assertTrue(response.context["n"]["warn"])
        self.assertContains(response, "is-warn")

    def test_tile_endpoint_forbidden_without_module_access(self):
        response = self.client.get(reverse("home_tile", args=["lab"]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("home_tile", args=["hospital_admin"])).status_code, 404)

    def test_quick_action_enters_section_then_goes_to_target(self):
        url = reverse("enter_nav_section", args=["reception"])
        response = self.client.get(url, {"next": reverse("reception_queue")})
        self.assertRedirects(response, reverse("reception_queue"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["nav_section"], "reception")

        response = self.client.get(url, {"next": "https://evil.example.com/"})
        self.assertNotEqual(response["Location"], "https://evil.example.com/")

    def test_compact_ugx_formatting(self):
        from accounts.home_tiles import _compact_ugx

        self.assertEqual(_compact_ugx(2_400_000), "UGX 2.4M")
        self.assertEqual(_compact_ugx(3_000_000), "UGX 3M")
        self.assertEqual(_compact_ugx(845_500), "UGX 846K")
        self.assertEqual(_compact_ugx(950), "UGX 950")


class InstallableAppTests(TestCase):
    """Chrome/Edge can install Ternah from the login page as a desktop app."""

    def test_manifest_describes_the_app(self):
        import json

        response = self.client.get(reverse("pwa_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = json.loads(response.content)
        self.assertEqual(manifest["name"], "Ternah Health")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], reverse("app_home"))
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})

    def test_service_worker_is_served_from_the_root_and_only_caches_the_offline_page(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertContains(response, reverse("pwa_offline"))
        self.assertEqual(self.client.get(reverse("pwa_offline")).status_code, 200)

    def test_login_page_links_manifest_and_offers_install(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, f'rel="manifest" href="{reverse("pwa_manifest")}"')
        self.assertContains(response, 'id="installApp"')
        self.assertContains(response, "serviceWorker.register")


class SmoothNavigationTests(TestCase):
    def test_links_load_in_place_and_forms_keep_normal_submits(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, 'hx-boost="true"')
        self.assertContains(response, "htmx.org@2.0.4")
        self.assertContains(response, "head-support")
        # Forms keep full submits so their confirm prompts and checks still apply.
        self.assertContains(response, "const NO_BOOST = 'form, ")
        # Prefetching would double every request now that links load in the background.
        self.assertNotContains(response, 'type="speculationrules"')

    def test_no_template_comment_leaks_onto_the_page(self):
        # Django's {# #} only works on one line; a multi-line one renders as
        # visible text (it showed at the top of every page once).
        from pathlib import Path
        from django.conf import settings

        offenders = [
            f"{path}:{number}"
            for path in Path(settings.BASE_DIR).rglob("*.html")
            if "staticfiles" not in path.parts
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "{#" in line and "#}" not in line
        ]
        self.assertEqual(offenders, [])
        self.assertNotContains(self.client.get(reverse("login")), "{#")
