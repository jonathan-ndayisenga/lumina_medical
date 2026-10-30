from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Hospital, HospitalModuleSubscription, Module, SubscriptionPlan, User
from doctor.models import Consultation
from reception.models import Patient, Payment, QueueEntry, Service, Visit, VisitService
from reception.workflow import debtor_move_available, ensure_pending_queue_entry


def _enable_modules(hospital, *codes):
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
        HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})


class _Base(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(name="Standard", price_monthly=Decimal("0"), price_yearly=Decimal("0"))
        self.hospital = Hospital.objects.create(name="Debtor Hospital", subdomain="debtor-hospital", subscription_plan=plan)
        _enable_modules(self.hospital, "doctor", "lab", "nurse", "sonographer", "finance")
        self.receptionist = User.objects.create_user(
            username="debt-reception", password="pass12345", role=User.ROLE_RECEPTIONIST,
            hospital=self.hospital, is_active=True,
        )
        self.accountant = User.objects.create_user(
            username="debt-accountant", password="pass12345", role=User.ROLE_ACCOUNTANT,
            hospital=self.hospital, is_active=True,
        )
        self.doctor = User.objects.create_user(
            username="debt-doctor", password="pass12345", role=User.ROLE_DOCTOR,
            hospital=self.hospital, is_active=True,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital, name="Owing Patient", registration_date=timezone.localdate(), age="30YRS", sex="M",
        )
        self.lab_service = Service.objects.create(
            hospital=self.hospital, name="Malaria RDT", category=Service.CATEGORY_LAB,
            price=Decimal("10000"), is_active=True,
        )
        self.procedure = Service.objects.create(
            hospital=self.hospital, name="Wound Dressing", category=Service.CATEGORY_PROCEDURE,
            price=Decimal("5000"), is_active=True,
        )


class DebtorQueueTests(_Base):
    def _owing_visit(self, days_waiting=0, paid=Decimal("4000")):
        visit = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, total_amount=Decimal("10000"),
            status=Visit.STATUS_IN_PROGRESS, created_by=self.receptionist,
        )
        VisitService.objects.create(
            visit=visit, service=self.lab_service, price_at_time=Decimal("10000"), is_approved=True, performed=True,
        )
        if paid:
            Payment.objects.create(
                visit=visit, amount=visit.total_amount, amount_paid=paid, mode=Payment.MODE_CASH,
                recorded_by=self.receptionist,
            )
        entry = ensure_pending_queue_entry(
            visit=visit, hospital=self.hospital, queue_type=QueueEntry.TYPE_RECEPTION, reason="Balance due",
        )
        visit.status = Visit.STATUS_READY_FOR_BILLING
        visit.save(update_fields=["status"])
        if days_waiting:
            QueueEntry.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(days=days_waiting))
            entry.refresh_from_db()
        return visit, entry

    def test_move_button_only_appears_after_three_days(self):
        _, fresh = self._owing_visit(days_waiting=1)
        _, waited = self._owing_visit(days_waiting=4)
        self.assertFalse(debtor_move_available(fresh))
        self.assertTrue(debtor_move_available(waited))

        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("reception_queue"))
        self.assertContains(response, reverse("reception_queue_move_to_debtors", args=[waited.pk]))
        self.assertNotContains(response, reverse("reception_queue_move_to_debtors", args=[fresh.pk]))

    def test_reception_moves_patient_to_debtors_after_three_days(self):
        visit, entry = self._owing_visit(days_waiting=4)
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_queue_move_to_debtors", args=[entry.pk]))

        entry.refresh_from_db()
        visit.refresh_from_db()
        self.assertTrue(entry.processed)
        self.assertIsNotNone(visit.moved_to_debtors_at)
        self.assertEqual(visit.balance_due, Decimal("6000"))

    def test_move_is_refused_before_three_days(self):
        visit, entry = self._owing_visit(days_waiting=1)
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_queue_move_to_debtors", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertFalse(entry.processed)

    def test_queue_moves_patients_to_debtors_on_its_own_after_thirty_days(self):
        old_visit, old_entry = self._owing_visit(days_waiting=31)
        recent_visit, recent_entry = self._owing_visit(days_waiting=10)
        self.client.force_login(self.receptionist)
        self.client.get(reverse("reception_queue"))

        old_entry.refresh_from_db()
        recent_entry.refresh_from_db()
        self.assertTrue(old_entry.processed)
        self.assertFalse(recent_entry.processed)
        old_visit.refresh_from_db()
        self.assertIsNotNone(old_visit.moved_to_debtors_at)

    def test_patient_with_other_open_work_is_never_moved(self):
        visit, entry = self._owing_visit(days_waiting=31)
        ensure_pending_queue_entry(visit=visit, hospital=self.hospital, queue_type=QueueEntry.TYPE_DOCTOR, reason="Review")
        self.client.force_login(self.receptionist)
        self.client.get(reverse("reception_queue"))
        entry.refresh_from_db()
        self.assertFalse(entry.processed)

    def test_finance_collects_debtor_balance_and_visit_completes(self):
        visit, entry = self._owing_visit(days_waiting=31)
        self.client.force_login(self.accountant)
        ledger = self.client.get(reverse("finance_debtors"))
        self.assertContains(ledger, reverse("finance_debtor_collect", args=[visit.pk]))

        response = self.client.post(
            reverse("finance_debtor_collect", args=[visit.pk]),
            {"amount_paid": "6000", "payment_mode": Payment.MODE_CASH, "payment_notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertTrue(visit.is_fully_paid)
        self.assertEqual(visit.status, Visit.STATUS_COMPLETED)

        payment = visit.payments.latest("id")
        receipt = self.client.get(reverse("print_payment_receipt", args=[payment.pk]))
        self.assertEqual(receipt.status_code, 200)

    def test_finance_cannot_collect_while_patient_is_still_at_reception(self):
        visit, _ = self._owing_visit(days_waiting=1)
        self.client.force_login(self.accountant)
        self.client.post(
            reverse("finance_debtor_collect", args=[visit.pk]),
            {"amount_paid": "6000", "payment_mode": Payment.MODE_CASH},
        )
        visit.refresh_from_db()
        self.assertEqual(visit.balance_due, Decimal("6000"))

    def test_receptionist_cannot_collect_from_the_debtor_ledger(self):
        visit, _ = self._owing_visit(days_waiting=31)
        self.client.force_login(self.receptionist)
        response = self.client.post(
            reverse("finance_debtor_collect", args=[visit.pk]),
            {"amount_paid": "6000", "payment_mode": Payment.MODE_CASH},
        )
        self.assertEqual(response.status_code, 403)

    def test_debts_older_than_a_year_are_only_cleared_on_request(self):
        old_visit, _ = self._owing_visit(days_waiting=31)
        recent_visit, _ = self._owing_visit(days_waiting=31)
        Visit.objects.filter(pk=old_visit.pk).update(visit_date=timezone.now() - timedelta(days=400))

        self.client.force_login(self.accountant)
        response = self.client.get(reverse("finance_debtors"))
        self.assertContains(response, "older than a year")
        old_visit.refresh_from_db()
        self.assertIsNone(old_visit.debt_cleared_at)  # never automatic

        self.client.post(reverse("finance_debtors_clear_old"))
        old_visit.refresh_from_db()
        recent_visit.refresh_from_db()
        self.assertIsNotNone(old_visit.debt_cleared_at)
        self.assertIsNone(recent_visit.debt_cleared_at)
        response = self.client.get(reverse("finance_debtors"))
        self.assertNotContains(response, reverse("finance_debtor_collect", args=[old_visit.pk]))
        self.assertContains(response, reverse("finance_debtor_collect", args=[recent_visit.pk]))


class FollowUpBillingTests(_Base):
    def setUp(self):
        super().setUp()
        self.parent = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, total_amount=Decimal("0"),
            status=Visit.STATUS_COMPLETED, created_by=self.receptionist,
        )
        Consultation.objects.create(
            visit=self.parent, created_by=self.doctor, signs_symptoms="Fever", diagnosis="Malaria", treatment="ACT",
        )
        self.visit = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, visit_type=Visit.TYPE_FOLLOW_UP,
            parent_visit=self.parent, total_amount=Decimal("0"), status=Visit.STATUS_IN_PROGRESS,
            created_by=self.receptionist,
        )
        ensure_pending_queue_entry(visit=self.visit, hospital=self.hospital, queue_type=QueueEntry.TYPE_DOCTOR)
        self.client.force_login(self.doctor)

    def test_doctor_chooses_billed_or_free_per_lab_test(self):
        self.client.post(
            reverse("send_lab_request_api", args=[self.visit.pk]),
            {"service_ids": [self.lab_service.pk], "free_follow_up": "1"},
        )
        line = self.visit.visit_services.get(service=self.lab_service)
        self.assertTrue(line.free_follow_up)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("0"))

        self.client.post(reverse("add_billable_service_api", args=[self.visit.pk]), {"service_id": self.procedure.pk})
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("5000"))
        self.assertFalse(self.visit.visit_services.get(service=self.procedure).free_follow_up)

    def test_billable_items_on_a_follow_up_can_be_billed(self):
        self.client.post(reverse("add_billable_service_api", args=[self.visit.pk]), {"service_id": self.procedure.pk})
        self.visit.refresh_from_db()
        self.visit.validate_billing_structure()  # used to reject any service on a follow-up

        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("complete_visit", args=[self.visit.pk]))
        self.assertEqual(response.status_code, 200)

    def test_free_flag_is_ignored_on_a_normal_visit(self):
        normal = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, total_amount=Decimal("0"),
            status=Visit.STATUS_IN_PROGRESS, created_by=self.receptionist,
        )
        self.client.post(
            reverse("add_billable_service_api", args=[normal.pk]),
            {"service_id": self.procedure.pk, "free_follow_up": "1"},
        )
        normal.refresh_from_db()
        self.assertEqual(normal.total_amount, Decimal("5000"))

    def test_removing_a_free_service_leaves_the_bill_alone(self):
        self.client.post(reverse("add_billable_service_api", args=[self.visit.pk]), {"service_id": self.procedure.pk})
        lab = self.lab_service
        self.client.post(reverse("send_lab_request_api", args=[self.visit.pk]), {"service_ids": [lab.pk], "free_follow_up": "1"})
        free_line = self.visit.visit_services.get(service=lab)
        self.client.post(reverse("remove_lab_service_api", args=[self.visit.pk, free_line.pk]))
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("5000"))

    def test_consultation_form_flags_the_follow_up_and_preselects_reception(self):
        response = self.client.get(reverse("consultation", args=[self.visit.pk]))
        self.assertContains(response, "FOLLOW-UP VISIT")
        self.assertContains(response, "Medicines you prescribe are always billed")
        self.assertContains(response, "Previous diagnosis:")
        self.assertTrue(response.context["form"].fields["send_to_reception"].initial)

    def test_reception_finishes_a_follow_up_with_nothing_to_pay(self):
        self.client.post(
            reverse("add_billable_service_api", args=[self.visit.pk]),
            {"service_id": self.procedure.pk, "free_follow_up": "1"},
        )
        self.client.force_login(self.receptionist)
        page = self.client.get(reverse("complete_visit", args=[self.visit.pk]))
        self.assertContains(page, "Finish Follow-up Visit")
        self.assertContains(page, "Free (follow-up)")

        QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR).update(processed=True)
        self.client.post(reverse("complete_visit", args=[self.visit.pk]), {"finish_follow_up_visit": "1"})
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.STATUS_COMPLETED)
