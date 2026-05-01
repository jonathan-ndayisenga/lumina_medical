from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital
from admin_dashboard.models import InventoryItem
from doctor.models import Consultation, Prescription
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
        self.second_lab_service = Service.objects.create(
            hospital=self.hospital,
            name="Urinalysis",
            category=Service.CATEGORY_LAB,
            price="12.00",
        )
        self.billable_service = Service.objects.create(
            hospital=self.hospital,
            name="Injection",
            category=Service.CATEGORY_PROCEDURE,
            price="10.00",
        )
        self.tablet_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Paracetamol",
            category=InventoryItem.CATEGORY_DRUG,
            unit="strip",
            base_unit="tablet",
            units_per_pack="10",
            strength_mg_per_unit="500",
            current_quantity="12",
            unit_cost="5.00",
            selling_price="20.00",
            reorder_level="20",
        )
        self.syrup_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Amoxicillin Syrup",
            category=InventoryItem.CATEGORY_SYRUP,
            unit="bottle",
            base_unit="ml",
            units_per_pack="100",
            current_quantity="20",
            unit_cost="5000.00",
            selling_price="10.00",
            reorder_level="5",
        )
        self.tube_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Clotrimazole Cream",
            category=InventoryItem.CATEGORY_TUBE,
            unit="tube",
            base_unit="g",
            units_per_pack="30",
            current_quantity="12",
            unit_cost="3.00",
            selling_price="8.00",
            reorder_level="2",
            days_covered_per_pack="7.00",
        )
        self.iv_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Normal Saline IV",
            category=InventoryItem.CATEGORY_IV,
            unit="bag",
            base_unit="ml",
            units_per_pack="500",
            current_quantity="10",
            unit_cost="3000.00",
            selling_price="6000.00",
            reorder_level="2",
        )
        self.im_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Diclofenac IM",
            category=InventoryItem.CATEGORY_IM,
            unit="vial",
            base_unit="ml",
            units_per_pack="3",
            current_quantity="30",
            unit_cost="800.00",
            selling_price="1500.00",
            reorder_level="5",
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
        }
        data.update(overrides)
        return data

    def test_consultation_page_renders_searchable_prescription_picker(self):
        response = self.client.get(reverse("consultation", args=[self.visit.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="drug-search-input"', html=False)
        self.assertContains(response, 'id="drug-results-select"', html=False)
        self.assertContains(response, "Click into the search field to browse all stocked drugs")

    def test_doctor_can_save_consultation_without_creating_duplicate_lab_requests(self):
        response = self.client.post(
            reverse("consultation", args=[self.visit.pk]),
            self.consultation_payload(send_to_reception="on"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("consultation_detail", args=[self.visit.pk]))
        self.visit.refresh_from_db()
        consultation = Consultation.objects.get(visit=self.visit)
        self.assertEqual(consultation.lab_requests, [])
        self.assertEqual(self.visit.total_amount, Decimal("25.00"))
        self.assertFalse(VisitService.objects.filter(visit=self.visit, service=self.lab_service).exists())
        self.assertFalse(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False).exists())

    def test_doctor_can_send_lab_request_immediately(self):
        response = self.client.post(
            reverse("send_lab_request_api", args=[self.visit.pk]),
            {"service_id": self.lab_service.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("40.00"))
        visit_service = VisitService.objects.get(visit=self.visit, service=self.lab_service)
        self.assertFalse(visit_service.performed)
        lab_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False)
        self.assertEqual(lab_queue.reason, "Doctor requested: CBC")

        payload = response.json()
        self.assertEqual(payload["service"]["service_name"], "CBC")
        self.assertEqual(payload["pending_services"][0]["service_name"], "CBC")

    def test_doctor_can_send_multiple_lab_requests_in_one_call(self):
        response = self.client.post(
            reverse("send_lab_request_api", args=[self.visit.pk]),
            {"service_ids": [self.lab_service.pk, self.second_lab_service.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("52.00"))
        self.assertTrue(VisitService.objects.filter(visit=self.visit, service=self.lab_service).exists())
        self.assertTrue(VisitService.objects.filter(visit=self.visit, service=self.second_lab_service).exists())
        lab_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False)
        self.assertEqual(lab_queue.reason, "Doctor requested: CBC, Urinalysis")

        payload = response.json()
        self.assertEqual(len(payload["services"]), 2)
        self.assertEqual(
            {item["service_name"] for item in payload["pending_services"]},
            {"CBC", "Urinalysis"},
        )

    def test_doctor_can_add_billable_service_without_reload(self):
        response = self.client.post(
            reverse("add_billable_service_api", args=[self.visit.pk]),
            {"service_id": self.billable_service.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("35.00"))
        self.assertTrue(VisitService.objects.filter(visit=self.visit, service=self.billable_service).exists())
        payload = response.json()
        self.assertEqual(payload["service"]["service_name"], "Injection")

    def test_doctor_can_add_tablet_prescription_without_reload(self):
        response = self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": self.tablet_drug.pk,
                "dosage_mg": "500",
                "frequency_per_day": "3",
                "duration_days": "5",
                "notes": "Take after meals",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.visit.refresh_from_db()
        prescription = Prescription.objects.get(visit=self.visit, drug=self.tablet_drug)
        self.assertEqual(prescription.total_quantity, Decimal("15.00"))
        self.assertEqual(prescription.total_price, Decimal("30.00"))
        self.assertEqual(self.visit.total_amount, Decimal("55.00"))
        self.assertIsNotNone(prescription.billing_visit_service)
        self.assertEqual(prescription.billing_visit_service.price_at_time, Decimal("30.00"))
        self.assertEqual(response.json()["prescription"]["quantity_display"], "15 tablet(s)")

    def test_doctor_can_remove_pending_prescription_and_restore_visit_total(self):
        self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": self.tablet_drug.pk,
                "dosage_mg": "500",
                "frequency_per_day": "3",
                "duration_days": "5",
            },
        )
        prescription = Prescription.objects.get(visit=self.visit, drug=self.tablet_drug)
        billing_line_id = prescription.billing_visit_service_id

        response = self.client.post(
            reverse("remove_prescription_api", args=[self.visit.pk, prescription.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("25.00"))
        self.assertFalse(Prescription.objects.filter(pk=prescription.pk).exists())
        self.assertFalse(VisitService.objects.filter(pk=billing_line_id).exists())

    def test_doctor_can_add_liquid_prescription_and_calculate_bottles(self):
        response = self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": self.syrup_drug.pk,
                "dosage_mg": "10",
                "frequency_per_day": "3",
                "duration_days": "5",
            },
        )

        self.assertEqual(response.status_code, 200)
        prescription = Prescription.objects.get(visit=self.visit, drug=self.syrup_drug)
        self.assertEqual(prescription.total_quantity, Decimal("2.00"))
        self.assertEqual(prescription.number_of_packs, 2)
        self.assertEqual(prescription.total_price, Decimal("20.00"))
        self.assertEqual(response.json()["prescription"]["quantity_display"], "2 bottle(s) covering 150 ml")

    def test_doctor_can_add_tube_prescription_and_calculate_whole_tubes(self):
        response = self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": self.tube_drug.pk,
                "dosage_mg": "1",
                "frequency_per_day": "2",
                "duration_days": "10",
            },
        )

        self.assertEqual(response.status_code, 200)
        prescription = Prescription.objects.get(visit=self.visit, drug=self.tube_drug)
        self.assertEqual(prescription.number_of_packs, 2)
        self.assertEqual(prescription.total_quantity, Decimal("2.00"))
        self.assertEqual(prescription.total_price, Decimal("16.00"))
        self.assertEqual(response.json()["prescription"]["quantity_display"], "2 tube(s)")

    def test_doctor_can_add_iv_prescription_and_calculate_whole_bags(self):
        response = self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": self.iv_drug.pk,
                "dosage_mg": "250",
                "frequency_per_day": "2",
                "duration_days": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        prescription = Prescription.objects.get(visit=self.visit, drug=self.iv_drug)
        self.assertEqual(prescription.total_quantity, Decimal("2.00"))
        self.assertEqual(prescription.number_of_packs, 2)
        self.assertEqual(prescription.total_price, Decimal("12000.00"))
        self.assertEqual(response.json()["prescription"]["quantity_display"], "2 bag(s) covering 1000 ml")

    def test_doctor_can_add_im_prescription_and_calculate_whole_vials(self):
        response = self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": self.im_drug.pk,
                "dosage_mg": "2",
                "frequency_per_day": "2",
                "duration_days": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        prescription = Prescription.objects.get(visit=self.visit, drug=self.im_drug)
        self.assertEqual(prescription.total_quantity, Decimal("3.00"))
        self.assertEqual(prescription.number_of_packs, 3)
        self.assertEqual(prescription.total_price, Decimal("4500.00"))
        self.assertEqual(response.json()["prescription"]["quantity_display"], "3 vial(s) covering 8 ml")

    def test_doctor_can_send_visit_to_billing(self):
        response = self.client.post(
            reverse("consultation", args=[self.visit.pk]),
            self.consultation_payload(send_to_reception="on"),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("consultation_detail", args=[self.visit.pk]))
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.STATUS_READY_FOR_BILLING)
        self.assertFalse(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False).exists())
