from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth.models import Group

from accounts.models import Hospital, HospitalModuleSubscription, Module, User
from admin_dashboard.models import InventoryBatch, InventoryItem, InventoryTransaction
from doctor.models import Prescription
from pharmacy import services
from reception.models import Patient, Payment, Visit, VisitService


def _enable(hospital, *codes):
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
        HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})


class PharmacyCase(TestCase):
    def setUp(self):
        self.hospital = Hospital.objects.create(name="Pharmacy Clinic", subdomain="pharmacy-clinic")
        _enable(self.hospital, "doctor", "nurse", "reception", "inventory", "finance")
        self.admin = User.objects.create_user(username="ph-admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=self.hospital, is_active=True)
        self.doctor = User.objects.create_user(username="ph-doctor", password="x", role=User.ROLE_DOCTOR, hospital=self.hospital, is_active=True)
        self.receptionist = User.objects.create_user(username="ph-rec", password="x", role=User.ROLE_RECEPTIONIST, hospital=self.hospital, is_active=True)
        self.nurse = User.objects.create_user(username="ph-nurse", password="x", role=User.ROLE_NURSE, hospital=self.hospital, is_active=True)
        self.pharmacist = User.objects.create_user(username="ph-pharm", password="x", role=User.ROLE_NURSE, hospital=self.hospital, is_active=True)
        self.pharmacist.groups.add(Group.objects.get_or_create(name="Inventory")[0])
        self.drug = InventoryItem.objects.create(
            hospital=self.hospital, name="Paracetamol", category=InventoryItem.CATEGORY_DRUG, unit="strip", base_unit="tablet",
            units_per_pack=Decimal("10"), current_quantity=Decimal("0"), unit_cost=Decimal("500"), selling_price=Decimal("2000"),
            reorder_level=Decimal("2"), strength_mg_per_unit=Decimal("500"),
        )
        InventoryBatch.objects.create(item=self.drug, batch_number="LATE", quantity="5", expiry_date="2029-01-31", unit_cost="500")
        InventoryBatch.objects.create(item=self.drug, batch_number="SOON", quantity="5", expiry_date="2027-01-31", unit_cost="500")
        self.drug.recalculate_current_quantity()
        self.syrup = InventoryItem.objects.create(
            hospital=self.hospital, name="Cough Syrup", category=InventoryItem.CATEGORY_SYRUP, unit="bottle", base_unit="ml",
            units_per_pack=Decimal("100"), current_quantity=Decimal("0"), unit_cost=Decimal("3000"), selling_price=Decimal("6000"),
        )
        InventoryBatch.objects.create(item=self.syrup, batch_number="SYR", quantity="4", expiry_date="2028-06-30", unit_cost="3000")
        self.syrup.recalculate_current_quantity()
        patient = Patient.objects.create(hospital=self.hospital, name="Rx Patient", age="30YRS", sex="F")
        self.visit = Visit.objects.create(patient=patient, hospital=self.hospital, created_by=self.receptionist, total_amount=Decimal("0"))

    def prescribe(self, user=None, drug=None, dose="500", per_day="3", days="2"):
        self.client.force_login(user or self.doctor)
        response = self.client.post(reverse("add_prescription_api", args=[self.visit.pk]), {
            "drug_id": (drug or self.drug).pk, "dosage_mg": dose, "frequency_per_day": per_day, "duration_days": days,
        })
        self.assertEqual(response.status_code, 200, response.content)
        return Prescription.objects.latest("pk")

    def pharmacy_mode(self):
        self.hospital.dispensing_mode = Hospital.DISPENSING_PHARMACY
        self.hospital.pharmacy_mode_since = timezone.now() - timezone.timedelta(minutes=5)
        self.hospital.save()


class DispenseTests(PharmacyCase):
    def test_dispensing_takes_stock_once_from_the_batch_expiring_first(self):
        rx = self.prescribe()
        self.assertEqual(rx.total_quantity, Decimal("6.00"))
        rx, done = services.dispense(rx, self.nurse)
        self.assertTrue(done)
        rx2, done_again = services.dispense(rx, self.nurse)
        self.assertFalse(done_again, "a second press does nothing")
        self.assertEqual(InventoryTransaction.objects.filter(prescription=rx, transaction_type="consume").count(), 1)
        soon = InventoryBatch.objects.get(batch_number="SOON")
        self.assertEqual(soon.quantity, Decimal("4.40"))
        self.assertEqual(InventoryBatch.objects.get(batch_number="LATE").quantity, Decimal("5.00"))

    def test_records_what_was_paid_when_medicine_went_out_and_allows_part_payment(self):
        rx = self.prescribe()
        self.visit.refresh_from_db()
        Payment.objects.create(visit=self.visit, amount=self.visit.total_amount, amount_paid=Decimal("600"), mode=Payment.MODE_CASH)
        rx, done = services.dispense(rx, self.nurse)
        self.assertTrue(done)
        self.assertEqual(rx.paid_at_dispense, Decimal("600"))
        self.assertEqual(rx.bill_at_dispense, Decimal("1200"))

    def test_the_dispense_lock_is_valid_on_postgresql(self):
        """Tests run on SQLite, which ignores FOR UPDATE, so build the query the
        way production's PostgreSQL would. Locking the outer-joined billing line
        was refused there ("FOR UPDATE cannot be applied to the nullable side of
        an outer join") and dispensing crashed."""
        from unittest import mock
        from django.db.backends.postgresql.base import DatabaseWrapper
        from django.db.models.query import QuerySet

        pg = DatabaseWrapper({
            "ENGINE": "django.db.backends.postgresql", "NAME": "x", "USER": "", "PASSWORD": "", "HOST": "", "PORT": "",
            "OPTIONS": {}, "TIME_ZONE": None, "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": False, "AUTOCOMMIT": True,
            "ATOMIC_REQUESTS": False, "TEST": {},
        })
        pg.__dict__["pg_version"] = 160000  # No connection is ever opened.
        pg.get_autocommit = lambda: False  # As inside dispense's transaction.
        seen = []
        real_get = QuerySet.get

        def record(qs, *args, **kwargs):
            if qs.model is Prescription and qs.query.select_for_update:
                sql, _ = qs.filter(*args, **kwargs).query.get_compiler(connection=pg).as_sql()
                seen.append(sql)
            return real_get(qs, *args, **kwargs)

        with mock.patch.object(QuerySet, "get", record):
            services.dispense(self.prescribe(), self.nurse)
        self.assertTrue(seen, "dispense locks the prescription")
        for sql in seen:
            self.assertIn("LEFT OUTER JOIN", sql)
            self.assertRegex(sql, r'FOR UPDATE OF "doctor_prescription"$')

    def test_not_enough_stock_is_refused_and_nothing_changes(self):
        rx = self.prescribe(per_day="10", days="20")
        with self.assertRaisesMessage(Exception, "Not enough Paracetamol"):
            services.dispense(rx, self.nurse)
        rx.refresh_from_db()
        self.assertFalse(rx.dispensed)
        self.assertEqual(InventoryBatch.objects.get(batch_number="SOON").quantity, Decimal("5.00"))

    def test_undo_puts_stock_back_in_the_same_batch(self):
        from doctor.views import remove_prescription_workflow
        rx = self.prescribe()
        services.dispense(rx, self.nurse)
        remove_prescription_workflow(prescription=Prescription.objects.get(pk=rx.pk), actor=self.admin)
        self.assertEqual(InventoryBatch.objects.get(batch_number="SOON").quantity, Decimal("5.00"))


class ModeTests(PharmacyCase):
    def test_standard_mode_keeps_nurse_and_reception_dispensing(self):
        rx = self.prescribe()
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_dispense_prescription", args=[self.visit.pk, rx.pk]))
        rx.refresh_from_db()
        self.assertTrue(rx.dispensed)

    def test_pharmacy_mode_sends_medicine_to_the_pharmacy_queue(self):
        self.pharmacy_mode()
        rx = self.prescribe()
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_dispense_prescription", args=[self.visit.pk, rx.pk]))
        rx.refresh_from_db()
        self.assertFalse(rx.dispensed, "reception can't hand it over in pharmacy mode")
        page = self.client.get(reverse("complete_visit", args=[self.visit.pk]))
        self.assertContains(page, "Waiting at pharmacy")

        self.assertEqual(list(services.pharmacy_queue(self.hospital)), [self.visit])
        self.client.force_login(self.pharmacist)
        self.assertContains(self.client.get(reverse("pharmacy_queue")), "Rx Patient")
        self.client.post(reverse("pharmacy_visit", args=[self.visit.pk]), {"action": "dispense", "prescription": rx.pk})
        rx.refresh_from_db()
        self.assertTrue(rx.dispensed)
        self.assertEqual(rx.dispensed_by, self.pharmacist)
        self.assertEqual(list(services.pharmacy_queue(self.hospital)), [])

    def test_drugs_reception_adds_at_billing_land_in_the_pharmacy_queue(self):
        self.pharmacy_mode()
        rx = self.prescribe(user=self.receptionist)
        self.assertEqual(rx.added_from, Prescription.ADDED_FROM_RECEPTION)
        self.assertIn(self.visit, services.pharmacy_queue(self.hospital))

    def test_queue_ignores_old_history_from_before_pharmacy_mode(self):
        rx = self.prescribe()
        Prescription.objects.filter(pk=rx.pk).update(prescribed_at=timezone.now() - timezone.timedelta(days=3))
        self.pharmacy_mode()
        self.assertEqual(list(services.pharmacy_queue(self.hospital)), [])

    def test_only_admins_switch_the_mode(self):
        self.client.force_login(self.pharmacist)
        self.assertEqual(self.client.post(reverse("pharmacy_set_mode"), {"mode": "pharmacy"}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("pharmacy_set_mode"), {"mode": "pharmacy"})
        self.hospital.refresh_from_db()
        self.assertEqual(self.hospital.dispensing_mode, Hospital.DISPENSING_PHARMACY)
        self.assertIsNotNone(self.hospital.pharmacy_mode_since)


class QuantitySaleTests(PharmacyCase):
    def test_walk_in_sale_by_packs_and_units_is_billed_and_handed_over(self):
        self.client.force_login(self.pharmacist)
        response = self.client.post(reverse("pharmacy_walk_in"), {"client_type": "walk_in"})
        sale = Visit.objects.exclude(pk=self.visit.pk).get()
        self.assertRedirects(response, reverse("pharmacy_visit", args=[sale.pk]), fetch_redirect_response=False)
        url = reverse("pharmacy_visit", args=[sale.pk])
        self.client.post(url, {"action": "add", "drug": self.drug.pk, "quantity": "2", "counted_in": "pack"})
        self.client.post(url, {"action": "add", "drug": self.syrup.pk, "quantity": "150", "counted_in": "unit"})
        tablets, syrup = Prescription.objects.filter(visit=sale).order_by("pk")
        self.assertEqual((tablets.total_quantity, tablets.total_price), (Decimal("20.00"), Decimal("4000.00")))
        self.assertEqual((syrup.number_of_packs, syrup.total_price), (2, Decimal("12000.00")), "150 ml rounds up to 2 bottles")
        self.assertFalse(syrup.nursing_managed)
        self.assertEqual(tablets.regimen_display, "Sold by quantity")
        self.assertEqual(tablets.quantity_display, "20 tablet(s)")
        self.assertEqual(syrup.quantity_display, "2 bottle(s)")
        sale.refresh_from_db()
        self.assertEqual(sale.total_amount, Decimal("16000.00"))
        self.assertEqual(VisitService.objects.filter(visit=sale, service__category="pharmacy").count(), 2)
        for rx in (tablets, syrup):
            self.client.post(url, {"action": "dispense", "prescription": rx.pk})
        self.assertEqual(Prescription.objects.filter(visit=sale, dispensed=True).count(), 2)
        self.drug.refresh_from_db()
        self.assertEqual(self.drug.current_quantity, Decimal("8.00"))

    def test_bad_quantities_and_other_hospitals_drugs_are_refused(self):
        other = Hospital.objects.create(name="Other", subdomain="other-ph")
        foreign = InventoryItem.objects.create(hospital=other, name="Foreign", category=InventoryItem.CATEGORY_DRUG, unit="strip", base_unit="tablet", units_per_pack=Decimal("10"))
        self.client.force_login(self.pharmacist)
        url = reverse("pharmacy_visit", args=[self.visit.pk])
        self.client.post(url, {"action": "add", "drug": self.drug.pk, "quantity": "0", "counted_in": "unit"})
        self.assertEqual(self.client.post(url, {"action": "add", "drug": foreign.pk, "quantity": "1"}).status_code, 404)
        self.assertFalse(Prescription.objects.exists())

    def test_handed_over_medicine_cannot_be_removed_from_the_pharmacy(self):
        rx = self.prescribe()
        services.dispense(rx, self.nurse)
        self.client.force_login(self.pharmacist)
        self.client.post(reverse("pharmacy_visit", args=[self.visit.pk]), {"action": "remove", "prescription": rx.pk})
        self.assertTrue(Prescription.objects.filter(pk=rx.pk).exists())


class AccessTests(PharmacyCase):
    def test_pharmacy_is_for_module_users_and_only_admins_set_prices(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.client.get(reverse("pharmacy_queue")).status_code, 403)
        self.client.force_login(self.pharmacist)
        self.assertEqual(self.client.get(reverse("pharmacy_queue")).status_code, 200)
        self.client.post(reverse("edit_inventory_item", args=[self.drug.pk]), {
            "name": "Paracetamol", "category": "drug", "unit": "strip", "base_unit": "tablet", "units_per_pack": "10",
            "current_quantity": "10", "unit_cost": "500", "selling_price": "1", "reorder_level": "2", "strength_mg_per_unit": "500",
            "is_active": "on",
        })
        self.drug.refresh_from_db()
        self.assertEqual(self.drug.selling_price, Decimal("2000.00"), "a pharmacist cannot change the price")
        self.assertEqual(self.client.post(reverse("delete_inventory_item", args=[self.drug.pk])).status_code, 403)

    def test_another_hospitals_visit_is_not_found(self):
        other = Hospital.objects.create(name="Other", subdomain="other-ph2")
        stranger = Visit.objects.create(patient=Patient.objects.create(hospital=other, name="Stranger", age="20YRS", sex="M"), hospital=other, total_amount=0)
        self.client.force_login(self.pharmacist)
        self.assertEqual(self.client.get(reverse("pharmacy_visit", args=[stranger.pk])).status_code, 404)


class ReviewFixTests(PharmacyCase):
    def test_a_drug_without_a_price_is_not_given_away(self):
        free = InventoryItem.objects.create(hospital=self.hospital, name="Unpriced", category=InventoryItem.CATEGORY_DRUG, unit="strip", base_unit="tablet", units_per_pack=Decimal("10"))
        self.client.force_login(self.pharmacist)
        response = self.client.post(reverse("pharmacy_visit", args=[self.visit.pk]), {"action": "add", "drug": free.pk, "quantity": "1"}, follow=True)
        self.assertContains(response, "no selling price yet")
        self.assertFalse(Prescription.objects.exists())

    def test_adding_to_a_finished_visit_sends_it_back_for_payment(self):
        Visit.objects.filter(pk=self.visit.pk).update(status=Visit.STATUS_COMPLETED)
        self.visit.refresh_from_db()
        services.add_by_quantity(self.visit, self.drug, 1, "pack", self.pharmacist)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.STATUS_READY_FOR_BILLING)
