from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital
from doctor.models import Consultation
from reception.models import Patient, QueueEntry, Service, Visit, VisitService


class DoctorWorkflowTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Central", subdomain="lumina-doc")
        self.doctor = self.User.objects.create_user(
            username="doctor1",
            password="StrongPass123!",
            role=self.User.ROLE_DOCTOR,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="John Patient",
            age="31YRS",
            sex="M",
        )
        self.consult_service = Service.objects.create(
            hospital=self.hospital,
            name="Consultation",
            category=Service.CATEGORY_CONSULTATION,
            price="25.00",
        )
        self.lab_service = Service.objects.create(
            hospital=self.hospital,
            name="CBC",
            category=Service.CATEGORY_LAB,
            price="15.00",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            created_by=self.doctor,
            total_amount="25.00",
        )
        VisitService.objects.create(visit=self.visit, service=self.consult_service, price_at_time="25.00")
        QueueEntry.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            queue_type=QueueEntry.TYPE_DOCTOR,
            reason="Initial consultation: Consultation",
            requested_by=self.doctor,
        )
        self.client.force_login(self.doctor)

    def consultation_payload(self, **overrides):
        data = {
            "weight_kg": "70.0",
            "bp_systolic": "120",
            "bp_diastolic": "80",
            "pulse": "78",
            "respiratory_rate": "18",
            "temperature_celsius": "36.7",
            "glucose_mg_dl": "98",
            "oxygen_saturation": "99",
            "signs_symptoms": "Fever and weakness",
            "diagnosis": "Malaria rule-out",
            "treatment": "Supportive care",
            "follow_up_date": "",
            "lab_services": str(self.lab_service.pk),
        }
        data.update(overrides)
        return data

    def test_doctor_can_request_lab_and_bill_increases(self):
        response = self.client.post(reverse("consultation", args=[self.visit.pk]), self.consultation_payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("consultation_detail", args=[self.visit.pk]))
        self.visit.refresh_from_db()
        consultation = Consultation.objects.get(visit=self.visit)
        self.assertEqual(consultation.lab_requests, [self.lab_service.pk])
        self.assertEqual(self.visit.total_amount, 40)
        self.assertTrue(VisitService.objects.filter(visit=self.visit, service=self.lab_service).exists())

        lab_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False)
        self.assertEqual(lab_queue.reason, "Doctor requested: CBC")
        self.assertEqual(lab_queue.requested_by, self.doctor)
        self.assertTrue(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False).exists())

    def test_doctor_can_send_visit_to_billing(self):
        response = self.client.post(
            reverse("consultation", args=[self.visit.pk]),
            self.consultation_payload(lab_services="", send_to_reception="on"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("consultation_detail", args=[self.visit.pk]))
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.STATUS_READY_FOR_BILLING)
        self.assertFalse(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False).exists())
