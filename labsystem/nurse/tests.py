from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital
from admin_dashboard.models import InventoryBatch, InventoryItem, InventoryTransaction
from doctor.models import Prescription
from lab.models import LabReport, TestCatalog, TestResult
from nurse.models import NurseNote
from reception.models import Patient, QueueEntry, Service, Visit, VisitService
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
        self.drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Paracetamol",
            category=InventoryItem.CATEGORY_DRUG,
            unit="strip",
            base_unit="tablet",
            units_per_pack="10",
            current_quantity="12",
            unit_cost="5.00",
            selling_price="20.00",
            reorder_level="2",
            strength_mg_per_unit="500",
        )
        InventoryBatch.objects.create(
            item=self.drug,
            batch_number="PCM-001",
            quantity="12",
            expiry_date="2028-01-31",
            unit_cost="5.00",
        )
        self.drug.recalculate_current_quantity()
        self.pharmacy_service = Service.objects.create(
            hospital=self.hospital,
            name="Pharmacy Item: Paracetamol",
            category=Service.CATEGORY_PHARMACY,
            price="2.00",
        )
        self.billing_line = VisitService.objects.create(
            visit=self.visit,
            service=self.pharmacy_service,
            price_at_time="12.00",
            notes="Prescription billing line",
        )
        self.prescription = Prescription.objects.create(
            visit=self.visit,
            drug=self.drug,
            dosage_mg="500",
            frequency_per_day=3,
            duration_days=2,
            prescribed_by=self.nurse,
            billing_visit_service=self.billing_line,
        )
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
        self.assertEqual(self.visit.status, Visit.STATUS_IN_PROGRESS)
        self.assertTrue(
            QueueEntry.objects.filter(
                visit=self.visit,
                queue_type=QueueEntry.TYPE_RECEPTION,
                processed=False,
            ).exists()
        )

    def test_nurse_can_dispense_prescription_and_deduct_inventory(self):
        response = self.client.post(
            reverse("dispense_prescription", args=[self.queue_entry.pk, self.prescription.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("perform_nursing", args=[self.queue_entry.pk]))
        self.prescription.refresh_from_db()
        self.drug.refresh_from_db()
        self.billing_line.refresh_from_db()
        self.assertTrue(self.prescription.dispensed)
        self.assertEqual(self.drug.current_quantity, Decimal("11.40"))
        self.assertTrue(self.billing_line.performed)
        batch = InventoryBatch.objects.get(item=self.drug, batch_number="PCM-001")
        self.assertEqual(batch.quantity, Decimal("11.40"))
        transaction = InventoryTransaction.objects.get(prescription=self.prescription)
        self.assertEqual(transaction.quantity, Decimal("6.00"))

    def test_nurse_can_dispense_reagent_prescription_with_bottle_logic(self):
        reagent_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Acetic Acid Reagent",
            category=InventoryItem.CATEGORY_REAGENT,
            unit="unit",
            pack_size_ml="5",
            current_quantity="10",
            unit_cost="1000.00",
            selling_price="1500.00",
            reorder_level="2",
        )
        InventoryBatch.objects.create(
            item=reagent_drug,
            batch_number="REAG-001",
            quantity="10",
            expiry_date="2028-01-31",
            unit_cost="1000.00",
        )
        reagent_drug.recalculate_current_quantity()

        reagent_prescription = Prescription.objects.create(
            visit=self.visit,
            drug=reagent_drug,
            dosage_mg="5",
            frequency_per_day=1,
            duration_days=1,
            prescribed_by=self.nurse,
            billing_visit_service=self.billing_line,
        )

        response = self.client.post(
            reverse("dispense_prescription", args=[self.queue_entry.pk, reagent_prescription.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("perform_nursing", args=[self.queue_entry.pk]))

        reagent_prescription.refresh_from_db()
        reagent_drug.refresh_from_db()
        batch = InventoryBatch.objects.get(item=reagent_drug, batch_number="REAG-001")
        transaction = InventoryTransaction.objects.get(prescription=reagent_prescription)

        self.assertTrue(reagent_prescription.dispensed)
        self.assertEqual(reagent_prescription.total_quantity, Decimal("1.00"))
        self.assertEqual(reagent_prescription.number_of_packs, 1)
        self.assertEqual(reagent_prescription.quantity_display, "1 bottle(s) covering 5 ml")
        self.assertEqual(reagent_drug.current_quantity, Decimal("9.00"))
        self.assertEqual(batch.quantity, Decimal("9.00"))
        self.assertEqual(transaction.quantity, Decimal("1.00"))

    def test_nurse_can_dispense_reagent_prescription_with_multiple_bottles(self):
        reagent_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Larger Reagent",
            category=InventoryItem.CATEGORY_REAGENT,
            unit="unit",
            pack_size_ml="5",
            current_quantity="10",
            unit_cost="1000.00",
            selling_price="1500.00",
            reorder_level="2",
        )
        InventoryBatch.objects.create(
            item=reagent_drug,
            batch_number="REAG-002",
            quantity="10",
            expiry_date="2028-01-31",
            unit_cost="1000.00",
        )
        reagent_drug.recalculate_current_quantity()

        reagent_prescription = Prescription.objects.create(
            visit=self.visit,
            drug=reagent_drug,
            dosage_mg="10",  # 10 ml dosage
            frequency_per_day=1,
            duration_days=1,
            prescribed_by=self.nurse,
            billing_visit_service=self.billing_line,
        )

        response = self.client.post(
            reverse("dispense_prescription", args=[self.queue_entry.pk, reagent_prescription.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("perform_nursing", args=[self.queue_entry.pk]))

        reagent_prescription.refresh_from_db()
        reagent_drug.refresh_from_db()
        batch = InventoryBatch.objects.get(item=reagent_drug, batch_number="REAG-002")
        transaction = InventoryTransaction.objects.get(prescription=reagent_prescription)

        self.assertTrue(reagent_prescription.dispensed)
        self.assertEqual(reagent_prescription.total_quantity, Decimal("2.00"))  # 2 bottles
        self.assertEqual(reagent_prescription.number_of_packs, 2)
        self.assertEqual(reagent_prescription.quantity_display, "2 bottle(s) covering 10 ml")
        self.assertEqual(reagent_drug.current_quantity, Decimal("8.00"))  # 10 - 2 = 8
        self.assertEqual(batch.quantity, Decimal("8.00"))
        self.assertEqual(transaction.quantity, Decimal("2.00"))
