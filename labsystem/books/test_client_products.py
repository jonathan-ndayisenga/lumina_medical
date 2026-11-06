from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital, User
from books.models import Account, Client, ClientProduct, Invoice, Product


class ClientProductTests(TestCase):
    def setUp(self):
        tenant, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.admin = User.objects.create_user(username="products-admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=tenant, is_active=True)
        self.clerk = User.objects.create_user(username="products-clerk", password="x", role=User.ROLE_RECEPTIONIST, hospital=tenant, is_active=True)
        Account.objects.create(code="1200", name="Receivables", type=Account.TYPE_ASSET, role=Account.ROLE_AR)
        self.income = Account.objects.create(code="4000", name="Software income", type=Account.TYPE_INCOME)
        self.hosting_income = Account.objects.create(code="4100", name="Hosting income", type=Account.TYPE_INCOME)
        self.hms = Product.objects.create(name="Ternah Health", code="hms", revenue_account=self.income, default_price=Decimal("500000"))
        self.hosting = Product.objects.create(name="Hosting", code="hosting", revenue_account=self.hosting_income, default_price=Decimal("350000"))
        self.old = Product.objects.create(name="Old product", code="old", revenue_account=self.income, active=False)
        self.acme = Client.objects.create(name="Acme Ltd")
        self.other = Client.objects.create(name="Other Ltd")
        self.client.force_login(self.admin)

    def _add(self, product, price="", client=None, **extra):
        data = {"product": product.pk, "price": price, "quantity": "1", "start_date": "2026-01-01", "active": "on", **extra}
        return self.client.post(reverse("books:client_product_add", args=[(client or self.acme).pk]), data)

    def _new_invoice_page(self, client=None):
        return self.client.get(reverse("books:invoice_create"), {"client": (client or self.acme).pk})

    def _post_invoice_from_page(self, page, **changes):
        """Sends the form back exactly as the page filled it in."""
        formset = page.context["formset"]
        data = {
            "client": page.context["form"].initial["client"].pk, "kind": Invoice.KIND_SUBSCRIPTION, "issue_date": "2026-11-05", "currency": "UGX",
            f"{formset.prefix}-TOTAL_FORMS": formset.total_form_count(), f"{formset.prefix}-INITIAL_FORMS": 0,
            f"{formset.prefix}-MIN_NUM_FORMS": 0, f"{formset.prefix}-MAX_NUM_FORMS": 1000,
        }
        for form in formset:
            for name in ("product", "description", "quantity", "unit_price"):
                value = form[name].value()
                if value is not None:
                    data[form.add_prefix(name)] = str(value)
        data.update({key.replace("PREFIX", formset.prefix): value for key, value in changes.items()})
        return self.client.post(reverse("books:invoice_create"), data)

    def test_blank_price_uses_the_products_usual_price_and_a_clients_own_price_is_kept(self):
        self._add(self.hms)
        self._add(self.hosting, price="200000")
        self.assertEqual(ClientProduct.objects.get(product=self.hms).price, Decimal("500000"))
        self.assertEqual(ClientProduct.objects.get(product=self.hosting).price, Decimal("200000"))
        response = self.client.get(reverse("books:client_detail", args=[self.acme.pk]))
        self.assertContains(response, "Ternah Health")
        self.assertContains(response, "200,000")

    def test_changing_a_products_usual_price_leaves_agreed_prices_alone(self):
        self._add(self.hms)
        self.hms.default_price = Decimal("900000")
        self.hms.save()
        self.assertEqual(ClientProduct.objects.get(product=self.hms).price, Decimal("500000"))

    def test_same_product_twice_is_refused_and_inactive_products_are_not_offered(self):
        self._add(self.hms)
        self.assertContains(self._add(self.hms), "already has this product")
        self.assertEqual(ClientProduct.objects.count(), 1)
        self._add(self.old)
        self.assertFalse(ClientProduct.objects.filter(product=self.old).exists())

    def test_products_page_shows_who_uses_what(self):
        self._add(self.hms)
        self._add(self.hms, client=self.other)
        response = self.client.get(reverse("books:product_list"))
        self.assertContains(response, "Acme Ltd")
        self.assertContains(response, "Other Ltd")
        self.assertContains(response, "500,000")

    def test_new_invoice_starts_with_only_this_clients_current_products(self):
        self._add(self.hms)
        self._add(self.hosting, price="200000")
        self._add(self.hosting, client=self.other)
        row = ClientProduct.objects.get(client=self.acme, product=self.hms)
        row.active = False
        row.save()
        page = self._new_invoice_page()
        self.assertEqual(page.context["client_products_loaded"], 1)
        self.assertEqual(page.context["formset"].total_form_count(), 2)
        self.assertEqual(page.context["formset"].forms[0].initial["unit_price"], Decimal("200000"))

    def test_issuing_the_filled_in_invoice_prints_the_products(self):
        self._add(self.hms, quantity="2")
        ClientProduct.objects.filter(product=self.hms).update(quantity=Decimal("2"))
        self._add(self.hosting, price="200000")
        response = self._post_invoice_from_page(self._new_invoice_page())
        invoice = Invoice.objects.get()
        self.assertRedirects(response, reverse("books:invoice_detail", args=[invoice.pk]), fetch_redirect_response=False)
        self.assertEqual(invoice.status, Invoice.STATUS_OPEN)
        self.assertEqual(invoice.total, Decimal("1200000"))
        self.assertEqual(invoice.lines.get(product=self.hms).quantity, Decimal("2"))
        self.assertEqual(self.income.balance(), Decimal("1000000"))
        self.assertEqual(self.hosting_income.balance(), Decimal("200000"), "each product's income goes to its own account")
        printed = self.client.get(reverse("books:invoice_pdf", args=[invoice.pk]))
        self.assertContains(printed, "Ternah Health")
        self.assertContains(printed, "Hosting")
        self.assertContains(printed, "1,200,000")

    def test_a_product_can_be_changed_or_removed_for_this_invoice_only(self):
        self._add(self.hms)
        self._add(self.hosting)
        response = self._post_invoice_from_page(self._new_invoice_page(), **{"PREFIX-0-unit_price": "1000", "PREFIX-1-DELETE": "on"})
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.total, Decimal("1000"))
        self.assertEqual(ClientProduct.objects.get(product=self.hms).price, Decimal("500000"))
        self.assertEqual(ClientProduct.objects.filter(client=self.acme).count(), 2)

    def test_client_with_no_products_or_a_bad_link_gets_the_normal_blank_invoice(self):
        self.assertEqual(self._new_invoice_page().context["formset"].total_form_count(), 1)
        for value in ("abc", "999999"):
            response = self.client.get(reverse("books:invoice_create"), {"client": value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["formset"].total_form_count(), 1)

    def test_stopping_records_the_date_and_restarting_clears_it(self):
        self._add(self.hms)
        row = ClientProduct.objects.get()
        url = reverse("books:client_product_edit", args=[row.pk])
        self.client.post(url, {"product": self.hms.pk, "price": "500000", "quantity": "1", "start_date": "2026-01-01"})
        row.refresh_from_db()
        self.assertFalse(row.active)
        self.assertIsNotNone(row.end_date)
        self.client.post(url, {"product": self.hms.pk, "price": "500000", "quantity": "1", "start_date": "2026-01-01", "active": "on"})
        row.refresh_from_db()
        self.assertTrue(row.active)
        self.assertIsNone(row.end_date)

    def test_product_code_is_made_from_the_name_when_left_blank(self):
        data = {"name": "Ternah Health", "default_price": "1", "revenue_account": self.income.pk, "active": "on"}
        self.client.post(reverse("books:product_create"), data)
        self.assertEqual(Product.objects.get(default_price=Decimal("1")).code, "ternah-health")

    def test_clerks_can_see_products_and_assign_them_but_not_edit_the_catalogue(self):
        self.client.force_login(self.clerk)
        self.assertNotContains(self.client.get(reverse("books:product_list")), "New product")
        self.assertEqual(self.client.get(reverse("books:product_create")).status_code, 403)
        self._add(self.hms)
        self.assertTrue(ClientProduct.objects.filter(client=self.acme).exists())

    def test_hospital_staff_cannot_open_any_of_it(self):
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        self.client.force_login(User.objects.create_user(username="clinic", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True))
        for url in (reverse("books:product_list"), reverse("books:client_product_add", args=[self.acme.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)


class MobileMenuTests(TestCase):
    def test_every_books_page_has_the_slide_out_menu(self):
        tenant, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.client.force_login(User.objects.create_user(username="phone", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=tenant, is_active=True))
        for name in ("books:dashboard", "books:invoice_list", "books:client_list", "books:product_list"):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'aria-controls="books-nav"')
            self.assertContains(response, 'id="books-nav"')
            self.assertContains(response, 'class="nav-backdrop"')
