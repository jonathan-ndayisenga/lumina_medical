from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog, Hospital, HospitalModuleSubscription, Module
from admin_dashboard.models import InventoryBatch, InventoryItem, InventoryTransaction
from doctor.models import Consultation, Prescription
from reception.models import Patient, QueueEntry, Service, Visit, VisitService


def _enable_modules(hospital, *codes):
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
        HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})


class DoctorWorkflowTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Central", subdomain="lumina-doc")
        _enable_modules(self.hospital, "doctor", "lab", "nurse")
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
            units_per_pack=Decimal("10"),
            strength_mg_per_unit=Decimal("500"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("5.00"),
            selling_price=Decimal("20.00"),
            reorder_level=Decimal("20"),
        )
        self.syrup_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Amoxicillin Syrup",
            category=InventoryItem.CATEGORY_SYRUP,
            unit="bottle",
            base_unit="ml",
            units_per_pack=Decimal("100"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("5000.00"),
            selling_price=Decimal("10.00"),
            reorder_level=Decimal("5"),
        )
        self.tube_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Clotrimazole Cream",
            category=InventoryItem.CATEGORY_TUBE,
            unit="tube",
            base_unit="g",
            units_per_pack=Decimal("30"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("3.00"),
            selling_price=Decimal("8.00"),
            reorder_level=Decimal("2"),
            days_covered_per_pack=Decimal("7.00"),
        )
        self.iv_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Normal Saline IV",
            category=InventoryItem.CATEGORY_IV_FLUID,
            unit="bag",
            base_unit="ml",
            units_per_pack=Decimal("500"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("3000.00"),
            selling_price=Decimal("6000.00"),
            reorder_level=Decimal("2"),
        )
        self.im_drug = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Diclofenac IM",
            category=InventoryItem.CATEGORY_IM,
            unit="vial",
            base_unit="ml",
            units_per_pack=Decimal("3"),
            current_quantity=Decimal("0"),
            unit_cost=Decimal("800.00"),
            selling_price=Decimal("1500.00"),
            reorder_level=Decimal("5"),
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
        self.assertContains(response, 'id="drug-search-results"', html=False)
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
        # A doctor ordering through their own consultation is recorded as
        # the requester automatically -- whoever is logged in, no popup.
        self.assertEqual(visit_service.requested_by_type, VisitService.REQUESTED_BY_INTERNAL_DOCTOR)
        self.assertEqual(visit_service.requested_by_user, self.doctor)
        self.assertEqual(visit_service.requested_by_display, f"Dr. {self.doctor.username}")
        # Lab requests go through reception for approval before routing to lab
        reception_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False)
        self.assertIn("Lab approval required", reception_queue.reason)

        payload = response.json()
        self.assertEqual(payload["service"]["service_name"], "CBC")
        self.assertEqual(payload["pending_services"][0]["service_name"], "CBC")

    def test_doctor_can_remove_a_mistaken_lab_request_immediately(self):
        self.client.post(
            reverse("send_lab_request_api", args=[self.visit.pk]),
            {"service_id": self.lab_service.pk},
        )
        visit_service = VisitService.objects.get(visit=self.visit, service=self.lab_service)

        response = self.client.post(
            reverse("remove_lab_service_api", args=[self.visit.pk, visit_service.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.total_amount, Decimal("25.00"))
        self.assertFalse(VisitService.objects.filter(pk=visit_service.pk).exists())

    def test_doctor_is_blocked_once_reception_has_approved_it_admin_still_can(self):
        """Same mistaken-request scenario, but discovered later -- reception
        has already approved it (acted on it, routed it toward the lab
        queue). A plain doctor can no longer self-remove it at that point --
        they get told to contact an admin instead. An admin/superadmin can
        still remove it through the same endpoint."""
        self.client.post(
            reverse("send_lab_request_api", args=[self.visit.pk]),
            {"service_id": self.lab_service.pk},
        )
        visit_service = VisitService.objects.get(visit=self.visit, service=self.lab_service)
        reception_entry = QueueEntry.objects.get(
            visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False,
        )

        receptionist = self.User.objects.create_user(
            username="approving_reception", password="StrongPass123!",
            role=self.User.ROLE_RECEPTIONIST, hospital=self.hospital,
        )
        self.client.force_login(receptionist)
        approve_response = self.client.post(reverse("reception_queue_approve_lab", args=[reception_entry.pk]))
        self.assertEqual(approve_response.status_code, 302)
        visit_service.refresh_from_db()
        self.assertTrue(visit_service.is_approved)

        self.client.force_login(self.doctor)
        blocked_response = self.client.post(
            reverse("remove_lab_service_api", args=[self.visit.pk, visit_service.pk]),
        )
        self.assertEqual(blocked_response.status_code, 403)
        self.assertIn("contact an admin", blocked_response.json()["error"])
        self.assertTrue(VisitService.objects.filter(pk=visit_service.pk).exists())

        admin_user = self.User.objects.create_user(
            username="lab_removal_admin", password="StrongPass123!",
            role=self.User.ROLE_HOSPITAL_ADMIN, hospital=self.hospital,
        )
        self.client.force_login(admin_user)
        admin_response = self.client.post(
            reverse("remove_lab_service_api", args=[self.visit.pk, visit_service.pk]),
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertFalse(VisitService.objects.filter(pk=visit_service.pk).exists())

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
        # Lab requests go through reception for approval before routing to lab
        reception_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False)
        self.assertIn("Lab approval required", reception_queue.reason)

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

    def test_undoing_a_dispensed_prescription_restores_stock_to_the_exact_original_batch(self):
        """Regression: undoing a dispensed prescription used to dump the
        restored quantity into a brand-new "REVERSAL-RX-<id>" batch with no
        expiry date, instead of crediting it back to whichever real batch(es)
        it was actually drawn from -- losing lot/expiry traceability every
        time a dispense was undone."""
        from datetime import date

        batch = self.tablet_drug.add_or_update_batch(
            "LOT-EXPIRES-2027", Decimal("100"), expiry_date=date(2027, 1, 1),
            unit_cost=Decimal("5.00"),
        )
        self.tablet_drug.refresh_from_db()

        receptionist = self.User.objects.create_user(
            username="dispense_reception", password="StrongPass123!",
            role=self.User.ROLE_RECEPTIONIST, hospital=self.hospital,
        )

        self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {"drug_id": self.tablet_drug.pk, "dosage_mg": "500", "frequency_per_day": "3", "duration_days": "5"},
        )
        prescription = Prescription.objects.get(visit=self.visit, drug=self.tablet_drug)

        self.client.force_login(receptionist)
        dispense_response = self.client.post(
            reverse("reception_dispense_prescription", args=[self.visit.pk, prescription.pk]),
        )
        self.assertEqual(dispense_response.status_code, 302)
        prescription.refresh_from_db()
        self.assertTrue(prescription.dispensed)

        consume_txn = InventoryTransaction.objects.get(
            prescription=prescription, transaction_type=InventoryTransaction.TYPE_CONSUME,
        )
        self.assertEqual(consume_txn.batch_breakdown, [
            {"batch_id": batch.pk, "batch_number": "LOT-EXPIRES-2027", "quantity": "1.50"},
        ])
        batch.refresh_from_db()
        self.assertEqual(batch.quantity, Decimal("98.50"))

        remove_response = self.client.post(
            reverse("remove_prescription_api", args=[self.visit.pk, prescription.pk]),
        )
        self.assertEqual(remove_response.status_code, 200)

        batch.refresh_from_db()
        self.assertEqual(batch.quantity, Decimal("100.00"))
        self.assertEqual(batch.expiry_date.isoformat(), "2027-01-01")
        self.assertFalse(
            InventoryBatch.objects.filter(item=self.tablet_drug, batch_number__startswith="REVERSAL-RX").exists()
        )

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

    def test_doctor_can_add_reagent_prescription_and_calculate_bottles(self):
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

        response = self.client.post(
            reverse("add_prescription_api", args=[self.visit.pk]),
            {
                "drug_id": reagent_drug.pk,
                "dosage_mg": "5",
                "frequency_per_day": "1",
                "duration_days": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        prescription = Prescription.objects.get(visit=self.visit, drug=reagent_drug)
        self.assertEqual(prescription.total_quantity, Decimal("2.00"))
        self.assertEqual(prescription.number_of_packs, 2)
        self.assertEqual(prescription.total_price, Decimal("3000.00"))
        self.assertEqual(response.json()["prescription"]["quantity_display"], "2 bottle(s) covering 10 ml")

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
        self.assertEqual(self.visit.status, Visit.STATUS_IN_PROGRESS)
        self.assertFalse(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False).exists())
        self.assertTrue(QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False).exists())

    def test_adjustment_visit_prescription_is_covered_without_new_billing(self):
        original_prescription = Prescription.objects.create(
            visit=self.visit,
            drug=self.tablet_drug,
            dosage_mg=Decimal("500"),
            frequency_per_day=2,
            duration_days=5,
            prescribed_by=self.doctor,
        )
        adjustment_visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            visit_type=Visit.TYPE_ADJUSTMENT,
            parent_visit=self.visit,
            adjustment_origin_prescription=original_prescription,
            adjustment_days_used=3,
            adjustment_remaining_days=2,
            adjustment_reason="Side effects",
            created_by=self.doctor,
            total_amount=Decimal("0.00"),
        )

        response = self.client.post(
            reverse("add_prescription_api", args=[adjustment_visit.pk]),
            {
                "drug_id": self.syrup_drug.pk,
                "dosage_mg": "10",
                "frequency_per_day": "2",
                "duration_days": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        adjustment_visit.refresh_from_db()
        original_prescription.refresh_from_db()
        replacement = Prescription.objects.get(visit=adjustment_visit, drug=self.syrup_drug)
        self.assertTrue(replacement.is_adjustment)
        self.assertTrue(replacement.covered_by_previous)
        self.assertEqual(replacement.parent_prescription, original_prescription)
        self.assertEqual(replacement.remaining_days_covered, 2)
        self.assertIsNone(replacement.billing_visit_service)
        self.assertEqual(adjustment_visit.total_amount, Decimal("0.00"))
        self.assertTrue(original_prescription.discontinued_early)
        self.assertIn("No new billing was added", response.json()["message"])

    def test_adjustment_visit_consultation_page_shows_swap_summary(self):
        original_prescription = Prescription.objects.create(
            visit=self.visit,
            drug=self.tablet_drug,
            dosage_mg=Decimal("500"),
            frequency_per_day=2,
            duration_days=5,
            prescribed_by=self.doctor,
        )
        adjustment_visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            visit_type=Visit.TYPE_ADJUSTMENT,
            parent_visit=self.visit,
            adjustment_origin_prescription=original_prescription,
            adjustment_days_used=3,
            adjustment_remaining_days=2,
            adjustment_reason="Ineffective response",
            created_by=self.doctor,
        )

        response = self.client.get(reverse("consultation", args=[adjustment_visit.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Medication Adjustment Visit")
        self.assertContains(response, "Remaining days")
        self.assertContains(response, "Ineffective response")


class ConsultationAdminOverrideTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Admin Doctor", subdomain="lumina-admin-doctor")
        _enable_modules(self.hospital, "hospital_mgmt")
        self.doctor = self.User.objects.create_user(
            username="doctor2",
            password="StrongPass123!",
            role=self.User.ROLE_DOCTOR,
            hospital=self.hospital,
        )
        self.admin_user = self.User.objects.create_user(
            username="doctoradmin",
            password="StrongPass123!",
            role=self.User.ROLE_HOSPITAL_ADMIN,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Consult Delete",
            age="29YRS",
            sex="F",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            created_by=self.doctor,
            total_amount="25.00",
        )
        self.consultation = Consultation.objects.create(
            visit=self.visit,
            created_by=self.doctor,
            signs_symptoms="Headache",
            diagnosis="Migraine",
            treatment="Rest",
        )

    def test_hospital_admin_can_delete_consultation(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("consultation_delete", args=[self.visit.pk]),
            {
                "admin_reason": "Saved on the wrong visit.",
                "next": reverse("doctor_queue"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Consultation.objects.filter(pk=self.consultation.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="delete_consultation",
                model_name="Consultation",
                object_id=str(self.consultation.pk),
            ).exists()
        )

    def test_doctor_cannot_delete_consultation(self):
        self.client.force_login(self.doctor)

        response = self.client.get(reverse("consultation_delete", args=[self.visit.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Consultation.objects.filter(pk=self.consultation.pk).exists())


class SonographerReceptionApprovalTests(TestCase):
    """A doctor referring a patient for a scan must route through reception
    for approval first -- the same gate lab requests already get -- instead
    of landing straight in the live sonographer queue unreviewed."""

    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Sonographer Gate Hospital", subdomain="sono-gate")
        _enable_modules(self.hospital, "doctor", "reception", "sonographer")
        self.doctor = self.User.objects.create_user(
            username="sono_doctor", password="StrongPass123!", role=self.User.ROLE_DOCTOR, hospital=self.hospital,
        )
        self.receptionist = self.User.objects.create_user(
            username="sono_reception", password="StrongPass123!", role=self.User.ROLE_RECEPTIONIST, hospital=self.hospital,
        )
        self.patient = Patient.objects.create(hospital=self.hospital, name="Scan Patient", age="40YRS", sex="F")
        self.consult_service = Service.objects.create(
            hospital=self.hospital, name="Consultation", category=Service.CATEGORY_CONSULTATION, price="25.00",
        )
        self.scan_service = Service.objects.create(
            hospital=self.hospital, name="Abdominal Ultrasound", category=Service.CATEGORY_SCAN, price="30.00",
        )
        self.visit = Visit.objects.create(
            patient=self.patient, hospital=self.hospital, created_by=self.doctor, total_amount="25.00",
        )
        VisitService.objects.create(visit=self.visit, service=self.consult_service, price_at_time="25.00")
        QueueEntry.objects.create(
            hospital=self.hospital, visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR,
            reason="Initial consultation", requested_by=self.doctor,
        )

    def test_doctor_referral_lands_in_reception_not_sonographer_queue_directly(self):
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("consultation", args=[self.visit.pk]),
            {
                "weight_kg": "60.0", "bp_systolic": "120", "bp_diastolic": "80", "pulse": "78",
                "respiratory_rate": "18", "temperature_celsius": "36.7", "glucose_mg_dl": "98",
                "oxygen_saturation": "99", "signs_symptoms": "Abdominal pain", "diagnosis": "Rule out gallstones",
                "treatment": "Ultrasound requested", "follow_up_date": "", "send_to_sonographer": "on",
            },
        )
        self.assertEqual(response.status_code, 302)

        scan_vs = VisitService.objects.get(visit=self.visit, service=self.scan_service)
        self.assertFalse(scan_vs.is_approved, "a doctor-referred scan must start unapproved, awaiting reception")

        self.assertFalse(
            QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_SONOGRAPHER).exists(),
            "the sonographer queue must not be touched until reception approves",
        )
        reception_entry = QueueEntry.objects.filter(
            visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False,
        ).first()
        self.assertIsNotNone(reception_entry, "the visit must land in reception's queue for approval")

    def test_reception_approve_scan_then_routes_to_sonographer_queue(self):
        self.client.force_login(self.doctor)
        self.client.post(
            reverse("consultation", args=[self.visit.pk]),
            {
                "weight_kg": "60.0", "bp_systolic": "120", "bp_diastolic": "80", "pulse": "78",
                "respiratory_rate": "18", "temperature_celsius": "36.7", "glucose_mg_dl": "98",
                "oxygen_saturation": "99", "signs_symptoms": "Abdominal pain", "diagnosis": "Rule out gallstones",
                "treatment": "Ultrasound requested", "follow_up_date": "", "send_to_sonographer": "on",
            },
        )
        reception_entry = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False)

        self.client.logout()
        self.client.force_login(self.receptionist)
        response = self.client.post(reverse("reception_queue_approve_scan", args=[reception_entry.pk]))
        self.assertEqual(response.status_code, 302)

        scan_vs = VisitService.objects.get(visit=self.visit, service=self.scan_service)
        self.assertTrue(scan_vs.is_approved)
        self.assertTrue(
            QueueEntry.objects.filter(visit=self.visit, queue_type=QueueEntry.TYPE_SONOGRAPHER, processed=False).exists(),
            "approving must now route the patient to the sonographer queue",
        )
        reception_entry.refresh_from_db()
        self.assertTrue(reception_entry.processed)
