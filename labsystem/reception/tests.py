from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Hospital, SubscriptionPlan, User
from admin_dashboard.models import (
    BankAccount,
    BankTransaction,
    CashDrawer,
    CashTransaction,
    MobileMoneyAccount,
    MobileMoneyTransaction,
)
from lab.models import LabReport
from lab.views import send_report_results_to_doctor
from reception.models import Patient, Payment, QueueEntry, Service, Visit, VisitService
from reception.workflow import ensure_pending_queue_entry


class ReceiptRenderingTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="Standard",
            price_monthly=Decimal("0.00"),
            price_yearly=Decimal("0.00"),
        )
        self.hospital = Hospital.objects.create(
            name="Lumina Test Hospital",
            subdomain="lumina",
            location="Kampala",
            box_number="PO Box 1",
            phone_number="+256700000000",
            email="test@example.com",
            subscription_plan=plan,
        )
        self.receptionist = User.objects.create_user(
            username="reception",
            password="pass12345",
            role=User.ROLE_RECEPTIONIST,
            hospital=self.hospital,
            is_active=True,
        )
        self.cash_drawer = CashDrawer.objects.create(
            hospital=self.hospital,
            opening_balance=Decimal("1000.00"),
        )

        self.consult = Service.objects.create(
            hospital=self.hospital,
            name="Consultation",
            category=Service.CATEGORY_CONSULTATION,
            price=Decimal("50.00"),
            is_active=True,
        )

        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="John Doe",
            registration_date=timezone.localdate(),
            age="35YRS",
            sex="M",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            total_amount=Decimal("50.00"),
            status=Visit.STATUS_READY_FOR_BILLING,
            created_by=self.receptionist,
        )
        VisitService.objects.create(
            visit=self.visit,
            service=self.consult,
            price_at_time=self.consult.price,
        )

    def test_receipt_renders_and_cash_syncs_to_drawer(self):
        self.client.force_login(self.receptionist)
        resp = self.client.post(
            reverse("complete_visit", kwargs={"visit_id": self.visit.pk}),
            data={
                "amount_paid": "50.00",
                "payment_mode": Payment.MODE_CASH,
                "bank_account": "",
                "mobile_account": "",
                "payment_notes": "Paid in cash",
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Payment Receipt")

        payment = Payment.objects.filter(visit=self.visit).order_by("-id").first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.amount_paid, Decimal("50.00"))

        cash_txn = CashTransaction.objects.filter(payment=payment).first()
        self.assertIsNotNone(cash_txn)
        self.assertEqual(cash_txn.cash_drawer_id, self.cash_drawer.id)
        self.assertEqual(cash_txn.amount, Decimal("50.00"))


class FinancialChannelSyncTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="Standard",
            price_monthly=Decimal("0.00"),
            price_yearly=Decimal("0.00"),
        )
        self.hospital = Hospital.objects.create(
            name="Lumina Test Hospital",
            subdomain="lumina",
            location="Kampala",
            subscription_plan=plan,
        )
        self.receptionist = User.objects.create_user(
            username="reception",
            password="pass12345",
            role=User.ROLE_RECEPTIONIST,
            hospital=self.hospital,
            is_active=True,
        )
        self.bank_account = BankAccount.objects.create(
            hospital=self.hospital,
            account_name="Main",
            account_number="12345",
            bank_name="Test Bank",
            opening_balance=Decimal("0.00"),
        )
        self.mobile_account = MobileMoneyAccount.objects.create(
            hospital=self.hospital,
            provider="MTN",
            number="+256700000001",
            is_active=True,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Jane Roe",
            registration_date=timezone.localdate(),
            age="30YRS",
            sex="F",
        )
        self.service = Service.objects.create(
            hospital=self.hospital,
            name="CBC",
            category=Service.CATEGORY_LAB,
            price=Decimal("30.00"),
            is_active=True,
        )

    def _create_billable_visit(self, total: Decimal) -> Visit:
        visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            total_amount=total,
            status=Visit.STATUS_READY_FOR_BILLING,
            created_by=self.receptionist,
        )
        VisitService.objects.create(visit=visit, service=self.service, price_at_time=self.service.price)
        return visit

    def test_card_payment_can_be_matched_to_bank_statement_line(self):
        visit = self._create_billable_visit(Decimal("30.00"))
        payment = Payment.objects.create(
            visit=visit,
            amount=visit.total_amount,
            amount_paid=Decimal("30.00"),
            mode=Payment.MODE_CARD,
            bank_account=self.bank_account,
            recorded_by=self.receptionist,
        )
        bank_txn = BankTransaction.objects.create(
            bank_account=self.bank_account,
            transaction_date=timezone.localdate(),
            description="Card settlement",
            amount=Decimal("30.00"),
            transaction_type=BankTransaction.TYPE_CREDIT,
            reference=payment.receipt_number,
            is_reconciled=False,
        )

        from admin_dashboard.views import payment_from_receipt_reference

        matched = payment_from_receipt_reference(payment.receipt_number, self.hospital, mode=Payment.MODE_CARD)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, payment.id)

        bank_txn.reconciled_with = matched
        bank_txn.is_reconciled = True
        bank_txn.save(update_fields=["reconciled_with", "is_reconciled"])

        bank_txn.refresh_from_db()
        self.assertEqual(bank_txn.reconciled_with_id, payment.id)
        self.assertTrue(bank_txn.is_reconciled)

    def test_mobile_money_payment_can_be_matched_to_mobile_statement_line(self):
        visit = self._create_billable_visit(Decimal("30.00"))
        payment = Payment.objects.create(
            visit=visit,
            amount=visit.total_amount,
            amount_paid=Decimal("30.00"),
            mode=Payment.MODE_MOBILE_MONEY,
            mobile_account=self.mobile_account,
            recorded_by=self.receptionist,
        )
        mm_txn = MobileMoneyTransaction.objects.create(
            mobile_money_account=self.mobile_account,
            transaction_date=timezone.localdate(),
            description="Mobile money settlement",
            amount=Decimal("30.00"),
            transaction_type=MobileMoneyTransaction.TYPE_CREDIT,
            reference=payment.receipt_number,
            is_reconciled=False,
        )

        from admin_dashboard.views import payment_from_receipt_reference

        matched = payment_from_receipt_reference(payment.receipt_number, self.hospital, mode=Payment.MODE_MOBILE_MONEY)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, payment.id)

        mm_txn.reconciled_with = matched
        mm_txn.is_reconciled = True
        mm_txn.save(update_fields=["reconciled_with", "is_reconciled"])

        mm_txn.refresh_from_db()
        self.assertEqual(mm_txn.reconciled_with_id, payment.id)
        self.assertTrue(mm_txn.is_reconciled)


class EndToEndPatientJourneyTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="Standard",
            price_monthly=Decimal("0.00"),
            price_yearly=Decimal("0.00"),
        )
        self.hospital = Hospital.objects.create(
            name="Lumina Test Hospital",
            subdomain="lumina",
            subscription_plan=plan,
        )
        self.receptionist = User.objects.create_user(
            username="reception",
            password="pass12345",
            role=User.ROLE_RECEPTIONIST,
            hospital=self.hospital,
            is_active=True,
        )
        self.doctor = User.objects.create_user(
            username="doctor",
            password="pass12345",
            role=User.ROLE_DOCTOR,
            hospital=self.hospital,
            is_active=True,
        )
        self.lab_attendant = User.objects.create_user(
            username="lab",
            password="pass12345",
            role=User.ROLE_LAB_ATTENDANT,
            hospital=self.hospital,
            is_active=True,
        )
        self.cash_drawer = CashDrawer.objects.create(hospital=self.hospital, opening_balance=Decimal("0.00"))

        self.consult = Service.objects.create(
            hospital=self.hospital,
            name="Consultation",
            category=Service.CATEGORY_CONSULTATION,
            price=Decimal("50.00"),
            is_active=True,
        )
        self.lab_service = Service.objects.create(
            hospital=self.hospital,
            name="CBC",
            category=Service.CATEGORY_LAB,
            price=Decimal("30.00"),
            is_active=True,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Sam Patient",
            registration_date=timezone.localdate(),
            age="40YRS",
            sex="M",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            total_amount=self.consult.price,
            status=Visit.STATUS_IN_PROGRESS,
            created_by=self.receptionist,
        )
        VisitService.objects.create(visit=self.visit, service=self.consult, price_at_time=self.consult.price)
        ensure_pending_queue_entry(
            visit=self.visit,
            hospital=self.hospital,
            queue_type=QueueEntry.TYPE_DOCTOR,
            reason="Initial consultation: Consultation",
            requested_by=self.receptionist,
        )

    def test_doctor_requests_lab_lab_sends_results_and_reception_bills(self):
        # Doctor requests lab services during consultation.
        self.client.force_login(self.doctor)
        resp = self.client.post(
            reverse("consultation", kwargs={"visit_id": self.visit.pk}),
            data={
                "signs_symptoms": "Fever",
                "diagnosis": "Malaria?",
                "treatment": "Test first",
                "follow_up_date": "",
                "bp": "",
                "pr": "",
                "rr": "",
                "temp": "",
                "glucose": "",
                "spo2": "",
                "send_to_nurse": "",
                "send_to_reception": "",
                "lab_services": str(self.lab_service.id),
            },
        )
        self.assertEqual(resp.status_code, 302)

        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("80.00"))
        self.assertTrue(
            QueueEntry.objects.filter(
                visit=self.visit,
                queue_type=QueueEntry.TYPE_LAB_DOCTOR,
                processed=False,
            ).exists()
        )

        # Lab completes the report and sends results back to doctor.
        report = LabReport.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            patient_name=self.patient.name,
            patient_age=self.patient.age,
            patient_sex=self.patient.sex,
            sample_date=timezone.localdate(),
            specimen_type="BLOOD",
            attendant=self.lab_attendant,
            attendant_name="Lab Tech",
        )
        send_report_results_to_doctor(report)

        self.assertTrue(
            QueueEntry.objects.filter(
                visit=self.visit,
                queue_type=QueueEntry.TYPE_DOCTOR,
                processed=False,
                reason__icontains="lab results ready",
            ).exists()
        )

        # Reception completes billing with cash; should mirror into cash drawer.
        self.visit.status = Visit.STATUS_READY_FOR_BILLING
        self.visit.save(update_fields=["status"])
        self.client.force_login(self.receptionist)
        resp = self.client.post(
            reverse("complete_visit", kwargs={"visit_id": self.visit.pk}),
            data={
                "amount_paid": "80.00",
                "payment_mode": Payment.MODE_CASH,
                "bank_account": "",
                "mobile_account": "",
                "payment_notes": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        payment = Payment.objects.filter(visit=self.visit).order_by("-id").first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(CashTransaction.objects.filter(payment=payment).first())
