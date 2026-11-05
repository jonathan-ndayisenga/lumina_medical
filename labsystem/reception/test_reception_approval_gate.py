from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, Hospital, HospitalModuleSubscription, Module, SubscriptionPlan, User
from lab.models import LabOrder, LabTest, OrderStage, ResultType, ServiceCategory
from reception.models import Patient, QueueEntry, Service, Visit, VisitService
from reception.workflow import close_orphaned_lab_queue_entries, queue_counts_for_hospital


class ReceptionApprovalGateTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(name="Standard", price_monthly=Decimal("0"), price_yearly=Decimal("0"))
        self.hospital = Hospital.objects.create(name="Gate Hospital", subdomain="gate-hospital", subscription_plan=plan)
        for code in ("doctor", "lab", "nurse", "sonographer", "finance"):
            module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
            HospitalModuleSubscription.objects.get_or_create(hospital=self.hospital, module=module, defaults={"is_active": True})
        make_user = lambda name, role: User.objects.create_user(
            username=name, password="pass12345", role=role, hospital=self.hospital, is_active=True,
        )
        self.doctor = make_user("gate-doctor", User.ROLE_DOCTOR)
        self.receptionist = make_user("gate-reception", User.ROLE_RECEPTIONIST)
        self.lab_user = make_user("gate-lab", User.ROLE_LAB_ATTENDANT)
        patient = Patient.objects.create(
            hospital=self.hospital, name="Gate Patient", registration_date=timezone.localdate(), age="30YRS", sex="M",
        )
        self.visit = Visit.objects.create(
            patient=patient, hospital=self.hospital, status=Visit.STATUS_IN_PROGRESS,
            total_amount=Decimal("0"), created_by=self.receptionist,
        )
        self.malaria = Service.objects.create(
            hospital=self.hospital, name="Malaria RDT", category=Service.CATEGORY_LAB, price=Decimal("10000"), is_active=True,
        )
        self.cbc = Service.objects.create(
            hospital=self.hospital, name="CBC", category=Service.CATEGORY_LAB, price=Decimal("20000"), is_active=True,
        )
        self.scan = Service.objects.create(
            hospital=self.hospital, name="Abdominal Scan", category=Service.CATEGORY_SCAN, price=Decimal("50000"), is_active=True,
        )
        category = ServiceCategory.objects.create(name="Gate Lab")
        self.test = LabTest.objects.create(
            hospital=self.hospital, name="Malaria RDT", code="MRDT", category=category, result_type=ResultType.choices[0][0],
        )
        self.cbc_test = LabTest.objects.create(
            hospital=self.hospital, name="CBC", code="CBC", category=category, result_type=ResultType.choices[0][0],
        )
        self.malaria.lab_tests_next.add(self.test)
        self.cbc.lab_tests_next.add(self.cbc_test)
        self.client.force_login(self.doctor)

    def _reception_entry(self):
        return QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False).first()

    def _request_lab(self, service):
        return self.client.post(reverse("send_lab_request_api", args=[self.visit.pk]), {"service_ids": [service.pk]})

    def test_lab_request_waits_at_reception_and_says_so(self):
        response = self._request_lab(self.malaria)
        self.assertIn("reception for approval", response.json()["message"])
        self.assertFalse(response.json()["service"]["is_approved"])
        self.assertIsNotNone(self._reception_entry())
        self.assertFalse(QueueEntry.objects.filter(
            visit=self.visit, queue_type__in=[QueueEntry.TYPE_LAB_DOCTOR, QueueEntry.TYPE_LAB_RECEPTION],
        ).exists())

    def test_second_request_cannot_reach_a_patient_already_in_the_lab_queue(self):
        self._request_lab(self.malaria)
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_queue_approve_lab", args=[self._reception_entry().pk]))

        self.client.force_login(self.doctor)
        self._request_lab(self.cbc)

        self.client.force_login(self.lab_user)
        self.client.get(reverse("queue"))
        held = VisitService.objects.get(visit=self.visit, service=self.cbc)
        self.assertFalse(held.is_approved)
        self.assertFalse(LabOrder.objects.filter(visit_service=held).exists())

    def test_scan_added_to_the_bill_waits_at_reception(self):
        response = self.client.post(reverse("add_billable_service_api", args=[self.visit.pk]), {"service_id": self.scan.pk})
        self.assertIn("reception for approval", response.json()["message"])
        self.assertFalse(VisitService.objects.get(visit=self.visit, service=self.scan).is_approved)
        entry = self._reception_entry()
        self.assertIsNotNone(entry)
        self.assertFalse(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_SONOGRAPHER).exists())

        self.client.force_login(self.receptionist)
        self.assertContains(self.client.get(reverse("reception_queue")), "Approve Scan")
        self.client.post(reverse("reception_queue_approve_scan", args=[entry.pk]))
        self.assertTrue(QueueEntry.objects.filter(
            visit=self.visit, queue_type=QueueEntry.TYPE_SONOGRAPHER, processed=False,
        ).exists())

    def test_approving_one_kind_keeps_the_patient_for_the_other(self):
        self._request_lab(self.malaria)
        self.client.post(reverse("add_billable_service_api", args=[self.visit.pk]), {"service_id": self.scan.pk})
        entry = self._reception_entry()

        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_queue_approve_lab", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertFalse(entry.processed, "the scan still needs approving")

        self.client.post(reverse("reception_queue_approve_scan", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertTrue(entry.processed)

    def _lab_entry(self):
        return QueueEntry.objects.create(
            hospital=self.hospital, visit=self.visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, reason="Doctor requested",
        )

    def test_entry_with_nothing_behind_it_is_not_counted_and_gets_closed(self):
        entry = self._lab_entry()
        self.assertEqual(queue_counts_for_hospital(self.hospital)["lab"], 0)

        self.assertEqual(close_orphaned_lab_queue_entries(self.hospital), 1)
        entry.refresh_from_db()
        self.assertTrue(entry.processed)
        self.assertTrue(AuditLog.objects.filter(action="lab_queue_orphan_closed", object_id=str(entry.pk)).exists())

    def test_badge_counts_exactly_the_people_on_the_lab_queue_page(self):
        VisitService.objects.create(visit=self.visit, service=self.malaria, price_at_time=Decimal("10000"), is_approved=True)
        self._lab_entry()
        self.assertEqual(queue_counts_for_hospital(self.hospital)["lab"], 1)

        self.client.force_login(self.lab_user)
        response = self.client.get(reverse("queue"))
        self.assertEqual(len(response.context["rows"]), 1)

    def test_results_awaiting_review_keep_their_entry_but_leave_the_badge(self):
        vs = VisitService.objects.create(visit=self.visit, service=self.malaria, price_at_time=Decimal("10000"), is_approved=True)
        LabOrder.objects.create(visit_service=vs, test=self.test, hospital=self.hospital, stage=OrderStage.ENTERED)
        entry = self._lab_entry()

        self.assertEqual(queue_counts_for_hospital(self.hospital)["lab"], 0)
        self.assertEqual(close_orphaned_lab_queue_entries(self.hospital), 0)
        entry.refresh_from_db()
        self.assertFalse(entry.processed)

    def test_queue_status_changes_when_a_request_arrives_and_does_not_count_as_activity(self):
        self.client.force_login(self.receptionist)
        session = self.client.session
        last_active = int(timezone.now().timestamp()) - 60
        session["_session_last_activity_ts"] = last_active
        session.save()
        before = self.client.get(reverse("reception_queue_status"), headers={"X-Background-Poll": "1"}).json()
        self.assertEqual(self.client.session["_session_last_activity_ts"], last_active)

        self.client.force_login(self.doctor)
        self._request_lab(self.malaria)
        self.client.force_login(self.receptionist)
        after = self.client.get(reverse("reception_queue_status")).json()
        self.assertNotEqual(before["signature"], after["signature"])
