from django.test import TestCase, override_settings

from accounts.models import Hospital

BOOKS = "books.ternahworks.com"
HEALTH = "health.ternahworks.com"
OLD = "shark-app-7ssb2.ondigitalocean.app"


@override_settings(
    ALLOWED_HOSTS=[BOOKS, HEALTH, OLD, "testserver"],
    TERNAH_BOOKS_HOST=BOOKS, TERNAH_HEALTH_HOST=HEALTH, TERNAH_LEGACY_HOSTS=[OLD],
)
class ProductHostTests(TestCase):
    def get(self, host, path):
        return self.client.get(path, HTTP_HOST=host)

    def assertGoesTo(self, response, url):
        self.assertEqual(response.status_code, 302, response)
        self.assertEqual(response["Location"], url)

    def test_the_books_address_opens_books(self):
        self.assertGoesTo(self.get(BOOKS, "/"), "/books/")
        self.assertEqual(self.get(BOOKS, "/books/login/").status_code, 200)

    def test_health_pages_on_the_books_address_go_to_health(self):
        self.assertGoesTo(self.get(BOOKS, "/reception/?x=1"), f"https://{HEALTH}/reception/?x=1")

    def test_books_keeps_the_pages_its_accounts_use_outside_books(self):
        for path in ("/welcome/", "/offline/", "/static/css/thermal-receipt.css"):
            response = self.get(BOOKS, path)
            self.assertNotEqual(response.get("Location", ""), f"https://{HEALTH}{path}", path)

    def test_books_links_on_health_go_to_the_books_address(self):
        self.assertGoesTo(self.get(HEALTH, "/books/invoices/?status=open"), f"https://{BOOKS}/books/invoices/?status=open")
        self.assertEqual(self.get(HEALTH, "/").status_code, 200)

    def test_old_address_still_works_for_links_and_printed_qr_codes(self):
        self.assertGoesTo(self.get(OLD, "/books/check-receipt/12-abc/"), f"https://{BOOKS}/books/check-receipt/12-abc/")
        self.assertGoesTo(self.get(OLD, "/reception/patients/545/visits/"), f"https://{HEALTH}/reception/patients/545/visits/")
        self.assertGoesTo(self.get(OLD, "/"), f"https://{HEALTH}/")

    def test_forms_posted_to_an_old_address_are_not_lost(self):
        response = self.client.post("/", {"username": "x", "password": "y"}, HTTP_HOST=OLD)
        self.assertFalse(response.get("Location", "").startswith(f"https://{HEALTH}"), "a POST is handled, not bounced")

    def test_product_addresses_never_pick_a_hospital_by_subdomain(self):
        Hospital.objects.create(name="Books Clinic", subdomain="books")
        response = self.get(HEALTH, "/")
        self.assertIsNone(response.wsgi_request.hospital)
        response = self.client.get("/books/login/", HTTP_HOST=BOOKS)
        self.assertIsNone(response.wsgi_request.hospital)


class ProductHostsOffTests(TestCase):
    def test_nothing_changes_until_the_addresses_are_set(self):
        self.assertNotEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/books/login/").status_code, 200)
