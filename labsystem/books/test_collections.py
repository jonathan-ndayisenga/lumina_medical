import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Hospital, User
from books import delivery, documents, pdf
from books.models import Account, Client, CompanySettings, CreditNote, Expense, Invoice, Payment, ReminderLog, StatementLine
from books.reconciliation_models import import_statement, parse_statement
from books.reports import client_statement, trial_balance


class BooksCase(TestCase):
    def setUp(self):
        self.tenant, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.user = User.objects.create_user(username="books-admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=self.tenant, is_active=True)
        company = CompanySettings.load()
        company.legal_name, company.trading_name, company.tin = "Ternah Software Company Ltd", "Ternah", "1058472195"
        company.address, company.city, company.phone, company.email = "Plot 1", "Kampala", "0787770007", "ternah22@gmail.com"
        company.save()
        self.ar = Account.objects.create(code="1200", name="Receivables", type=Account.TYPE_ASSET, role=Account.ROLE_AR)
        self.cash = Account.objects.create(code="1000", name="Cash", type=Account.TYPE_ASSET, role=Account.ROLE_CASH, is_payment_account=True)
        self.bank = Account.objects.create(code="1100", name="Stanbic", type=Account.TYPE_ASSET, role=Account.ROLE_BANK, is_payment_account=True)
        self.vat = Account.objects.create(code="2100", name="VAT", type=Account.TYPE_LIABILITY, role=Account.ROLE_VAT)
        self.income = Account.objects.create(code="4000", name="Onboarding income", type=Account.TYPE_INCOME)
        self.other_income = Account.objects.create(code="4100", name="Hosting income", type=Account.TYPE_INCOME)
        self.rent = Account.objects.create(code="6100", name="Rent", type=Account.TYPE_EXPENSE)
        Account.objects.create(code="2200", name="Customer credits", type=Account.TYPE_LIABILITY, role=Account.ROLE_CREDITS)
        Account.objects.create(code="6900", name="Charges", type=Account.TYPE_EXPENSE, role=Account.ROLE_CHARGES)
        self.acme = Client.objects.create(name="Acme Ltd", email="acme@example.com", phone="0772123456", contact_person="Grace")
        self.other = Client.objects.create(name="Other Ltd", email="other@example.com")
        self.client.force_login(self.user)

    def _invoice(self, client=None, amount="1000000", due=None, vat=False, extra_line=None):
        invoice = Invoice.objects.create(client=client or self.acme, due_date=due or date(2026, 12, 1), kind=Invoice.KIND_ONBOARDING, apply_vat=vat)
        invoice.lines.create(description="Onboarding", quantity=Decimal("1"), unit_price=Decimal(amount), revenue_account=self.income)
        if extra_line:
            invoice.lines.create(description="Hosting", quantity=Decimal("1"), unit_price=Decimal(extra_line), revenue_account=self.other_income)
        invoice.issue(user=self.user)
        return invoice

    def _pay(self, amount, client=None, on=date(2026, 11, 1), method=Payment.METHOD_CASH, reference=""):
        payment = Payment(client=client or self.acme, amount=Decimal(amount), date=on, method=method, reference=reference)
        payment.save()
        payment.record(user=self.user)
        return payment


class CreditNoteTests(BooksCase):
    def _note(self, invoice, amount, reason="Overcharged"):
        return CreditNote(invoice=invoice, client=invoice.client, amount=Decimal(amount), reason=reason).record(user=self.user)

    def test_reduces_the_balance_and_the_ledger_agrees(self):
        invoice = self._invoice()
        note = self._note(invoice, "400000")
        self.assertTrue(note.number)
        self.assertEqual(invoice.balance, Decimal("600000"))
        self.assertEqual(self.acme.balance, Decimal("600000"))
        self.assertEqual(self.ar.balance(), Decimal("600000"))
        self.assertEqual(self.income.balance(), Decimal("600000"))
        self.assertTrue(trial_balance()["balanced"])

    def test_vat_and_income_go_back_in_proportion_and_to_the_right_accounts(self):
        company = CompanySettings.load()
        company.vrn = "VAT123"
        company.save()
        invoice = self._invoice(amount="750000", extra_line="250000", vat=True)
        self.assertEqual(invoice.total, Decimal("1180000"))
        note = self._note(invoice, "590000")
        self.assertEqual(note.tax_amount, Decimal("90000.00"))
        self.assertEqual(self.vat.balance(), Decimal("90000.00"))
        self.assertEqual(self.income.balance(), Decimal("375000.00"))
        self.assertEqual(self.other_income.balance(), Decimal("125000.00"))
        self.assertTrue(trial_balance()["balanced"])

    def test_full_credit_settles_the_invoice_and_voiding_brings_it_back(self):
        invoice = self._invoice()
        note = self._note(invoice, "1000000")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_PAID)
        note.void("Mistake", user=self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_OPEN)
        self.assertEqual(invoice.balance, Decimal("1000000"))
        self.assertEqual(self.income.balance(), Decimal("1000000"))
        self.assertTrue(trial_balance()["balanced"])

    def test_cannot_credit_more_than_is_owed_or_an_unissued_invoice(self):
        invoice = self._invoice()
        self._pay("800000")
        with self.assertRaises(ValidationError):
            self._note(invoice, "200001")
        draft = Invoice.objects.create(client=self.acme, due_date=date(2026, 12, 1))
        with self.assertRaises(ValidationError):
            CreditNote(invoice=draft, client=self.acme, amount=Decimal("1"), reason="x").record()
        self.assertFalse(CreditNote.objects.exists())

    def test_invoice_with_a_credit_note_cannot_be_voided_underneath_it(self):
        invoice = self._invoice()
        self._note(invoice, "100000")
        with self.assertRaises(ValidationError):
            invoice.void("x", user=self.user)

    def test_appears_on_the_statement_and_the_invoice(self):
        invoice = self._invoice()
        self._note(invoice, "100000")
        statement = client_statement(self.acme)
        self.assertEqual(statement["closing_balance"], Decimal("900000"))
        self.assertIn("credit_note", [e["type"] for e in statement["events"]])
        self.assertContains(self.client.get(reverse("books:invoice_pdf", args=[invoice.pk])), "Credit notes")

    def test_screen_records_one_and_refuses_too_much(self):
        invoice = self._invoice()
        url = reverse("books:credit_note_create", args=[invoice.pk])
        self.assertContains(self.client.post(url, {"date": "2026-11-05", "amount": "9999999", "reason": "x"}), "more than the invoice")
        response = self.client.post(url, {"date": "2026-11-05", "amount": "1000", "reason": "Rounding"})
        self.assertRedirects(response, reverse("books:invoice_detail", args=[invoice.pk]))
        note = CreditNote.objects.get()
        self.assertEqual(self.client.get(reverse("books:credit_note_print", args=[note.pk])).status_code, 200)


class PdfTests(BooksCase):
    def test_every_document_downloads_as_a_real_pdf(self):
        invoice = self._invoice()
        payment = self._pay("300000")
        note = CreditNote(invoice=invoice, client=self.acme, amount=Decimal("1000"), reason="<b>Odd</b> & text").record(user=self.user)
        for name, args in (("invoice_download", [invoice.pk]), ("receipt_download", [payment.pk]), ("statement_download", [self.acme.pk]), ("credit_note_download", [note.pk])):
            response = self.client.get(reverse(f"books:{name}", args=args))
            self.assertEqual(response.status_code, 200, name)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertTrue(response.content.startswith(b"%PDF"), name)

    def test_long_invoices_flow_onto_more_pages(self):
        invoice = Invoice.objects.create(client=self.acme, due_date=date(2026, 12, 1))
        for i in range(70):
            invoice.lines.create(description=f"Line {i}", detail="Some detail\nacross lines", quantity=Decimal("1"), unit_price=Decimal("1000"), revenue_account=self.income)
        invoice.issue(user=self.user)
        self.assertGreater(pdf.invoice_pdf(documents.invoice_context(invoice)).count(b"/Type /Page\n") or 0, 1)

    def test_stamps_follow_the_invoice_state(self):
        invoice = self._invoice(due=timezone.localdate() + timedelta(days=5))
        self.assertEqual(documents.invoice_context(invoice)["stamp"], "")
        overdue = self._invoice(client=self.other, due=timezone.localdate() - timedelta(days=5))
        self.assertEqual(documents.invoice_context(overdue)["stamp"], "OVERDUE")
        self._pay("1000000")
        invoice.refresh_from_db()
        self.assertEqual(documents.invoice_context(invoice)["stamp"], "PAID")
        self.assertContains(self.client.get(reverse("books:invoice_pdf", args=[invoice.pk])), 'class="stamp stamp-paid"')


class ReceiptVerifyTests(BooksCase):
    def test_qr_on_the_receipt_leads_to_a_page_that_confirms_it(self):
        payment = self._pay("300000")
        self.assertContains(self.client.get(reverse("books:receipt_pdf", args=[payment.pk])), "<svg")
        self.client.logout()
        response = self.client.get(reverse("books:receipt_verify", args=[delivery.receipt_verify_token(payment)]))
        self.assertContains(response, "Genuine receipt")
        self.assertContains(response, "300,000")
        self.assertNotContains(response, "Total still outstanding")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_changed_or_guessed_codes_are_refused(self):
        first, second = self._pay("1000"), self._pay("2000")
        token = delivery.receipt_verify_token(first)
        forged = f"{second.pk}-{token.split('-', 1)[1]}"
        self.client.logout()
        for bad in (forged, f"{first.pk}-abc", "1", "x-y"):
            response = self.client.get(reverse("books:receipt_verify", args=[bad]))
            self.assertEqual(response.status_code, 404, bad)
            self.assertContains(response, "could not be confirmed", status_code=404)

    def test_voided_receipt_says_so(self):
        payment = self._pay("1000")
        payment.void("Bounced", user=self.user)
        self.client.logout()
        response = self.client.get(reverse("books:receipt_verify", args=[delivery.receipt_verify_token(payment)]))
        self.assertContains(response, "was cancelled")
        self.assertNotContains(response, "Genuine")


class EmailTests(BooksCase):
    def test_nothing_sends_until_email_is_set_up(self):
        invoice = self._invoice()
        response = self.client.post(reverse("books:invoice_email", args=[invoice.pk]), follow=True)
        self.assertContains(response, "set up on this server")
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_HOST_CONFIGURED=True)
    def test_invoice_and_receipt_go_out_with_the_pdf(self):
        invoice = self._invoice()
        self.assertContains(self.client.get(reverse("books:invoice_detail", args=[invoice.pk])), "Email to client")
        self.client.post(reverse("books:invoice_email", args=[invoice.pk]))
        message = mail.outbox[0]
        self.assertEqual(message.to, ["acme@example.com"])
        self.assertEqual(message.reply_to, ["ternah22@gmail.com"])
        self.assertIn("Grace", message.body)
        name, content, kind = message.attachments[0]
        self.assertEqual((name, kind), (f"{invoice.number}.pdf", "application/pdf"))
        self.assertTrue(content.startswith(b"%PDF"))

        payment = self._pay("1000")
        self.client.post(reverse("books:receipt_email", args=[payment.pk]))
        self.assertIn(payment.receipt_number, mail.outbox[1].subject)

    @override_settings(EMAIL_HOST_CONFIGURED=True)
    def test_draft_and_addressless_invoices_do_not_send(self):
        draft = Invoice.objects.create(client=self.acme, due_date=date(2026, 12, 1))
        self.client.post(reverse("books:invoice_email", args=[draft.pk]))
        no_email = Client.objects.create(name="No Email Ltd")
        invoice = self._invoice(client=no_email)
        response = self.client.post(reverse("books:invoice_email", args=[invoice.pk]), follow=True)
        self.assertContains(response, "no email address")
        self.assertEqual(mail.outbox, [])

    def test_send_is_post_only(self):
        invoice = self._invoice()
        self.assertEqual(self.client.get(reverse("books:invoice_email", args=[invoice.pk])).status_code, 405)

    def test_whatsapp_button_has_the_number_and_the_invoice(self):
        invoice = self._invoice()
        self.assertTrue(delivery.invoice_whatsapp_url(invoice).startswith("https://wa.me/256772123456?text="))
        self.assertContains(self.client.get(reverse("books:invoice_detail", args=[invoice.pk])), "Send on WhatsApp")


@override_settings(EMAIL_HOST_CONFIGURED=True)
class ReminderTests(BooksCase):
    TODAY = date(2026, 11, 20)

    def _enable(self):
        company = CompanySettings.load()
        company.reminders_enabled = True
        company.save()

    def _days(self, offset):
        return self.TODAY + timedelta(days=offset)

    def test_off_by_default(self):
        self._invoice(due=self._days(1))
        result = delivery.send_due_reminders(today=self.TODAY)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(result["sent"], [])

    def test_each_stage_is_sent_once_and_only_when_due(self):
        self._enable()
        invoice = self._invoice(due=self._days(3))
        self.assertEqual(delivery.send_due_reminders(today=self.TODAY - timedelta(days=1))["sent"], [])
        for day, stage in ((self.TODAY, "before_due"), (self._days(3), "due_today"), (self._days(6), "overdue_3"), (self._days(17), "overdue_14"), (self._days(33), "overdue_30")):
            sent = delivery.send_due_reminders(today=day)["sent"]
            self.assertEqual(len(sent), 1, stage)
            self.assertIn(stage, sent[0])
            self.assertEqual(delivery.send_due_reminders(today=day)["sent"], [], f"{stage} sent twice")
        self.assertEqual(ReminderLog.objects.filter(invoice=invoice).count(), 5)
        self.assertEqual(len(mail.outbox), 5)
        self.assertTrue(mail.outbox[0].attachments)

    def test_a_late_run_sends_one_email_not_a_burst(self):
        self._enable()
        self._invoice(due=self.TODAY - timedelta(days=40))
        delivery.send_due_reminders(today=self.TODAY)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ReminderLog.objects.get().stage, "overdue_30")

    def test_paid_credited_and_void_invoices_are_left_alone(self):
        self._enable()
        self._invoice(due=self.TODAY)
        self._pay("1000000")
        credited = self._invoice(client=self.other, due=self.TODAY)
        CreditNote(invoice=credited, client=self.other, amount=Decimal("1000000"), reason="x").record(user=self.user)
        third = Client.objects.create(name="Third", email="t@example.com")
        self._invoice(client=third, due=self.TODAY).void("x", user=self.user)
        delivery.send_due_reminders(today=self.TODAY)
        self.assertEqual(mail.outbox, [])

    def test_dry_run_sends_nothing_and_no_email_address_is_reported(self):
        self._enable()
        self._invoice(due=self.TODAY)
        self._invoice(client=Client.objects.create(name="No Email"), due=self.TODAY)
        result = delivery.send_due_reminders(today=self.TODAY, dry_run=True)
        self.assertEqual(len(result["sent"]), 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(ReminderLog.objects.exists())

    def test_failed_send_is_retried_tomorrow(self):
        self._enable()
        self._invoice(due=self.TODAY)
        with override_settings(EMAIL_BACKEND="books.test_collections.BrokenEmailBackend"):
            result = delivery.send_due_reminders(today=self.TODAY)
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse(ReminderLog.objects.exists())
        self.assertEqual(len(delivery.send_due_reminders(today=self.TODAY)["sent"]), 1)

    def test_command_runs(self):
        out = StringIO()
        call_command("send_invoice_reminders", "--dry-run", stdout=out)
        self.assertIn("turned off", out.getvalue())


class BrokenEmailBackend:
    def __init__(self, *args, **kwargs):
        pass

    def send_messages(self, messages):
        raise OSError("connection refused")


class ClientPortalTests(BooksCase):
    def setUp(self):
        super().setUp()
        self.mine = self._invoice()
        self.payment = self._pay("100000")
        self.theirs = self._invoice(client=self.other, amount="777000")
        self.their_payment = self._pay("5000", client=self.other)
        self.draft = Invoice.objects.create(client=self.acme, due_date=date(2026, 12, 1))
        self.acme.enable_portal()
        self.token = self.acme.portal_token
        self.client.logout()

    def test_off_until_turned_on_and_a_wrong_link_finds_nothing(self):
        self.assertIsNone(self.other.portal_token)
        self.assertEqual(self.client.get(reverse("books:portal_home", args=["wrong-token"])).status_code, 404)

    def test_shows_their_account_privately(self):
        response = self.client.get(reverse("books:portal_home", args=[self.token]))
        self.assertContains(response, "Acme Ltd")
        self.assertContains(response, self.mine.number)
        self.assertContains(response, self.payment.receipt_number)
        self.assertNotContains(response, self.theirs.number)
        self.assertNotContains(response, "777,000")
        self.assertNotContains(response, "Other Ltd")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_one_clients_link_cannot_open_another_clients_documents(self):
        self.assertEqual(self.client.get(reverse("books:portal_invoice", args=[self.token, self.theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("books:portal_receipt", args=[self.token, self.their_payment.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("books:portal_invoice", args=[self.token, self.draft.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("books:portal_invoice", args=[self.token, self.mine.pk])).status_code, 200)

    def test_a_new_link_kills_the_old_one_and_off_means_off(self):
        self.acme.enable_portal()
        self.assertEqual(self.client.get(reverse("books:portal_home", args=[self.token])).status_code, 404)
        self.acme.disable_portal()
        self.assertEqual(self.client.get(reverse("books:portal_statement", args=[self.acme.portal_token or "none"])).status_code, 404)

    def test_inactive_client_link_stops_working(self):
        self.acme.active = False
        self.acme.save()
        self.assertEqual(self.client.get(reverse("books:portal_home", args=[self.token])).status_code, 404)

    def test_only_books_admins_can_flip_the_switch(self):
        url = reverse("books:client_portal_toggle", args=[self.other.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.other.refresh_from_db()
        self.assertIsNone(self.other.portal_token)
        staff = User.objects.create_user(username="clerk", password="x", role=User.ROLE_RECEPTIONIST, hospital=self.tenant, is_active=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.user)
        self.client.post(url, {"action": "enable"})
        self.other.refresh_from_db()
        self.assertTrue(self.other.portal_token)
        self.assertContains(self.client.get(reverse("books:client_detail", args=[self.other.pk])), self.other.portal_token)


BANK_CSV = b"""Date,Description,Reference,Amount
05/11/2026,Deposit ACME,TX1,300000
06/11/2026,Rent payment,TX2,-700000
07/11/2026,Bank charge,,"-1,500"
not-a-date,Bad row,TX9,100
"""
MOMO_CSV = "Transaction ID,Completed On,Details,Money In,Money Out\nMP1,2026-11-05 10:12:00,From Acme,\"300,000\",\nMP2,2026-11-06 09:00:00,To Rent,,700000\n".encode()


class StatementMatchingTests(BooksCase):
    def test_reads_bank_and_momo_layouts(self):
        rows, problems = parse_statement(BANK_CSV)
        self.assertEqual([r["amount"] for r in rows], [Decimal("300000"), Decimal("-700000"), Decimal("-1500")])
        self.assertEqual(rows[0]["date"], date(2026, 11, 5))
        self.assertEqual(rows[0]["reference"], "TX1")
        self.assertEqual(len(problems), 1)
        self.assertIn("Row 5", problems[0])
        rows, problems = parse_statement(MOMO_CSV)
        self.assertEqual(problems, [])
        self.assertEqual([r["amount"] for r in rows], [Decimal("300000"), Decimal("-700000")])
        self.assertEqual(rows[1]["date"], date(2026, 11, 6))

    def test_file_without_the_columns_is_refused(self):
        with self.assertRaises(ValidationError):
            parse_statement(b"Name,Age\nA,1\n")

    def test_uploading_the_same_file_twice_adds_nothing_but_real_twins_stay(self):
        rows, _ = parse_statement(BANK_CSV)
        self.assertEqual(import_statement(self.bank, rows), (3, 0))
        self.assertEqual(import_statement(self.bank, rows), (0, 3))
        twin = [{"date": date(2026, 11, 8), "amount": Decimal("5000"), "description": "Fee", "reference": ""}] * 2
        self.assertEqual(import_statement(self.bank, twin), (2, 0))

    def _payment_into_bank(self, amount="300000", on=date(2026, 11, 4), reference=""):
        return self._pay(amount, on=on, method=Payment.METHOD_BANK, reference=reference)

    def test_suggests_only_same_amount_account_and_week(self):
        good = self._payment_into_bank()
        self._payment_into_bank(amount="299999")
        self._payment_into_bank(on=date(2026, 10, 1))
        self._pay("300000", on=date(2026, 11, 4))  # Same amount, but into the cash account.
        line = StatementLine.objects.create(account=self.bank, date=date(2026, 11, 5), amount=Decimal("300000"), import_key="a")
        self.assertEqual(line.suggestions(), [good])

    def test_reference_match_wins_over_the_closer_date(self):
        self._payment_into_bank(on=date(2026, 11, 5))
        by_reference = self._payment_into_bank(on=date(2026, 11, 1), reference="TX1")
        line = StatementLine.objects.create(account=self.bank, date=date(2026, 11, 5), amount=Decimal("300000"), reference="tx1", import_key="a")
        self.assertEqual(line.suggestions()[0], by_reference)

    def test_money_out_suggests_expenses_including_the_charge(self):
        expense = Expense(date=date(2026, 11, 5), description="Rent", account=self.rent, amount=Decimal("700000"), method=Expense.METHOD_BANK, transaction_charge=Decimal("1000"), no_receipt_reason="n/a")
        expense.save()
        expense.record(user=self.user)
        line = StatementLine.objects.create(account=self.bank, date=date(2026, 11, 5), amount=Decimal("-701000"), import_key="a")
        self.assertEqual(line.suggestions(), [expense])

    def test_full_flow_upload_match_undo_and_ledger_untouched(self):
        payment = self._payment_into_bank()
        before = self.bank.balance()
        self.client.post(reverse("books:reconcile"), {"account": self.bank.pk, "file": SimpleUploadedFile("s.csv", BANK_CSV, content_type="text/csv")})
        self.assertEqual(StatementLine.objects.count(), 3)
        self.assertEqual(self.bank.balance(), before)

        line = StatementLine.objects.get(reference="TX1")
        response = self.client.get(reverse("books:reconcile"))
        self.assertContains(response, payment.receipt_number)
        self.assertEqual(response.context["open_count"], 3)

        self.client.post(reverse("books:reconcile_action", args=[line.pk]), {"action": "match", "target": payment.pk})
        line.refresh_from_db()
        self.assertEqual(line.matched_payment, payment)
        self.assertEqual(self.client.get(reverse("books:reconcile")).context["open_count"], 2)

        second = StatementLine.objects.create(account=self.bank, date=date(2026, 11, 5), amount=Decimal("300000"), import_key="b")
        self.assertEqual(second.suggestions(), [], "a payment already matched is not offered again")

        self.client.post(reverse("books:reconcile_action", args=[line.pk]), {"action": "undo"})
        line.refresh_from_db()
        self.assertIsNone(line.matched_payment)

    def test_cannot_match_a_record_the_line_did_not_suggest(self):
        unrelated = self._pay("999", on=date(2026, 1, 1))
        line = StatementLine.objects.create(account=self.bank, date=date(2026, 11, 5), amount=Decimal("300000"), import_key="a")
        self.client.post(reverse("books:reconcile_action", args=[line.pk]), {"action": "match", "target": unrelated.pk})
        line.refresh_from_db()
        self.assertIsNone(line.matched_payment)

    def test_ignore_button_wins_over_the_hidden_match_field(self):
        line = StatementLine.objects.create(account=self.bank, date=date(2026, 11, 5), amount=Decimal("300000"), import_key="a")
        self.client.post(reverse("books:reconcile_action", args=[line.pk]), {"action": ["match", "ignore"]})
        line.refresh_from_db()
        self.assertTrue(line.ignored)


class BooksAccessTests(BooksCase):
    def test_hospital_staff_are_refused_every_new_books_page(self):
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        self.client.force_login(User.objects.create_user(username="clinic", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True))
        invoice = self._invoice()
        payment = self._pay("1000")
        for name, args in (("invoice_download", [invoice.pk]), ("receipt_download", [payment.pk]), ("statement_download", [self.acme.pk]), ("reconcile", []), ("credit_note_create", [invoice.pk])):
            self.assertEqual(self.client.get(reverse(f"books:{name}", args=args)).status_code, 403, name)


class MoveToNewServerTests(BooksCase):
    def test_export_holds_books_and_its_staff_but_nothing_of_the_hospitals(self):
        clinic = Hospital.objects.create(name="Secret Clinic", subdomain="secret-clinic")
        doctor = User.objects.create_user(username="clinic-doctor", password="x", role=User.ROLE_DOCTOR, hospital=clinic, is_active=True)
        linked = Client.objects.create(name="Linked", hospital=clinic)
        invoice = self._invoice(client=linked)
        self._pay("1000", client=linked)
        invoice.journal_entry.created_by = doctor
        invoice.journal_entry.save()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "books.json")
            call_command("export_books", str(path), stdout=StringIO())
            text = path.read_text(encoding="utf-8")
        rows = json.loads(text)
        self.assertNotIn("Secret Clinic", text)
        self.assertNotIn("clinic-doctor", text)
        self.assertEqual({r["fields"]["subdomain"] for r in rows if r["model"] == "accounts.hospital"}, {settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN})
        self.assertIn("books-admin", {r["fields"]["username"] for r in rows if r["model"] == "accounts.user"})
        client_row = next(r for r in rows if r["model"] == "books.client" and r["fields"]["name"] == "Linked")
        self.assertIsNone(client_row["fields"]["hospital"])
        self.assertTrue(any(r["model"] == "books.invoice" for r in rows))

    def test_import_refuses_a_server_that_already_has_books_data(self):
        self._invoice()
        with self.assertRaisesMessage(Exception, "already has invoices"):
            call_command("import_books", "nothing.json", stdout=StringIO())
