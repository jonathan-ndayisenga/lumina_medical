import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from accounts.models import Hospital, HospitalModuleSubscription, Module, User
from reception.models import Patient, QueueEntry, Service, Visit, VisitService

SECRET_NAME = "Zzsecret Otherhospital Patient"

# Pages meant to be open to anyone. Everything else must ask for a login.
PUBLIC_URL_NAMES = {
    "login", "landing", "pwa_manifest", "service_worker", "pwa_offline",
    "books:login", "admin:login", "logout",
}


def walk(patterns, namespace="", prefix=""):
    for entry in patterns:
        if isinstance(entry, URLResolver):
            child = f"{namespace}{entry.namespace}:" if entry.namespace else namespace
            yield from walk(entry.url_patterns, child, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern) and entry.name:
            yield f"{namespace}{entry.name}", prefix + str(entry.pattern), entry.pattern.converters


def sample_kwargs(pattern, converters, pk):
    names = {}
    for name, converter in converters.items():
        kind = type(converter).__name__
        if kind == "IntConverter":
            names[name] = pk
        elif kind == "UUIDConverter":
            names[name] = "00000000-0000-0000-0000-000000000000"
        else:
            names[name] = "lab"
    return names


class UrlSecurityTests(TestCase):
    def _urls(self, pk=1):
        for name, pattern, converters in walk(get_resolver().url_patterns):
            if name.startswith("admin:") or "<path:" in pattern or "(?P<" in pattern:
                continue
            try:
                yield name, reverse(name, kwargs=sample_kwargs(pattern, converters, pk))
            except Exception:
                continue

    def test_no_page_opens_without_signing_in(self):
        open_pages = []
        for name, url in self._urls():
            if name in PUBLIC_URL_NAMES:
                continue
            response = self.client.get(url)
            if response.status_code == 200:
                open_pages.append(f"{name} {url}")
        self.assertEqual(open_pages, [], "These pages opened with nobody signed in")

    def _seed_two_hospitals(self):
        modules = ["doctor", "lab", "nurse", "sonographer", "finance", "reception", "hospital_mgmt", "pharmacy", "homecare"]

        def build(name):
            hospital = Hospital.objects.create(name=name, subdomain=name.lower())
            for code in modules:
                module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
                HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})
            return hospital

        mine, theirs = build("Mine"), build("Theirs")
        patient = Patient.objects.create(hospital=theirs, name=SECRET_NAME, registration_date=timezone.localdate(), age="30YRS", sex="M")
        visit = Visit.objects.create(patient=patient, hospital=theirs, status=Visit.STATUS_IN_PROGRESS, total_amount=Decimal("0"))
        service = Service.objects.create(hospital=theirs, name="Theirs Service", category=Service.CATEGORY_LAB, price=Decimal("1000"), is_active=True)
        VisitService.objects.create(visit=visit, service=service, price_at_time=Decimal("1000"), is_approved=True)
        QueueEntry.objects.create(hospital=theirs, visit=visit, queue_type=QueueEntry.TYPE_RECEPTION, reason="x")
        return mine, theirs, patient, visit

    def _pages_showing_the_patient(self, user, patient, visit):
        self.client.force_login(user)
        self.client.raise_request_exception = False
        found = []
        for pk in {patient.pk, visit.pk}:
            for name, url in self._urls(pk):
                if not any(c.isdigit() for c in url):
                    continue
                response = self.client.get(url)
                if response.status_code == 200 and SECRET_NAME.encode() in response.content:
                    found.append(f"{name} {url}")
        return found

    def test_staff_cannot_read_another_hospitals_records_by_changing_the_number(self):
        mine, theirs, patient, visit = self._seed_two_hospitals()
        outsider = User.objects.create_user(username="mine-admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=mine, is_active=True)
        insider = User.objects.create_user(username="theirs-admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=theirs, is_active=True)
        self.assertEqual(self._pages_showing_the_patient(outsider, patient, visit), [], "These pages showed another hospital's patient")
        # The check above only means something if these pages really do show the patient to the right people.
        self.assertGreater(len(self._pages_showing_the_patient(insider, patient, visit)), 3)

    def test_the_audit_really_walks_the_site(self):
        self.assertGreater(len(list(self._urls())), 200)


class BooksLoginBoundaryTests(TestCase):
    def setUp(self):
        self.books, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.user = User.objects.create_user(
            username="books-owner", password="Correct-pass-123", role=User.ROLE_HOSPITAL_ADMIN, hospital=self.books, is_active=True,
        )

    def test_signing_in_at_either_page_goes_straight_to_books(self):
        for page in ("login", "books:login"):
            self.client.logout()
            response = self.client.post(reverse(page), {"username": "books-owner", "password": "Correct-pass-123"})
            self.assertRedirects(response, reverse("books:dashboard"), fetch_redirect_response=False, msg_prefix=page)

    def test_books_account_cannot_wander_into_the_hospital_system(self):
        self.client.force_login(self.user)
        for url in ("/", "/home/", "/platform/", "/reception/", "/doctor/", "/lab/", "/finance/", "/admin/"):
            self.assertRedirects(self.client.get(url), reverse("books:dashboard"), fetch_redirect_response=False, msg_prefix=url)
        self.assertIn("_auth_user_id", self.client.session, "going to the wrong page must not sign them out")
        self.assertEqual(self.client.get(reverse("books:dashboard")).status_code, 200)

    def test_books_is_not_stopped_by_a_hospital_subscription_date(self):
        self.books.subscription_end_date = date(2020, 1, 1)
        self.books.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("books:dashboard")).status_code, 200)
        self.books.refresh_from_db()
        self.assertTrue(self.books.is_active)

    def test_books_login_page_skips_the_form_when_already_signed_in(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("books:login")), reverse("books:dashboard"), fetch_redirect_response=False)

    def test_signing_out_of_books_is_still_allowed(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.post(reverse("books:logout")), reverse("books:login"), fetch_redirect_response=False)

    def test_hospital_staff_are_not_affected_and_cannot_open_books(self):
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        User.objects.create_user(username="clinic-admin", password="Correct-pass-123", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True)
        response = self.client.post(reverse("login"), {"username": "clinic-admin", "password": "Correct-pass-123"})
        self.assertRedirects(response, reverse("app_home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("books:invoice_list")).status_code, 403)
        self.client.logout()
        response = self.client.post(reverse("books:login"), {"username": "clinic-admin", "password": "Correct-pass-123"})
        self.assertContains(response, "does not have access to Ternah Books")


class UploadedFileAccessTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name)
        override.enable()
        self.addCleanup(override.disable)
        for folder, name in (("books/receipts", "rent.pdf"), ("hospital_logos", "logo.png"), ("private", "x.txt")):
            Path(self.media.name, folder).mkdir(parents=True)
            Path(self.media.name, folder, name).write_bytes(b"secret-bytes")
        books, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.books_user = User.objects.create_user(username="b", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=books, is_active=True)
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        self.clinic_user = User.objects.create_user(username="c", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True)

    def test_books_files_are_for_books_staff_only(self):
        url = "/media/books/receipts/rent.pdf"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.clinic_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.books_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Content-Security-Policy"], "sandbox")

    def test_logos_stay_open_and_everything_else_is_closed(self):
        self.assertEqual(self.client.get("/media/hospital_logos/logo.png").status_code, 200)
        self.assertEqual(self.client.get("/media/private/x.txt").status_code, 404)
        self.client.force_login(self.books_user)
        self.assertEqual(self.client.get("/media/private/x.txt").status_code, 404)

    def test_climbing_out_of_a_public_folder_finds_nothing(self):
        for path in ("/media/hospital_logos/../books/receipts/rent.pdf", "/media/hospital_logos/..%2Fbooks%2Freceipts%2Frent.pdf"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)


class PrivatePagesTests(TestCase):
    def test_signed_in_pages_are_not_kept_by_the_browser(self):
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        user = User.objects.create_user(username="c", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/home/")["Cache-Control"], "private, no-store")
