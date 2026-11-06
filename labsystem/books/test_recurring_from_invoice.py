from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital, User
from books.models import Account, Client, CompanySettings, Invoice, Product, RecurringInvoice


class RecurringFromInvoiceTests(TestCase):
    def setUp(self):
        tenant, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.user = User.objects.create_user(username="plans", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=tenant, is_active=True)
        Account.objects.create(code="1200", name="Receivables", type=Account.TYPE_ASSET, role=Account.ROLE_AR)
        self.software = Account.objects.create(code="4000", name="Software income", type=Account.TYPE_INCOME)
        self.hosting = Account.objects.create(code="4100", name="Hosting income", type=Account.TYPE_INCOME)
        self.product = Product.objects.create(name="Ternah Health", code="health", revenue_account=self.software)
        self.acme = Client.objects.create(name="Acme Ltd")
        self.october = self._invoice(date(2026, 10, 1))
        self.client.force_login(self.user)

    def _invoice(self, issued):
        invoice = Invoice.objects.create(client=self.acme, issue_date=issued, kind=Invoice.KIND_SUBSCRIPTION, apply_vat=False)
        invoice.lines.create(product=self.product, description="Ternah Health licence", quantity=Decimal("1"), unit_price=Decimal("500000"))
        invoice.lines.create(revenue_account=self.hosting, description="Hosting", quantity=Decimal("2"), unit_price=Decimal("100000"))
        return invoice.issue(user=self.user)

    def _make_plan(self, invoice=None, **data):
        payload = {"next_run_date": "2026-11-01", "issue_mode": "draft", **data}
        return self.client.post(reverse("books:invoice_make_plan", args=[(invoice or self.october).pk]), payload)

    def test_plan_copies_every_line_and_starts_the_month_after(self):
        page = self.client.get(reverse("books:invoice_make_plan", args=[self.october.pk]))
        self.assertEqual(page.context["form"].initial["next_run_date"], date(2026, 11, 1))
        self._make_plan()
        plan = RecurringInvoice.objects.get()
        self.assertEqual(plan.source_invoice, self.october)
        self.assertEqual(plan.lines.count(), 2)
        self.assertEqual(plan.monthly_total, Decimal("700000"))
        self.assertEqual(plan.issue_mode, RecurringInvoice.MODE_DRAFT)

    def test_starting_on_or_before_the_invoice_is_refused(self):
        response = self._make_plan(next_run_date="2026-10-01")
        self.assertContains(response, "billed twice")
        self.assertFalse(RecurringInvoice.objects.exists())

    def test_a_month_already_invoiced_needs_a_tick_to_go_ahead(self):
        november = self._invoice(date(2026, 11, 1))
        response = self._make_plan(next_run_date="2026-11-15")
        self.assertContains(response, november.number)
        self.assertFalse(RecurringInvoice.objects.exists())
        self._make_plan(next_run_date="2026-11-15", confirm_month="on")
        self.assertTrue(RecurringInvoice.objects.exists())

    def test_page_points_to_the_newer_invoice_when_this_one_is_old(self):
        november = self._invoice(date(2026, 11, 1))
        response = self.client.get(reverse("books:invoice_make_plan", args=[self.october.pk]))
        self.assertContains(response, f"{november.number} is newer than this one")

    def test_draft_mode_prepares_unnumbered_unposted_invoices_and_catches_up(self):
        self._make_plan()
        plan = RecurringInvoice.objects.get()
        made = plan.generate_due(today=date(2026, 12, 5), user=self.user)
        self.assertEqual([i.issue_date for i in made], [date(2026, 11, 1), date(2026, 12, 1)])
        for invoice in made:
            self.assertEqual(invoice.status, Invoice.STATUS_DRAFT)
            self.assertIsNone(invoice.number)
            self.assertIsNone(invoice.journal_entry_id)
            self.assertEqual(invoice.total, Decimal("700000"))
            self.assertEqual(sorted(l.description for l in invoice.lines.all()), ["Hosting", "Ternah Health licence"])
        self.assertEqual(plan.generate_due(today=date(2026, 12, 5)), [], "never twice")
        self.assertEqual(self.software.balance(), Decimal("500000"), "only the October invoice is in the books")

    def test_issue_mode_issues_and_each_line_keeps_its_income_account(self):
        self._make_plan(issue_mode="issue")
        invoice = RecurringInvoice.objects.get().generate_due(today=date(2026, 11, 1), user=self.user)[0]
        self.assertEqual(invoice.status, Invoice.STATUS_OPEN)
        self.assertTrue(invoice.number)
        self.assertEqual(self.software.balance(), Decimal("1000000"))
        self.assertEqual(self.hosting.balance(), Decimal("400000"))

    def test_changing_the_plan_price_only_affects_future_invoices(self):
        self._make_plan()
        plan = RecurringInvoice.objects.get()
        first = plan.generate_due(today=date(2026, 11, 1))[0]
        lines = list(plan.lines.all())
        data = {"next_run_date": plan.next_run_date.isoformat(), "issue_mode": "draft", "lines-TOTAL_FORMS": 2, "lines-INITIAL_FORMS": 2}
        for i, line in enumerate(lines):
            data.update({f"lines-{i}-id": line.pk, f"lines-{i}-description": line.description, f"lines-{i}-quantity": line.quantity,
                         f"lines-{i}-unit_price": "600000" if i == 0 else line.unit_price})
        self.assertEqual(self.client.post(reverse("books:recurring_edit", args=[plan.pk]), data).status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.monthly_total, Decimal("800000"))
        first.refresh_from_db()
        self.assertEqual(first.total, Decimal("700000"))
        self.assertEqual(plan.generate_due(today=date(2026, 12, 1))[0].total, Decimal("800000"))

    def test_repeat_makes_a_draft_copy_dated_today(self):
        response = self.client.post(reverse("books:invoice_repeat", args=[self.october.pk]))
        copy = Invoice.objects.exclude(pk=self.october.pk).get()
        self.assertRedirects(response, reverse("books:invoice_edit", args=[copy.pk]), fetch_redirect_response=False)
        self.assertEqual(copy.status, Invoice.STATUS_DRAFT)
        self.assertEqual(copy.total, Decimal("700000"))
        self.assertEqual(copy.reference, f"Repeat of {self.october.number}")
        self.assertEqual(self.client.get(reverse("books:invoice_repeat", args=[self.october.pk])).status_code, 405)

    def test_drafts_and_void_invoices_cannot_be_repeated_or_planned(self):
        draft = Invoice.objects.create(client=self.acme, issue_date=date(2026, 10, 2))
        for name in ("books:invoice_repeat", "books:invoice_make_plan"):
            self.client.post(reverse(name, args=[draft.pk]), {"next_run_date": "2026-11-01", "issue_mode": "draft"})
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertFalse(RecurringInvoice.objects.exists())

    def test_old_single_line_plans_keep_working_and_keep_issuing(self):
        plan = RecurringInvoice.objects.create(client=self.acme, description="Hosting", amount=Decimal("350000"),
                                               revenue_account=self.hosting, next_run_date=date(2026, 11, 1))
        self.assertEqual(plan.issue_mode, RecurringInvoice.MODE_ISSUE)
        invoice = plan.generate_due(today=date(2026, 11, 1))[0]
        self.assertEqual(invoice.status, Invoice.STATUS_OPEN)
        self.assertEqual(invoice.total, Decimal("350000"))

    def test_dashboard_and_screens_point_to_waiting_drafts(self):
        self._make_plan()
        RecurringInvoice.objects.get().generate_due(today=date(2026, 11, 1))
        self.assertContains(self.client.get(reverse("books:dashboard")), "prepared and waiting")
        detail = self.client.get(reverse("books:invoice_detail", args=[self.october.pk]))
        self.assertContains(detail, "Monthly plan made from this invoice")
        self.assertContains(self.client.get(reverse("books:recurring_list")), f"From")
        response = self.client.post(reverse("books:recurring_generate"), follow=True)
        self.assertEqual(response.status_code, 200)

    def test_hospital_staff_cannot_use_any_of_it(self):
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        self.client.force_login(User.objects.create_user(username="c", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True))
        self.assertEqual(self.client.get(reverse("books:invoice_make_plan", args=[self.october.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("books:invoice_repeat", args=[self.october.pk])).status_code, 403)
