from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital
from lab.models import LabReport, TestCatalog, TestProfile, TestResult
from lab.views import report_needs_doctor_send, send_report_results_to_doctor
from reception.models import Patient, QueueEntry, Service, Visit, VisitService


class LabDoctorHandoffTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Lab", subdomain="lumina-lab-handoff")
        self.doctor = self.User.objects.create_user(
            username="labdoctor",
            password="StrongPass123!",
            role=self.User.ROLE_DOCTOR,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Handoff Patient",
            age="29YRS",
            sex="F",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            created_by=self.doctor,
            total_amount="15.00",
        )
        self.report = LabReport.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            patient_name=self.patient.name,
            patient_age=self.patient.age,
            patient_sex=self.patient.sex,
            sample_date=date.today(),
            specimen_type="BLOOD",
        )
        test = TestCatalog.objects.create(name="CBC", unit="cells")
        TestResult.objects.create(
            lab_report=self.report,
            test=test,
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

    def test_pending_doctor_queue_requires_explicit_send(self):
        self.assertTrue(report_needs_doctor_send(self.report))
        self.lab_queue.refresh_from_db()
        self.assertFalse(self.lab_queue.processed)
        self.assertFalse(self.report.sent_to_doctor)

    def test_send_report_results_to_doctor_marks_handoff_complete(self):
        self.assertTrue(send_report_results_to_doctor(self.report))
        self.report.refresh_from_db()
        self.lab_queue.refresh_from_db()
        self.assertTrue(self.report.sent_to_doctor)
        self.assertTrue(self.lab_queue.processed)
        doctor_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False)
        self.assertIn("Lab results ready for review", doctor_queue.reason)
        self.assertEqual(doctor_queue.requested_by, self.doctor)


class SequentialRequestedLabWorkflowTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Sequential", subdomain="lumina-sequential")
        self.lab_user = self.User.objects.create_user(
            username="labseq",
            password="StrongPass123!",
            role=self.User.ROLE_LAB_ATTENDANT,
            hospital=self.hospital,
        )
        self.doctor = self.User.objects.create_user(
            username="docseq",
            password="StrongPass123!",
            role=self.User.ROLE_DOCTOR,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Sequential Patient",
            age="33YRS",
            sex="F",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            created_by=self.doctor,
            total_amount="30.00",
        )
        self.cbc_profile = TestProfile.objects.create(name="CBC Sequential", code="cbc-seq")
        self.urine_profile = TestProfile.objects.create(name="Urinalysis Sequential", code="urinalysis-seq")
        self.cbc_service = Service.objects.create(
            hospital=self.hospital,
            name="CBC",
            category=Service.CATEGORY_LAB,
            price="15.00",
            test_profile=self.cbc_profile,
        )
        self.urine_service = Service.objects.create(
            hospital=self.hospital,
            name="Urinalysis",
            category=Service.CATEGORY_LAB,
            price="15.00",
            test_profile=self.urine_profile,
        )
        self.cbc_visit_service = VisitService.objects.create(
            visit=self.visit,
            service=self.cbc_service,
            price_at_time="15.00",
        )
        self.urine_visit_service = VisitService.objects.create(
            visit=self.visit,
            service=self.urine_service,
            price_at_time="15.00",
        )
        self.queue_entry = QueueEntry.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            queue_type=QueueEntry.TYPE_LAB_DOCTOR,
            reason="Doctor requested: CBC, Urinalysis",
            requested_by=self.doctor,
        )
        self.report = LabReport.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            patient_name=self.patient.name,
            patient_age=self.patient.age,
            patient_sex=self.patient.sex,
            sample_date=date.today(),
            specimen_type="BLOOD",
            attendant=self.lab_user,
            attendant_name="Lab Seq",
        )
        TestCatalog.objects.get_or_create(name="Hemoglobin", defaults={"unit": "g/dL"})
        self.client.force_login(self.lab_user)

    def build_post_data(self, selected_visit_service_id):
        return {
            "profile": str(self.cbc_profile.pk),
            "patient_name": self.patient.name,
            "patient_age": self.patient.age,
            "age_value": "33",
            "age_unit": "YRS",
            "patient_sex": self.patient.sex,
            "referred_by": "",
            "sample_date": date.today().isoformat(),
            "specimen_type": "BLOOD",
            "attendant_name": "Lab Seq",
            "comments": "",
            "requested_service_id": str(selected_visit_service_id),
            "results-TOTAL_FORMS": "1",
            "results-INITIAL_FORMS": "0",
            "results-MIN_NUM_FORMS": "0",
            "results-MAX_NUM_FORMS": "1000",
            "results-0-id": "",
            "results-0-source_profile": str(self.cbc_profile.pk),
            "results-0-section_name": "CBC",
            "results-0-display_order": "1",
            "results-0-test_name": "Hemoglobin",
            "results-0-result_value": "12.5",
            "results-0-reference_range": "12-16",
            "results-0-unit": "g/dL",
            "results-0-comment": "",
            "results-0-DELETE": "",
            "action": "save_report",
        }

    def test_saving_one_requested_service_keeps_remaining_service_in_queue(self):
        response = self.client.post(
            reverse("report_edit", args=[self.report.pk]),
            self.build_post_data(self.cbc_visit_service.pk),
        )

        self.assertRedirects(response, reverse("lab_queue"))
        self.cbc_visit_service.refresh_from_db()
        self.urine_visit_service.refresh_from_db()
        self.assertTrue(self.cbc_visit_service.performed)
        self.assertFalse(self.urine_visit_service.performed)
        self.assertTrue(report_needs_doctor_send(self.report))
        self.assertFalse(self.queue_entry.processed)
