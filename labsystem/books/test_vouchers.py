from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from accounts.models import User
from books import delivery, documents
from books.assistant_topics import assistant as books_assistant
from books.models import Expense, Payee, PaymentVoucher
from books.reports import trial_balance
from books.test_collections import BooksCase


class VoucherCase(BooksCase):
    def setUp(self):
        super().setUp()
        self.joel = Payee.objects.create(name="Joel Mukasa", phone="0772555111", email="joel@example.com", default_account=self.rent)

    def _post_payment(self, **overrides):
        data = {
            "payee": self.joel.pk, "date": "2026-11-03", "description": "Office cleaning, October",
            "account": self.rent.pk, "amount": "450000", "method": Expense.METHOD_CASH, "transaction_charge": "0",
        }
        data.update(overrides)
        return self.client.post(reverse("books:voucher_create"), data)

    def _voucher(self, amount="450000", on=date(2026, 11, 3), payee=None):
        return PaymentVoucher.pay(
            payee or self.joel, user=self.user, date=on, description="Office cleaning",
            account=self.rent, amount=Decimal(amount), method=Expense.METHOD_CASH,
        )


class PayingSomeoneTests(VoucherCase):
    def test_one_form_records_the_expense_and_issues_the_voucher(self):
        response = self._post_payment()
        voucher = PaymentVoucher.objects.get()
        self.assertRedirects(response, reverse("books:voucher_detail", args=[voucher.pk]))
        self.assertEqual(voucher.number, "PV-2026-0001")
        self.assertEqual(voucher.issued_by, self.user)
        expense = voucher.expense
        self.assertEqual((expense.amount_ugx, expense.supplier, expense.account), (Decimal("450000"), "Joel Mukasa", self.rent))
        self.assertIsNotNone(expense.journal_entry_id)
        self.assertEqual(self.rent.balance(), Decimal("450000"))
        self.assertEqual(self.cash.balance(), Decimal("-450000"))
        report = trial_balance()
        self.assertEqual(report["total_debit"], report["total_credit"])

    def test_numbers_run_on_and_shows_on_spending(self):
        self._voucher()
        second = self._voucher("100000")
        self.assertEqual(second.number, "PV-2026-0002")
        self.assertContains(self.client.get(reverse("books:expense_list")), "PV-2026-0002")
        self.assertEqual(self.joel.total_paid(), Decimal("550000"))

    def test_their_own_invoice_can_be_attached(self):
        upload = SimpleUploadedFile("joel.pdf", b"%PDF-1.4 joel", content_type="application/pdf")
        self._post_payment(attachment=upload)
        expense = PaymentVoucher.objects.get().expense
        self.assertTrue(expense.receipt.name)
        self.assertEqual(expense.no_receipt_reason, "")

    def test_bad_input_is_refused_and_nothing_is_saved(self):
        response = self._post_payment(amount="0")
        self.assertEqual(response.status_code, 200)
        self.joel.active = False
        self.joel.save()
        self._post_payment()
        self.assertFalse(PaymentVoucher.objects.exists())
        self.assertFalse(Expense.objects.exists())

    def test_the_form_starts_with_the_payees_usual_category(self):
        response = self.client.get(reverse("books:voucher_create") + f"?payee={self.joel.pk}")
        self.assertEqual(response.context["form"].initial["account"], self.rent)


class VoucherDocumentTests(VoucherCase):
    def test_print_page_and_pdf(self):
        voucher = self._voucher("3400000")
        page = self.client.get(reverse("books:voucher_print", args=[voucher.pk]))
        self.assertContains(page, "PAYMENT VOUCHER")
        self.assertContains(page, "Three Million, Four Hundred Thousand Uganda Shillings only.")
        self.assertContains(page, "Received by")
        self.assertContains(page, 'class="stamp stamp-paid"')
        self.assertContains(page, "<svg")
        response = self.client.get(reverse("books:voucher_download", args=[voucher.pk]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_payments_statement_pdf(self):
        self._voucher()
        response = self.client.get(reverse("books:payee_statement_download", args=[self.joel.pk]) + "?start=2026-11-01&end=2026-11-30")
        self.assertTrue(response.content.startswith(b"%PDF"))
        context = documents.payee_statement_context(self.joel, date(2026, 12, 1), None)
        self.assertEqual(context["rows"], [])

    def test_qr_check_page(self):
        voucher = self._voucher()
        self.client.logout()
        url = delivery.voucher_verify_url(self.client.get("/").wsgi_request, voucher)
        page = self.client.get(url[url.index("/books/"):])
        self.assertContains(page, "Genuine payment voucher")
        self.assertContains(page, "Joel Mukasa")
        self.assertEqual(page["Cache-Control"], "private, no-store")
        forged = reverse("books:voucher_verify", args=[f"{voucher.pk}-forged"])
        self.assertEqual(self.client.get(forged).status_code, 404)
        voucher.void("Paid twice", user=self.user)
        self.assertContains(self.client.get(url[url.index("/books/"):]), "was cancelled")

    @override_settings(EMAIL_HOST_CONFIGURED=True)
    def test_email_and_whatsapp(self):
        voucher = self._voucher()
        detail = self.client.get(reverse("books:voucher_detail", args=[voucher.pk]))
        self.assertContains(detail, "Email to joel@example.com")
        self.assertIn("wa.me/256772555111", detail.context["whatsapp_url"])
        self.client.post(reverse("books:voucher_email", args=[voucher.pk]))
        message = mail.outbox[0]
        self.assertEqual(message.to, ["joel@example.com"])
        self.assertIn("PV-2026-0001", message.subject)
        self.assertTrue(message.attachments[0][1].startswith(b"%PDF"))


class VoidAndEditTests(VoucherCase):
    def test_voiding_the_voucher_reverses_the_payment(self):
        voucher = self._voucher()
        self.client.post(reverse("books:voucher_void", args=[voucher.pk]), {"reason": "Wrong person"})
        voucher.refresh_from_db()
        self.assertTrue(voucher.is_void)
        self.assertEqual(self.rent.balance(), Decimal("0"))
        self.assertEqual(documents.voucher_context(voucher)["stamp"], "VOID")
        self.assertEqual(self.joel.total_paid(), Decimal("0"))
        with self.assertRaises(ValidationError):
            delivery.send_voucher(voucher)

    def test_an_expense_with_a_voucher_cannot_be_edited(self):
        voucher = self._voucher()
        response = self.client.post(reverse("books:expense_edit", args=[voucher.expense_id]), {"amount": "1"})
        self.assertRedirects(response, reverse("books:voucher_detail", args=[voucher.pk]))
        voucher.expense.refresh_from_db()
        self.assertEqual(voucher.expense.amount, Decimal("450000"))


class OlderExpenseTests(VoucherCase):
    def _old_expense(self, supplier="joel mukasa"):
        expense = Expense(date=date(2026, 6, 2), supplier=supplier, description="Painting", account=self.rent,
                          amount=Decimal("200000"), method=Expense.METHOD_CASH, no_receipt_reason="n/a")
        expense.save()
        expense.record(user=self.user)
        return expense

    def test_issue_a_voucher_for_an_earlier_payment(self):
        expense = self._old_expense()
        page = self.client.get(reverse("books:voucher_for_expense", args=[expense.pk]))
        self.assertEqual(page.context["form"].initial["payee"], self.joel)
        self.client.post(reverse("books:voucher_for_expense", args=[expense.pk]), {"payee": self.joel.pk})
        voucher = expense.voucher
        self.assertEqual((voucher.number, voucher.date), ("PV-2026-0001", date(2026, 6, 2)))
        self.assertEqual(self.rent.balance(), Decimal("200000"))
        again = self.client.get(reverse("books:voucher_for_expense", args=[expense.pk]))
        self.assertRedirects(again, reverse("books:expense_list"))

    def test_unknown_supplier_can_be_added_and_comes_back(self):
        expense = self._old_expense("Kato Builders")
        page = self.client.get(reverse("books:voucher_for_expense", args=[expense.pk]))
        self.assertContains(page, "Add them as a payee")
        response = self.client.post(
            reverse("books:payee_create") + f"?expense={expense.pk}", {"name": "Kato Builders", "kind": Payee.KIND_COMPANY, "active": "on"},
        )
        kato = Payee.objects.get(name="Kato Builders")
        self.assertRedirects(response, reverse("books:voucher_for_expense", args=[expense.pk]) + f"?payee={kato.pk}")


class PayeeTests(VoucherCase):
    def test_a_client_can_also_be_a_payee(self):
        self.joel.client = self.acme
        self.joel.save()
        self._voucher()
        self.assertContains(self.client.get(reverse("books:client_detail", args=[self.acme.pk])), "We also pay them")
        self.assertContains(self.client.get(reverse("books:payee_detail", args=[self.joel.pk])), "They owe us")
        listing = self.client.get(reverse("books:payee_list"))
        self.assertContains(listing, "450,000")

    def test_pages_need_books_staff(self):
        self.client.logout()
        hospital_user = User.objects.create_user(username="nurse", password="x", role=User.ROLE_HOSPITAL_ADMIN, is_active=True)
        self.client.force_login(hospital_user)
        for name, args in (("payee_list", []), ("voucher_list", []), ("voucher_create", []), ("payee_detail", [self.joel.pk])):
            self.assertIn(self.client.get(reverse(f"books:{name}", args=args)).status_code, (302, 403), name)


class VoucherAssistantTests(VoucherCase):
    def test_how_much_did_we_pay_someone(self):
        self._voucher(on=date(2026, 11, 3))
        result, _, topic = books_assistant.ask("how much did we pay Joel this month", {}, date(2026, 11, 20))
        self.assertEqual(topic, "paid_out")
        self.assertIn("UGX 450,000", result["text"])
        result, _, topic = books_assistant.ask("status of PV-2026-0001", {}, date(2026, 11, 20))
        self.assertEqual(topic, "document")
        self.assertIn("valid", result["text"])

    def test_money_coming_in_is_not_mistaken_for_a_voucher(self):
        self.joel.client = self.acme
        self.joel.save()
        _, _, topic = books_assistant.ask("what did Acme pay us this month", {}, date(2026, 11, 20))
        self.assertEqual(topic, "collected")
