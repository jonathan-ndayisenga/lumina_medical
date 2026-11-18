"""Regression tests for the fixes from SYSTEM_AUDIT.md (18 Nov 2026).

Each test is named after the finding it guards, so a failure here points
straight back to the audit entry it reopens."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, Hospital, HospitalSubscriptionPayment, User
from admin_dashboard.models import (
    BankAccount,
    BankTransaction,
    CashDrawer,
    CashTransaction,
    InventoryItem,
    Salary,
)
from labsystem.safe_json import script_json
from labsystem.tenancy import scope_to_hospital
from reception.models import Patient, Payment, QueueEntry, Service, Visit, VisitService


class Fixture(TestCase):
    def setUp(self):
        self.hospital = Hospital.objects.create(name="Audit Hospital", subdomain="audit")
        self.other = Hospital.objects.create(name="Other Hospital", subdomain="other")
        self.receptionist = self.user("reception", User.ROLE_RECEPTIONIST)
        self.accountant = self.user("accounts", User.ROLE_ACCOUNTANT)
        self.doctor = self.user("doctor", User.ROLE_DOCTOR)
        self.lab = self.user("lab", User.ROLE_LAB_ATTENDANT)
        self.admin = self.user("admin", User.ROLE_HOSPITAL_ADMIN)
        self.consult = Service.objects.create(
            hospital=self.hospital, name="Consultation", category=Service.CATEGORY_CONSULTATION,
            price=Decimal("50000"), is_active=True,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital, name="Audit Patient", registration_date=timezone.localdate(), age="30YRS", sex="F",
        )
        self.visit = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, total_amount=Decimal("50000"),
            status=Visit.STATUS_READY_FOR_BILLING, created_by=self.receptionist,
        )
        VisitService.objects.create(visit=self.visit, service=self.consult, price_at_time=self.consult.price)

    def user(self, username, role, hospital="mine"):
        return User.objects.create_user(
            username=username, password="x", role=role, is_active=True,
            hospital=self.hospital if hospital == "mine" else hospital,
        )


class LabAccessTests(Fixture):
    """C-1: only lab staff (and admins) do lab work; doctors may read reports."""

    def test_non_lab_roles_cannot_open_lab_work_pages(self):
        for user in (self.receptionist, self.accountant, self.doctor):
            self.client.force_login(user)
            for url in (reverse("queue"), reverse("lab_settings"), reverse("reviewing_results")):
                response = self.client.get(url)
                self.assertNotEqual(response.status_code, 200, f"{user.username} opened {url}")

    def test_lab_staff_still_can(self):
        self.client.force_login(self.lab)
        self.assertEqual(self.client.get(reverse("queue")).status_code, 200)

    def test_doctor_can_read_a_visit_report_but_not_release_it(self):
        self.client.force_login(self.doctor)
        url = reverse("visit_report", args=[self.visit.pk])
        self.assertNotEqual(self.client.get(url).status_code, 403)
        self.client.raise_request_exception = False
        self.assertEqual(self.client.post(url, {"action": "release"}).status_code, 403)

    def test_is_staff_is_kept_for_superadmins_only(self):
        for user in (self.receptionist, self.doctor, self.admin):
            user.refresh_from_db()
            self.assertFalse(user.is_staff, user.username)
        boss = User.objects.create_user(username="boss", password="x", role=User.ROLE_SUPERADMIN)
        self.assertTrue(boss.is_staff)


class TenantScopingTests(Fixture):
    """C-2: no hospital in effect means no rows, never every hospital's rows."""

    def test_scope_fails_closed_without_a_hospital(self):
        self.assertEqual(scope_to_hospital(Visit.objects.all(), self.admin, None).count(), 0)
        self.assertEqual(scope_to_hospital(Visit.objects.all(), self.admin, self.hospital).count(), 1)
        self.assertEqual(scope_to_hospital(Visit.objects.all(), self.admin, self.other).count(), 0)

    def test_superadmin_still_sees_everything(self):
        boss = User.objects.create_user(username="boss", password="x", role=User.ROLE_SUPERADMIN)
        self.assertEqual(scope_to_hospital(Visit.objects.all(), boss, None).count(), 1)

    def test_lab_user_without_a_hospital_sees_no_other_hospitals_queue(self):
        theirs = Patient.objects.create(hospital=self.other, name="Zz Other Lab Patient", age="20YRS", sex="M")
        visit = Visit.objects.create(patient=theirs, hospital=self.other, total_amount=Decimal("0"))
        QueueEntry.objects.create(hospital=self.other, visit=visit, queue_type=QueueEntry.TYPE_LAB_RECEPTION, reason="x")
        homeless = self.user("homeless-lab", User.ROLE_LAB_ATTENDANT, hospital=None)
        self.client.force_login(homeless)
        self.assertNotContains(self.client.get(reverse("queue")), "Zz Other Lab Patient")


class EscapingTests(Fixture):
    """C-3 and M-13: typed text never runs as script."""

    def test_audit_log_details_are_escaped(self):
        AuditLog.objects.create(
            user=self.admin, hospital=self.hospital, action="delete_lab_report", model_name="LabReport",
            object_id="1", details={"reason": "<script>alert('x')</script>"},
        )
        boss = User.objects.create_user(username="boss", password="x", role=User.ROLE_SUPERADMIN)
        self.client.force_login(boss)
        response = self.client.get(reverse("view_audit_logs"))
        self.assertNotContains(response, "<script>alert('x')</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_chart_json_cannot_close_the_script_tag(self):
        encoded = script_json(["</script><script>alert(1)</script>"])
        self.assertNotIn("</script>", encoded)
        self.assertIn("\\u003C/script\\u003E", encoded)


class PaymentTests(Fixture):
    """H-1: the same payment form submitted twice records one payment."""

    def _pay(self, token, amount="20000"):
        return self.client.post(reverse("complete_visit", args=[self.visit.pk]), {
            "amount_paid": amount, "payment_mode": Payment.MODE_CASH, "bank_account": "", "mobile_account": "",
            "payment_notes": "", "payment_token": token,
        })

    def test_double_submit_records_one_payment(self):
        self.client.force_login(self.receptionist)
        self._pay("token-1")
        self._pay("token-1")
        self.assertEqual(Payment.objects.filter(visit=self.visit).count(), 1)
        self._pay("token-2")
        self.assertEqual(Payment.objects.filter(visit=self.visit).count(), 2)

    def test_deleting_a_payment_keeps_the_bank_statement_line(self):
        """M-4: statement lines are the bank's record and must survive."""
        account = BankAccount.objects.create(hospital=self.hospital, account_name="Main", account_number="1", bank_name="Bank")
        payment = Payment.objects.create(
            visit=self.visit, amount=Decimal("50000"), amount_paid=Decimal("50000"), mode=Payment.MODE_CARD, bank_account=account,
        )
        line = BankTransaction.objects.create(
            bank_account=account, transaction_date=timezone.localdate(), description="POS", amount=Decimal("50000"),
            is_reconciled=True, reconciled_with=payment,
        )
        payment.delete()
        line.refresh_from_db()
        self.assertIsNone(line.reconciled_with)
        self.assertFalse(line.is_reconciled)


class BillTotalTests(Fixture):
    """H-2 and M-10."""

    def test_two_stale_copies_both_count(self):
        first, second = Visit.objects.get(pk=self.visit.pk), Visit.objects.get(pk=self.visit.pk)
        first.adjust_total(Decimal("20000"))
        second.adjust_total(Decimal("5000"))
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("75000"))

    def test_bill_never_goes_below_zero(self):
        self.visit.adjust_total(Decimal("-999999"))
        self.assertEqual(self.visit.total_amount, Decimal("0"))

    def test_a_paid_charge_cannot_be_removed_without_a_refund(self):
        Payment.objects.create(visit=self.visit, amount=Decimal("50000"), amount_paid=Decimal("50000"), mode=Payment.MODE_CASH)
        self.assertIn("Refund the patient", self.visit.removal_blocked_message(Decimal("50000")))
        self.visit.adjust_total(Decimal("10000"))
        self.assertEqual(self.visit.removal_blocked_message(Decimal("10000")), "")


class CashDrawerTests(Fixture):
    """M-3: a closed drawer stays as it was counted."""

    def test_cash_after_closing_goes_to_a_new_drawer(self):
        closed = CashDrawer.objects.create(
            hospital=self.hospital, opening_balance=Decimal("0"), closing_balance=Decimal("1000"), closed_at=timezone.now(),
        )
        payment = Payment.objects.create(visit=self.visit, amount=Decimal("50000"), amount_paid=Decimal("20000"), mode=Payment.MODE_CASH)
        self.assertFalse(closed.transactions.exists())
        txn = CashTransaction.objects.get(payment=payment)
        self.assertIsNone(txn.cash_drawer.closed_at)
        self.assertEqual(txn.cash_drawer.opening_balance, Decimal("1000"))

    def test_correcting_a_counted_receipt_books_only_the_difference(self):
        payment = Payment.objects.create(visit=self.visit, amount=Decimal("50000"), amount_paid=Decimal("20000"), mode=Payment.MODE_CASH)
        drawer = CashTransaction.objects.get(payment=payment).cash_drawer
        drawer.closed_at = timezone.now()
        drawer.closing_balance = Decimal("20000")
        drawer.save()
        payment.amount_paid = Decimal("25000")
        payment.save()
        self.assertEqual(drawer.transactions.get().amount, Decimal("20000"))
        correction = CashTransaction.objects.filter(payment=payment).exclude(cash_drawer=drawer).get()
        self.assertEqual(correction.amount, Decimal("5000"))
        self.assertEqual(correction.transaction_type, CashTransaction.TYPE_CASH_IN)


class StockTests(Fixture):
    """H-6: expired batches are never given out."""

    def test_expired_batch_is_skipped(self):
        drug = InventoryItem.objects.create(hospital=self.hospital, name="Amoxicillin", category=InventoryItem.CATEGORY_DRUG)
        drug.add_or_update_batch("OLD", Decimal("10"), expiry_date=timezone.localdate() - timedelta(days=1))
        drug.add_or_update_batch("NEW", Decimal("5"), expiry_date=timezone.localdate() + timedelta(days=90))
        drug.refresh_from_db()
        self.assertEqual(drug.expired_quantity, Decimal("10"))
        self.assertEqual(drug.available_dispense_quantity, Decimal("5"))
        log = drug.consume_stock(Decimal("3"))
        self.assertEqual([row["batch"].batch_number for row in log], ["NEW"])
        self.assertEqual(drug.batches.get(batch_number="OLD").quantity, Decimal("10"))
        with self.assertRaises(ValueError):
            drug.consume_stock(Decimal("3"))  # only 2 in-date left


class LedgerTests(Fixture):
    """H-4: a salary edited several times leaves exactly its amount in the books."""

    def _salary_expense_net(self):
        from finance.models import Account, JournalLine

        lines = JournalLine.objects.filter(entry__hospital=self.hospital, account__sub_type="expense_salary")
        if not lines.exists():
            lines = JournalLine.objects.filter(entry__hospital=self.hospital, account__account_type=Account.TYPE_EXPENSE)
        return sum((line.debit - line.credit for line in lines), Decimal("0"))

    def test_salary_survives_repeated_edits(self):
        salary = Salary.objects.create(
            hospital=self.hospital, employee=self.doctor, month=timezone.localdate().replace(day=1),
            amount=Decimal("100000"), paid=True,
        )
        for amount in ("120000", "130000", "140000"):
            salary.amount = Decimal(amount)
            salary.save()
        self.assertEqual(self._salary_expense_net(), Decimal("140000"))
        from finance.models import PostingFailure
        self.assertFalse(PostingFailure.objects.filter(resolved_at__isnull=True).exists())


class NumberingTests(Fixture):
    """M-1: deleting a document doesn't make the next one reuse its number."""

    def test_number_after_a_delete_is_new(self):
        make = lambda: HospitalSubscriptionPayment.objects.create(
            hospital=self.hospital, amount=Decimal("1"), period_start=timezone.localdate(), period_end=timezone.localdate(),
        )
        first, second = make(), make()
        first.delete()
        third = make()
        self.assertNotEqual(third.receipt_number, second.receipt_number)
        self.assertTrue(third.receipt_number.endswith("-0003"))


class DeleteGuardTests(Fixture):
    """M-5: records with money on them aren't hard-deleted."""

    def test_user_with_salary_history_is_deactivated_not_deleted(self):
        Salary.objects.create(hospital=self.hospital, employee=self.doctor, month=timezone.localdate().replace(day=1), amount=Decimal("1"))
        self.client.force_login(self.admin)
        self.client.post(reverse("delete_user", args=[self.doctor.pk]))
        self.doctor.refresh_from_db()
        self.assertFalse(self.doctor.is_active)
        self.assertTrue(Salary.objects.filter(employee=self.doctor).exists())
