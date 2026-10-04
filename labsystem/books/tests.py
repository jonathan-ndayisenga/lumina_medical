from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital, User
from books.documents import amount_in_words
from books.models import Account, Client, CompanySettings, Invoice
from books.quotation_models import Quotation, QuotationField, QuotationSettings

# The Kanyigo Bulonde SACCO quotation (TSC-SACCO-2026-001) used as the template.
SACCO_ITEMS = [
    ("System configuration & setup", "Core deployment; chart of accounts, savings & loan products.", "One-time", "1,400,000"),
    ("Customization to your SACCO's needs", "Custom workflows and approval chains.", "One-time", "600,000"),
    ("Data migration & member onboarding", "Import of existing members and balances.", "One-time", "450,000"),
    ("SMS notification setup", "Africa's Talking integration.", "One-time", "250,000"),
    ("Staff training", "Hands-on training for tellers and loan officers.", "3 days x 150,000", "450,000"),
    ("Go-live support", "On-site / remote support during the first live week.", "Min. 5 days", "250,000"),
]


class QuotationTests(TestCase):
    def setUp(self):
        hospital, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.admin = User.objects.create_user(
            username="books_admin", password="StrongPass123!", first_name="Jonathan", last_name="Ndayisenga",
            role=User.ROLE_HOSPITAL_ADMIN, hospital=hospital,
        )
        company = CompanySettings.load()
        company.trading_name, company.legal_name = "Ternah", "Ternah Software Company Ltd"
        company.tin, company.company_reg_no, company.city = "1058472195", "80034439607129", "Kampala"
        company.save()
        self.client_row = Client.objects.create(
            name="Kanyigo Bulonde Community SACCO", contact_person="The Chairperson / Manager", address="Kajjansi, Uganda",
        )
        self.income = Account.objects.create(code="4000", name="Onboarding income", type=Account.TYPE_INCOME)
        self.client.force_login(self.admin)

    def _post_quotation(self, items=SACCO_ITEMS, **extra):
        data = {
            "client": self.client_row.pk, "code": "sacco", "subtitle": "SACCO Management System",
            "issue_date": "2026-10-02", "currency": "UGX", "intro": "Thank you for choosing Ternah.",
            "highlight_title": "This setup comes with 3 months of FREE hosting.",
            "line_title": [i[0] for i in items], "line_description": [i[1] for i in items],
            "line_basis": [i[2] for i in items], "line_amount": [i[3] for i in items],
        }
        data.update(extra)
        return self.client.post(reverse("books:quotation_create"), data)

    def test_quotation_from_the_template_items_totals_and_numbers_like_the_pdf(self):
        response = self._post_quotation()
        quote = Quotation.objects.get()
        self.assertRedirects(response, reverse("books:quotation_detail", args=[quote.pk]))
        self.assertEqual(quote.number, "TSC-SACCO-2026-001")
        self.assertEqual(quote.total, Decimal("3400000"))
        self.assertEqual(quote.lines.count(), 6)
        self.assertEqual(quote.valid_until, date(2026, 11, 1))  # 30 days by default
        self.assertEqual(quote.attention, "The Chairperson / Manager")  # filled from the client

    def test_numbers_run_per_code(self):
        self._post_quotation()
        self._post_quotation()
        self._post_quotation(code="hms")
        self.assertEqual(
            sorted(Quotation.objects.values_list("number", flat=True)),
            ["TSC-HMS-2026-001", "TSC-SACCO-2026-001", "TSC-SACCO-2026-002"],
        )

    def test_printed_quotation_matches_the_template(self):
        field = QuotationField.objects.create(label="Project duration")
        self._post_quotation(**{f"xf_{field.pk}": "6 weeks"})
        quote = Quotation.objects.get()
        response = self.client.get(reverse("books:quotation_pdf", args=[quote.pk]))
        for text in ("QUOTATION", "SACCO Management System", "TERNAH", "Kanyigo Bulonde Community SACCO",
                     "Attn: The Chairperson / Manager", "TSC-SACCO-2026-001", "1,400,000", "3,400,000",
                     "Three Million, Four Hundred Thousand Uganda Shillings only.", "Not applicable",
                     "3 months of FREE hosting", "Project duration", "6 weeks", "Prepared by: Jonathan Ndayisenga"):
            self.assertContains(response, text)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_blank_rows_are_ignored_and_bad_amounts_are_rejected(self):
        self._post_quotation(items=SACCO_ITEMS[:1] + [("", "", "", "")])
        self.assertEqual(Quotation.objects.get().lines.count(), 1)
        response = self._post_quotation(items=[("Training", "", "One-time", "abc")])
        self.assertContains(response, "Item 1: enter an amount greater than zero.")
        self.assertEqual(Quotation.objects.count(), 1)

    def test_accepted_quotation_converts_to_a_draft_invoice_once(self):
        self._post_quotation()
        quote = Quotation.objects.get()
        response = self.client.post(
            reverse("books:quotation_convert", args=[quote.pk]),
            {"revenue_account": self.income.pk, "kind": Invoice.KIND_ONBOARDING},
        )
        quote.refresh_from_db()
        self.assertRedirects(response, reverse("books:invoice_edit", args=[quote.invoice.pk]), fetch_redirect_response=False)
        self.assertEqual(quote.status, Quotation.STATUS_CONVERTED)
        self.assertEqual(quote.invoice.status, Invoice.STATUS_DRAFT)
        self.assertEqual(quote.invoice.lines.count(), 6)
        self.assertEqual(quote.invoice.total, Decimal("3400000"))
        self.assertEqual(quote.invoice.reference, "TSC-SACCO-2026-001")
        self.client.post(reverse("books:quotation_convert", args=[quote.pk]), {"revenue_account": self.income.pk, "kind": "onboarding"})
        self.assertEqual(Invoice.objects.count(), 1)

    def test_settings_extra_field_appears_on_new_quotations(self):
        self.client.post(reverse("books:quotation_settings"), {"action": "add_field", "label": "Support level", "field_type": "text", "sort_order": 1})
        self.assertTrue(QuotationField.objects.filter(label="Support level", active=True).exists())
        self.assertContains(self.client.get(reverse("books:quotation_create")), "Support level")
        self.client.post(reverse("books:quotation_settings"), {
            "action": "settings", "prefix": "TSC", "validity_days": 14, "intro": "Hello", "footer_tagline": "Keep it simple.",
            "note_left_title": "", "note_left_body": "", "note_right_title": "", "note_right_body": "",
        })
        self.assertEqual(QuotationSettings.load().validity_days, 14)

    def test_other_hospital_staff_cannot_open_quotations(self):
        outsider = User.objects.create_user(
            username="outsider", password="x", role=User.ROLE_HOSPITAL_ADMIN,
            hospital=Hospital.objects.create(name="Some Clinic", subdomain="some-clinic"),
        )
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("books:quotation_list")).status_code, 403)

    def test_amount_in_words(self):
        self.assertEqual(amount_in_words(3400000), "Three Million, Four Hundred Thousand Uganda Shillings only.")
        self.assertEqual(amount_in_words(250000), "Two Hundred and Fifty Thousand Uganda Shillings only.")


class ExpenseEditTests(TestCase):
    """A wrong figure can be corrected; the ledger keeps the trail."""

    def setUp(self):
        from books.models import Expense

        hospital, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.user = User.objects.create_user(username="books_staff", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=hospital)
        self.rent = Account.objects.create(code="6100", name="Rent", type=Account.TYPE_EXPENSE)
        self.bank = Account.objects.create(code="1100", name="Bank", type=Account.TYPE_ASSET, role=Account.ROLE_BANK, is_payment_account=True)
        self.expense = Expense(date=date(2026, 10, 1), description="Office rent", account=self.rent, amount=Decimal("100000"),
                               method=Expense.METHOD_BANK, no_receipt_reason="Landlord gave no receipt")
        self.expense.save()
        self.expense.record(user=self.user)
        self.client.force_login(self.user)

    def _post_edit(self, amount):
        return self.client.post(reverse("books:expense_edit", args=[self.expense.pk]), {
            "date": "2026-10-01", "description": "Office rent", "account": self.rent.pk, "currency": "UGX",
            "amount": amount, "fx_rate": "1", "method": "bank", "transaction_charge": "0",
            "no_receipt_reason": "Landlord gave no receipt",
        })

    def test_list_offers_edit_for_live_expenses(self):
        self.assertContains(self.client.get(reverse("books:expense_list")), reverse("books:expense_edit", args=[self.expense.pk]))

    def test_correcting_the_amount_reverses_and_reposts(self):
        original_entry = self.expense.journal_entry
        response = self._post_edit("150000")
        self.assertRedirects(response, reverse("books:expense_list"))
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.amount_ugx, Decimal("150000"))
        self.assertNotEqual(self.expense.journal_entry_id, original_entry.pk)
        self.assertTrue(original_entry.reversed_by.is_posted)
        # Net effect on the books: only the corrected figure.
        from django.db.models import Sum
        net = self.rent.journal_lines.filter(entry__is_posted=True).aggregate(d=Sum("debit"), c=Sum("credit"))
        self.assertEqual(net["d"] - net["c"], Decimal("150000"))

    def test_void_expense_cannot_be_edited(self):
        self.expense.void("Duplicate", user=self.user)
        response = self.client.get(reverse("books:expense_edit", args=[self.expense.pk]))
        self.assertRedirects(response, reverse("books:expense_list"))


class BooksInsightsAndPlanningTests(TestCase):
    """Dashboard trend / cash / pipeline / top clients / budgets, recurring
    monthly invoices, and WhatsApp payment reminders."""

    def setUp(self):
        from books.models import Expense

        hospital, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.user = User.objects.create_user(username="books_owner", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=hospital)
        company = CompanySettings.load()
        company.trading_name, company.legal_name = "Ternah", "Ternah Software Company Ltd"
        company.momo_mtn_number, company.momo_mtn_name = "0787770007", "Ternah"
        company.save()
        self.ar = Account.objects.create(code="1200", name="Receivables", type=Account.TYPE_ASSET, role=Account.ROLE_AR)
        self.bank = Account.objects.create(code="1100", name="Stanbic", type=Account.TYPE_ASSET, role=Account.ROLE_BANK, is_payment_account=True)
        self.hosting = Account.objects.create(code="4100", name="Hosting income", type=Account.TYPE_INCOME)
        self.rent = Account.objects.create(code="6100", name="Rent", type=Account.TYPE_EXPENSE)
        self.sacco = Client.objects.create(name="Kanyigo Bulonde SACCO", contact_person="Chairperson", phone="0772 123456")
        self.today = date(2026, 10, 5)
        Expense.objects.create(date=date(2026, 10, 2), description="Office rent", account=self.rent, amount=Decimal("700000"),
                               method=Expense.METHOD_BANK, no_receipt_reason="n/a").record(user=self.user)
        self.client.force_login(self.user)

    def _plan(self, start):
        from books.planning_models import RecurringInvoice
        return RecurringInvoice.objects.create(client=self.sacco, description="Hosting & maintenance", amount=Decimal("350000"),
                                               revenue_account=self.hosting, next_run_date=start)

    def test_recurring_plan_issues_due_months_and_catches_up(self):
        plan = self._plan(date(2026, 8, 1))
        issued = plan.generate_due(today=self.today, user=self.user)
        self.assertEqual([inv.issue_date for inv in issued], [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)])
        self.assertTrue(all(inv.status == Invoice.STATUS_OPEN and inv.total == Decimal("350000") for inv in issued))
        plan.refresh_from_db()
        self.assertEqual(plan.next_run_date, date(2026, 11, 1))
        self.assertEqual(plan.generate_due(today=self.today, user=self.user), [])

    def test_paused_or_ended_plans_issue_nothing(self):
        paused = self._plan(date(2026, 10, 1))
        paused.active = False
        paused.save()
        ended = self._plan(date(2026, 10, 1))
        ended.end_date = date(2026, 9, 30)
        ended.save()
        self.assertEqual(paused.generate_due(today=self.today), [])
        self.assertEqual(ended.generate_due(today=self.today), [])

    def test_management_command_issues_due_invoices(self):
        from io import StringIO
        from django.core.management import call_command

        self._plan(date(2026, 1, 1)).next_run_date  # due long ago
        out = StringIO()
        call_command("generate_recurring_invoices", stdout=out)
        self.assertIn("invoice(s) issued", out.getvalue())
        self.assertTrue(Invoice.objects.filter(client=self.sacco, status=Invoice.STATUS_OPEN).exists())

    def test_whatsapp_reminder_has_uganda_number_amount_and_payment_details(self):
        from urllib.parse import unquote
        from books.insights import payment_reminder_url, whatsapp_phone

        invoice = self._plan(date(2026, 9, 1)).generate_due(today=date(2026, 9, 1))[0]
        url = payment_reminder_url(invoice)
        self.assertTrue(url.startswith("https://wa.me/256772123456?text="))
        text = unquote(url)
        self.assertIn(invoice.number, text)
        self.assertIn("UGX 350,000", text)
        self.assertIn("MTN MoMo: 0787770007", text)
        self.assertEqual(whatsapp_phone("+256 772 123456"), "256772123456")

    def test_dashboard_shows_trend_cash_pipeline_clients_budgets_and_due_plans(self):
        from books.models import ExpenseBudget

        self._plan(date(2026, 1, 1))
        ExpenseBudget.objects.create(account=self.rent, monthly_amount=Decimal("500000"))
        response = self.client.get(reverse("books:dashboard"))
        self.assertEqual(response.status_code, 200)
        for text in ("Last 12 months", "trendChart", "Cash position", "Quotation pipeline", "Top clients",
                     "Budgets this month", "Over ·", "monthly invoice", "Issue due invoices"):
            self.assertContains(response, text)
        self.assertEqual(len(response.context["trend"]["labels"]), 12)

    def test_cash_runway_and_budget_numbers(self):
        from books.insights import budget_status, cash_position
        from books.models import ExpenseBudget

        ExpenseBudget.objects.create(account=self.rent, monthly_amount=Decimal("500000"))
        cash = cash_position(self.today)
        self.assertEqual(cash["total"], Decimal("-700000"))  # rent paid from an empty bank account
        self.assertIsNone(cash["runway_months"])
        row = budget_status(self.today)[0]
        self.assertEqual((row["spent"], row["percent"], row["over"]), (Decimal("700000"), 140, True))

    def test_budgets_page_saves_monthly_amounts(self):
        from books.models import ExpenseBudget

        response = self.client.post(reverse("books:budgets"), {f"budget_{self.rent.pk}": "600,000"})
        self.assertRedirects(response, reverse("books:budgets"))
        self.assertEqual(ExpenseBudget.objects.get(account=self.rent).monthly_amount, Decimal("600000"))

    def test_generate_button_issues_due_plans(self):
        self._plan(date(2026, 1, 1))
        response = self.client.post(reverse("books:recurring_generate"), {"next": reverse("books:dashboard")})
        self.assertRedirects(response, reverse("books:dashboard"), fetch_redirect_response=False)
        self.assertTrue(Invoice.objects.filter(client=self.sacco).exists())
