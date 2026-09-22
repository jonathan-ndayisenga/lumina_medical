from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital, HospitalModuleSubscription, Module
from admin_dashboard.models import InventoryBatch, InventoryItem, InventoryTransaction
from doctor.models import Prescription
from nurse.models import NurseNote, ScanReport
from reception.models import Patient, QueueEntry, Service, Visit, VisitService


def _enable_modules(hospital, *codes):
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
        HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})


class NurseWorkflowTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Central", subdomain="lumina-nurse")
        _enable_modules(self.hospital, "doctor", "lab", "nurse")
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
            units_per_pack=Decimal("10"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("5.00"),
            selling_price=Decimal("20.00"),
            reorder_level=Decimal("2"),
            strength_mg_per_unit=Decimal("500"),
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
            pack_size_ml=Decimal("5"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("1000.00"),
            selling_price=Decimal("1500.00"),
            reorder_level=Decimal("2"),
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
            pack_size_ml=Decimal("5"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("1000.00"),
            selling_price=Decimal("1500.00"),
            reorder_level=Decimal("2"),
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

    def test_nurse_cannot_dispense_when_insufficient_stock(self):
        # Zero the batch first, then recalculate — avoids the InventoryItem.save()
        # override that resets current_quantity from self.quantity when quantity is non-zero.
        batch = InventoryBatch.objects.get(item=self.drug)
        batch.quantity = Decimal("0")
        batch.save()
        self.drug.recalculate_current_quantity()

        response = self.client.post(
            reverse("dispense_prescription", args=[self.queue_entry.pk, self.prescription.pk]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], reverse("perform_nursing", args=[self.queue_entry.pk]))

        # Check error message in response
        messages_list = list(response.wsgi_request._messages)
        self.assertEqual(len(messages_list), 1)
        self.assertIn("Cannot dispense", str(messages_list[0]))
        self.assertIn("insufficient stock", str(messages_list[0]))
        self.assertIn("Current stock:", str(messages_list[0]))
        self.assertIn("prescription requires: 6 tablet(s)", str(messages_list[0]))

        # Prescription should not be dispensed
        self.prescription.refresh_from_db()
        self.assertFalse(self.prescription.dispensed)


class SonographerScanReportBillingTests(TestCase):
    """Once a scan report is finalized, the handoff choice (nurse / doctor /
    reception) was a one-shot decision made at finalize time -- nothing on
    the "report already final" screen could route the patient anywhere
    afterward. Reported gap: a scan that came from reception, or one the
    sonographer forgot to route, had no way to be sent back for billing
    once finalized."""

    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Scan Hospital", subdomain="lumina-scan-billing")
        _enable_modules(self.hospital, "sonographer", "reception")
        self.sonographer = self.User.objects.create_user(
            username="sono1", password="StrongPass123!",
            role=self.User.ROLE_SONOGRAPHER, hospital=self.hospital,
        )
        self.patient = Patient.objects.create(hospital=self.hospital, name="Scan Patient", age="30YRS", sex="F")
        self.visit = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, created_by=self.sonographer, total_amount="0.00",
        )
        self.queue_entry = QueueEntry.objects.create(
            hospital=self.hospital, visit=self.visit,
            queue_type=QueueEntry.TYPE_SONOGRAPHER, reason="Reception sent patient for scan",
        )
        self.report = ScanReport.objects.create(
            visit=self.visit, sonographer=self.sonographer, scan_type=ScanReport.SCAN_ABDOMINAL,
            findings="Normal", impression="No abnormality detected", status=ScanReport.STATUS_FINAL,
        )
        self.client.force_login(self.sonographer)

    def test_finalized_report_screen_offers_a_send_to_billing_action(self):
        response = self.client.get(reverse("scan_report", args=[self.queue_entry.pk]))
        self.assertContains(response, "Send to Reception for Billing")

    def test_send_to_billing_routes_the_visit_to_reception(self):
        self.assertFalse(
            QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False).exists()
        )

        response = self.client.post(reverse("scan_report_send_to_billing", args=[self.report.pk]))

        self.assertRedirects(response, reverse("scan_queue"))
        self.assertTrue(
            QueueEntry.objects.filter(
                visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False,
                reason__icontains="Scan report finalized",
            ).exists()
        )

    def test_send_to_billing_is_safe_to_click_twice(self):
        self.client.post(reverse("scan_report_send_to_billing", args=[self.report.pk]))
        self.client.post(reverse("scan_report_send_to_billing", args=[self.report.pk]))

        self.assertEqual(
            QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False).count(),
            1,
        )
