from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital
from lab.models import LabReport, TestCatalog, TestResult
from nurse.models import NurseNote
from reception.models import Patient, QueueEntry, Visit
from lab.views import mark_lab_queue_complete


class LabWorkflowTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Central", subdomain="lumina-lab")
        self.doctor = self.User.objects.create_user(
            username="doctorlab",
            password="StrongPass123!",
            role=self.User.ROLE_DOCTOR,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(hospital=self.hospital, name="Lab Patient", age="26YRS", sex="F")
        self.visit = Visit.objects.create(patient=self.patient, hospital=self.hospital, created_by=self.doctor, total_amount="15.00")
        self.report = LabReport.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            patient_name=self.patient.name,
            patient_age=self.patient.age,
            patient_sex=self.patient.sex,
            sample_date=date.today(),
            specimen_type="BLOOD",
        )
        self.test = TestCatalog.objects.create(name="CBC", unit="cells")
        TestResult.objects.create(
            lab_report=self.report,
            test=self.test,
            result_value="Normal",
            reference_range="4-10",
            unit="cells",
        )
        self.lab_queue = QueueEntry.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            queue_type=QueueEntry.TYPE_LAB_DOCTOR,
            reason="Doctor requested: CBC",
            requested_by=self.doctor,
        )

    def test_lab_completion_returns_patient_to_doctor_queue(self):
        returned_to_doctor = mark_lab_queue_complete(self.report)

        self.assertTrue(returned_to_doctor)
        self.lab_queue.refresh_from_db()
        self.assertTrue(self.lab_queue.processed)
        doctor_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False)
        self.assertIn("Lab results ready for", doctor_queue.reason)
        self.assertEqual(doctor_queue.requested_by, self.doctor)


class NurseWorkflowTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Central", subdomain="lumina-nurse")
        self.nurse = self.User.objects.create_user(
            username="nurse1",
            password="StrongPass123!",
            role=self.User.ROLE_NURSE,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(hospital=self.hospital, name="Nurse Patient", age="22YRS", sex="M")
        self.visit = Visit.objects.create(patient=self.patient, hospital=self.hospital, created_by=self.nurse, total_amount="25.00")
        self.queue_entry = QueueEntry.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            queue_type=QueueEntry.TYPE_NURSE,
            reason="Doctor requested nursing follow-up.",
            requested_by=self.nurse,
        )
        self.client.force_login(self.nurse)

    def test_nurse_can_send_patient_back_to_doctor(self):
        response = self.client.post(
            reverse("perform_nursing", args=[self.queue_entry.pk]),
            {
                "weight_kg": "70.0",
                "bp_systolic": "120",
                "bp_diastolic": "80",
                "notes": "Vitals stabilised",
                "action": "back_to_doctor",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("nurse_queue"))
        self.queue_entry.refresh_from_db()
        self.assertTrue(self.queue_entry.processed)
        self.assertTrue(NurseNote.objects.filter(visit=self.visit).exists())
        doctor_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False)
        self.assertIn("Nurse completed", doctor_queue.reason)

    def test_nurse_can_send_patient_to_billing(self):
        response = self.client.post(
            reverse("perform_nursing", args=[self.queue_entry.pk]),
            {
                "weight_kg": "70.0",
                "bp_systolic": "120",
                "bp_diastolic": "80",
                "notes": "Ready for discharge",
                "action": "to_billing",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("nurse_queue"))
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.STATUS_READY_FOR_BILLING)
