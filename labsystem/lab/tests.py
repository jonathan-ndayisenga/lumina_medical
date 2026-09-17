from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import AuditLog, Hospital, HospitalModuleSubscription, Module
from lab.models import (
    LabOrder,
    LabReport,
    LabSettings,
    LabTest,
    OrderStage,
    Parameter,
    ParameterRange,
    ResultType,
    Sex,
    ServiceCategory,
    SpecimenType,
    TestCatalog,
    TestResult,
    ValueType,
)
from lab.templatetags.lab_extras import range_flag
from lab.views import report_needs_doctor_send, send_report_results_to_doctor
from reception.models import Patient, QueueEntry, Service, Visit, VisitService


def _enable_modules(hospital, *codes):
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
        HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})


class LabDoctorHandoffTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Lab", subdomain="lumina-lab-handoff")
        _enable_modules(self.hospital, "doctor", "lab", "nurse")
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
        self.reception_lab_queue = QueueEntry.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            queue_type=QueueEntry.TYPE_LAB_RECEPTION,
            reason="Reception sent patient to lab",
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
        self.reception_lab_queue.refresh_from_db()
        self.assertTrue(self.report.sent_to_doctor)
        self.assertTrue(self.lab_queue.processed)
        self.assertTrue(self.reception_lab_queue.processed)
        doctor_queue = QueueEntry.objects.get(visit=self.visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False)
        self.assertIn("Lab results ready for review", doctor_queue.reason)
        self.assertEqual(doctor_queue.requested_by, self.doctor)
        self.assertFalse(
            QueueEntry.objects.filter(
                visit=self.visit,
                queue_type__in=[QueueEntry.TYPE_LAB_DOCTOR, QueueEntry.TYPE_LAB_RECEPTION],
                processed=False,
            ).exists()
        )


class LabWorkflowPolishTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Polish", subdomain="lumina-polish")
        self.lab_user = self.User.objects.create_user(
            username="labpolish",
            password="StrongPass123!",
            role=self.User.ROLE_LAB_ATTENDANT,
            hospital=self.hospital,
        )
        self.reception_user = self.User.objects.create_user(
            username="receptionpolish",
            password="StrongPass123!",
            role=self.User.ROLE_RECEPTIONIST,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Routing Patient",
            age="32YRS",
            sex="F",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            created_by=self.reception_user,
            total_amount="15.00",
        )
        self.service = Service.objects.create(
            hospital=self.hospital,
            name="CBC Direct",
            category=Service.CATEGORY_LAB,
            price="15.00",
        )
        self.visit_service = VisitService.objects.create(
            visit=self.visit,
            service=self.service,
            price_at_time="15.00",
        )
        self.report = LabReport.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            requested_visit_service=self.visit_service,
            patient_name=self.patient.name,
            patient_age=self.patient.age,
            patient_sex=self.patient.sex,
            sample_date=date.today(),
            specimen_type="BLOOD",
            attendant=self.lab_user,
            attendant_name="Lab Polish",
        )
        self.test_catalog, _ = TestCatalog.objects.get_or_create(name="HGB", defaults={"unit": "g/dL"})
        TestResult.objects.create(
            lab_report=self.report,
            test=self.test_catalog,
            result_value="12.5",
            reference_range="11.0-15.0",
            unit="g/dL",
        )
        self.lab_queue = QueueEntry.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            queue_type=QueueEntry.TYPE_LAB_RECEPTION,
            reason="Reception sent patient to lab",
        )
        self.client.force_login(self.lab_user)

    def test_direct_lab_report_can_be_routed_to_reception_and_queue_clears(self):
        response = self.client.post(
            reverse("route_lab_report", args=[self.report.pk]),
            {"destination": "reception"},
        )
        self.assertRedirects(response, reverse("report_detail", args=[self.report.pk]))
        self.visit.refresh_from_db()
        self.lab_queue.refresh_from_db()
        self.assertEqual(self.visit.status, Visit.STATUS_IN_PROGRESS)
        self.assertTrue(self.lab_queue.processed)
        self.assertTrue(
            QueueEntry.objects.filter(
                visit=self.visit,
                queue_type=QueueEntry.TYPE_RECEPTION,
                processed=False,
                reason__icontains="Returned from Lab",
            ).exists()
        )
        self.assertFalse(
            QueueEntry.objects.filter(
                visit=self.visit,
                queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
                processed=False,
            ).exists()
        )

class LabRangeFlagTests(TestCase):
    def test_range_flag_marks_high_low_and_normal(self):
        self.assertEqual(range_flag("16.2", "11.0-15.0"), "HIGH")
        self.assertEqual(range_flag("10.4", "11.0-15.0"), "LOW")
        self.assertEqual(range_flag("13.3", "11.0-15.0"), "NORMAL")
        self.assertEqual(range_flag("Positive", "11.0-15.0"), "")


class LabReportAdminOverrideTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Admin Lab", subdomain="lumina-admin-lab")
        _enable_modules(self.hospital, "hospital_mgmt")
        self.lab_user = self.User.objects.create_user(
            username="labdelete",
            password="StrongPass123!",
            role=self.User.ROLE_LAB_ATTENDANT,
            hospital=self.hospital,
        )
        self.admin_user = self.User.objects.create_user(
            username="labadmin",
            password="StrongPass123!",
            role=self.User.ROLE_HOSPITAL_ADMIN,
            hospital=self.hospital,
        )
        self.patient = Patient.objects.create(
            hospital=self.hospital,
            name="Report Delete",
            age="35YRS",
            sex="M",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            hospital=self.hospital,
            created_by=self.lab_user,
            total_amount="15.00",
        )
        self.service = Service.objects.create(
            hospital=self.hospital,
            name="CBC Admin",
            category=Service.CATEGORY_LAB,
            price="15.00",
        )
        self.visit_service = VisitService.objects.create(
            visit=self.visit,
            service=self.service,
            price_at_time="15.00",
            performed=True,
        )
        self.report = LabReport.objects.create(
            hospital=self.hospital,
            visit=self.visit,
            requested_visit_service=self.visit_service,
            patient_name=self.patient.name,
            patient_age=self.patient.age,
            patient_sex=self.patient.sex,
            sample_date=date.today(),
            specimen_type="BLOOD",
            attendant=self.lab_user,
            attendant_name="Lab Delete",
        )

    def test_hospital_admin_can_delete_lab_report_and_reopen_service(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("report_delete", args=[self.report.pk]),
            {
                "admin_reason": "Attached to the wrong patient.",
                "next": reverse("report_list"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(LabReport.objects.filter(pk=self.report.pk).exists())
        self.visit_service.refresh_from_db()
        self.assertFalse(self.visit_service.performed)
        self.assertTrue(
            AuditLog.objects.filter(
                action="delete_lab_report",
                model_name="LabReport",
                object_id=str(self.report.pk),
            ).exists()
        )

    def test_non_admin_cannot_delete_lab_report(self):
        self.client.force_login(self.lab_user)

        response = self.client.get(reverse("report_delete", args=[self.report.pk]))

        self.assertEqual(response.status_code, 403)


# ===========================================================================
# New engine (LabTest/LabOrder/LabResult/ResultValue) — order -> pick sample
# -> enter results -> review -> release, end to end through the real views.
# ===========================================================================

class LabEngineTestBase(TestCase):
    """Shared catalog: one PARAMETER_PANEL test exercising all five
    ValueTypes plus a sex-specific multi-range Hemoglobin parameter, so a
    single walk through the workflow proves range resolution, flagging, and
    the full order->result->release pipeline together."""

    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Lumina Engine", subdomain="lumina-engine")
        _enable_modules(self.hospital, "lab", "doctor")
        self.lab_settings = LabSettings.objects.create(
            hospital=self.hospital,
            require_review_before_release=False,
            payment_required_before_release=False,
        )
        self.lab_user = self.User.objects.create_user(
            username="engine_lab",
            password="StrongPass123!",
            role=self.User.ROLE_LAB_ATTENDANT,
            hospital=self.hospital,
        )

        category = ServiceCategory.objects.create(name="Engine Chemistry")
        self.specimen = SpecimenType.objects.create(name="Engine Blood")
        self.test = LabTest.objects.create(
            hospital=self.hospital, name="Full Panel", category=category, result_type=ResultType.PARAMETER_PANEL,
        )
        self.test.accepted_specimens.add(self.specimen)

        self.p_hgb = Parameter.objects.create(
            test=self.test, name="Hemoglobin", unit="g/dL", value_type=ValueType.NUMERIC,
        )
        ParameterRange.objects.create(parameter=self.p_hgb, sex=Sex.MALE, ref_low="13", ref_high="17")
        ParameterRange.objects.create(parameter=self.p_hgb, sex=Sex.FEMALE, ref_low="12", ref_high="15")

        self.p_abx = Parameter.objects.create(
            test=self.test, name="Ampicillin", value_type=ValueType.CODED, group_label="Penicillins",
        )
        self.p_growth = Parameter.objects.create(
            test=self.test, name="Bacterial Growth", value_type=ValueType.GROWTH,
        )
        self.p_protein = Parameter.objects.create(
            test=self.test, name="Protein", value_type=ValueType.LEVEL,
        )
        self.p_appearance = Parameter.objects.create(
            test=self.test, name="Appearance", value_type=ValueType.TEXT,
        )

        self.service = Service.objects.create(
            hospital=self.hospital, name="Full Panel", category=Service.CATEGORY_LAB,
            price=Decimal("10.00"), lab_test_next=self.test,
        )
        self.client.force_login(self.lab_user)

    def _make_visit(self, sex, age_str):
        patient = Patient.objects.create(
            hospital=self.hospital, name=f"Patient {sex} {age_str}", age=age_str, sex=sex,
        )
        visit = Visit.objects.create(
            patient=patient, hospital=self.hospital, created_by=self.lab_user, total_amount=Decimal("10.00"),
        )
        VisitService.objects.create(visit=visit, service=self.service, price_at_time=Decimal("10.00"))
        QueueEntry.objects.create(
            hospital=self.hospital, visit=visit,
            queue_type=QueueEntry.TYPE_LAB_RECEPTION, reason="Reception sent patient to lab",
        )
        return visit

    def _order_for(self, visit):
        # Hitting the real queue view is what actually creates the LabOrder
        # (via _ensure_orders_for_visit) — exercising that, not calling the
        # helper directly, is the point of a workflow smoke test.
        response = self.client.get(reverse("queue"))
        self.assertEqual(response.status_code, 200)
        return LabOrder.objects.get(visit_service__visit=visit)

    def _collect_sample(self, order):
        response = self.client.post(
            reverse("pick_sample", args=[order.pk]),
            {"specimen": self.specimen.pk, "collection_notes": ""},
        )
        self.assertRedirects(response, reverse("enter_result", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.SAMPLE_COLLECTED)

    def _enter_results(self, order, *, hgb, abx, growth, protein, appearance):
        response = self.client.post(
            reverse("enter_result", args=[order.pk]),
            {
                f"param_{self.p_hgb.pk}": hgb,
                f"param_{self.p_abx.pk}": abx,
                f"param_{self.p_growth.pk}": growth,
                f"param_{self.p_protein.pk}": protein,
                f"param_{self.p_appearance.pk}": appearance,
            },
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.ENTERED)


class LabEngineOrderToReleaseTests(LabEngineTestBase):
    def test_cannot_enter_results_before_sample_collected(self):
        visit = self._make_visit("M", "30YRS")
        order = self._order_for(visit)
        response = self.client.get(reverse("enter_result", args=[order.pk]), follow=True)
        self.assertRedirects(response, reverse("pick_sample", args=[order.pk]))

    def test_male_and_female_resolve_different_hemoglobin_ranges_and_flag_correctly(self):
        # Same numeric value (12.5): below the male range (13-17) -> LOW,
        # inside the female range (12-15) -> normal. Proves resolve_range()
        # actually picks the sex-specific ParameterRange, not just "a" range.
        male_visit = self._make_visit("M", "30YRS")
        male_order = self._order_for(male_visit)
        self._collect_sample(male_order)
        self._enter_results(
            male_order, hgb="12.5", abx="R", growth="Growth Observed",
            protein="3+", appearance="Cloudy",
        )

        female_visit = self._make_visit("F", "28YRS")
        female_order = self._order_for(female_visit)
        self._collect_sample(female_order)
        self._enter_results(
            female_order, hgb="12.5", abx="S", growth="No Significant Growth",
            protein="Negative", appearance="Clear",
        )

        male_values = {v.label: v for v in male_order.result.values.all()}
        female_values = {v.label: v for v in female_order.result.values.all()}

        # Numeric, sex-specific range resolution + flagging.
        self.assertEqual(male_values["Hemoglobin"].flag, "L")
        self.assertEqual(male_values["Hemoglobin"].ref_display, "13 - 17")
        self.assertEqual(female_values["Hemoglobin"].flag, "N")
        self.assertEqual(female_values["Hemoglobin"].ref_display, "12 - 15")

        # CODED: only Resistant is abnormal.
        self.assertEqual(male_values["Ampicillin"].flag, "A")
        self.assertEqual(female_values["Ampicillin"].flag, "")

        # GROWTH: this is the bug that was silently unflagged before the fix.
        self.assertEqual(male_values["Bacterial Growth"].flag, "A")
        self.assertEqual(female_values["Bacterial Growth"].flag, "")

        # LEVEL: anything above Negative is abnormal — also previously silent.
        self.assertEqual(male_values["Protein"].flag, "A")
        self.assertEqual(female_values["Protein"].flag, "")

        # TEXT never gets an auto flag.
        self.assertEqual(male_values["Appearance"].flag, "")
        self.assertEqual(female_values["Appearance"].flag, "")

    def test_release_freezes_audit_trail_and_flags_render_on_the_report(self):
        visit = self._make_visit("M", "40YRS")
        order = self._order_for(visit)
        self._collect_sample(order)
        self._enter_results(
            order, hgb="20", abx="R", growth="Growth Observed",
            protein="4+", appearance="Turbid",
        )

        response = self.client.post(
            reverse("visit_report", args=[visit.pk]), {"action": "release"},
        )
        self.assertRedirects(response, reverse("visit_report", args=[visit.pk]))
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.RELEASED)
        result = order.result
        result.refresh_from_db()
        self.assertEqual(result.released_by, self.lab_user)
        self.assertIsNotNone(result.released_at)
        self.assertEqual(result.entered_by, self.lab_user)

        report = self.client.get(reverse("visit_report", args=[visit.pk]))
        body = report.content.decode()
        # High Hemoglobin (20, above male range 13-17) and the abnormal
        # GROWTH/LEVEL rows must all show a Comments-column flag on the
        # actual rendered report, not just in the DB.
        self.assertIn("High", body)
        self.assertIn("Abnormal", body)

    def test_release_routes_to_reception_when_not_doctor_requested(self):
        visit = self._make_visit("F", "25YRS")
        order = self._order_for(visit)
        self._collect_sample(order)
        self._enter_results(
            order, hgb="13", abx="S", growth="No Significant Growth",
            protein="Negative", appearance="Clear",
        )
        self.client.post(reverse("visit_report", args=[visit.pk]), {"action": "release"})
        self.assertTrue(
            QueueEntry.objects.filter(
                visit=visit, queue_type=QueueEntry.TYPE_RECEPTION, processed=False,
            ).exists()
        )
        self.assertFalse(
            QueueEntry.objects.filter(visit=visit, queue_type=QueueEntry.TYPE_DOCTOR).exists()
        )

    def test_release_routes_to_requesting_doctor_when_doctor_requested(self):
        doctor = self.User.objects.create_user(
            username="engine_doctor", password="StrongPass123!",
            role=self.User.ROLE_DOCTOR, hospital=self.hospital,
        )
        visit = self._make_visit("M", "33YRS")
        # Mark this as doctor-requested work, same as the real doctor-order flow.
        QueueEntry.objects.filter(visit=visit, queue_type=QueueEntry.TYPE_LAB_RECEPTION).delete()
        QueueEntry.objects.create(
            hospital=self.hospital, visit=visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR,
            reason="Doctor requested: Full Panel", requested_by=doctor,
        )
        order = self._order_for(visit)
        self._collect_sample(order)
        self._enter_results(
            order, hgb="13", abx="S", growth="No Significant Growth",
            protein="Negative", appearance="Clear",
        )
        self.client.post(reverse("visit_report", args=[visit.pk]), {"action": "release"})

        self.assertTrue(
            QueueEntry.objects.filter(
                visit=visit, queue_type=QueueEntry.TYPE_DOCTOR, processed=False, requested_by=doctor,
            ).exists()
        )
        self.assertFalse(
            QueueEntry.objects.filter(visit=visit, queue_type=QueueEntry.TYPE_RECEPTION).exists()
        )


class LabEngineReviewAndPaymentGateTests(LabEngineTestBase):
    def setUp(self):
        super().setUp()
        self.lab_settings.require_review_before_release = True
        self.lab_settings.save(update_fields=["require_review_before_release"])

    def test_release_blocked_until_marked_reviewed(self):
        visit = self._make_visit("M", "30YRS")
        order = self._order_for(visit)
        self._collect_sample(order)
        self._enter_results(
            order, hgb="14", abx="S", growth="No Significant Growth",
            protein="Negative", appearance="Clear",
        )

        response = self.client.post(reverse("visit_report", args=[visit.pk]), {"action": "release"})
        self.assertRedirects(response, reverse("visit_report", args=[visit.pk]))
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.ENTERED, "release must not proceed without a review step")

        self.client.post(reverse("visit_report", args=[visit.pk]), {"action": "mark_reviewed"})
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.REVIEWED)

        self.client.post(reverse("visit_report", args=[visit.pk]), {"action": "release"})
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.RELEASED)


class LabEnginePaymentGateTests(LabEngineTestBase):
    def setUp(self):
        super().setUp()
        self.lab_settings.payment_required_before_release = True
        self.lab_settings.save(update_fields=["payment_required_before_release"])

    def test_release_blocked_while_visit_balance_unpaid(self):
        visit = self._make_visit("F", "30YRS")
        order = self._order_for(visit)
        self._collect_sample(order)
        self._enter_results(
            order, hgb="13", abx="S", growth="No Significant Growth",
            protein="Negative", appearance="Clear",
        )
        self.assertFalse(visit.is_fully_paid)

        self.client.post(reverse("visit_report", args=[visit.pk]), {"action": "release"})
        order.refresh_from_db()
        self.assertEqual(order.stage, OrderStage.ENTERED, "release must not proceed while the balance is unpaid")


class LabEngineTenantIsolationTests(LabEngineTestBase):
    def test_lab_user_cannot_reach_another_hospitals_order(self):
        other_hospital = Hospital.objects.create(name="Lumina Other Engine", subdomain="lumina-other-engine")
        other_patient = Patient.objects.create(
            hospital=other_hospital, name="Other Patient", age="40YRS", sex="M",
        )
        other_visit = Visit.objects.create(
            patient=other_patient, hospital=other_hospital, total_amount=Decimal("10.00"),
        )
        other_service = Service.objects.create(
            hospital=other_hospital, name="Full Panel", category=Service.CATEGORY_LAB,
            price=Decimal("10.00"), lab_test_next=self.test,
        )
        other_visit_service = VisitService.objects.create(
            visit=other_visit, service=other_service, price_at_time=Decimal("10.00"),
        )
        other_order = LabOrder.objects.create(
            visit_service=other_visit_service, test=self.test, hospital=other_hospital,
            patient_sex=Sex.MALE, patient_age_years=40,
        )

        response = self.client.get(reverse("pick_sample", args=[other_order.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse("visit_report", args=[other_visit.pk]))
        self.assertEqual(response.status_code, 404)


class LabTestHospitalBackfillMigrationTests(TransactionTestCase):
    """
    Regression test for the data migration in lab/migrations/0030 that
    backfills LabTest.hospital before making it NOT NULL. Locally that
    backfill already happened by hand a long time ago, so 0030 is a no-op
    here in normal runs — this test is the only thing that actually
    exercises the backfill logic, by rolling the schema back to right after
    `hospital` was added (still nullable), recreating the pre-scoping shape
    of real data (one LabTest shared by two hospitals' order history), then
    migrating forward again and checking the split came out right. This is
    exactly what happens the first time this migration runs against a
    database that still has an old, un-scoped lab catalog — e.g. live.
    """

    def test_shared_labtest_splits_by_order_history_on_first_run(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("lab", "0029_labtest_hospital")])

        # Reload so project_state reflects the rollback just performed. Also
        # pull in reception's migration that adds Service.lab_test_next —
        # it's not a dependency of lab.0029, just applied before it in a
        # normal full migrate, so it has to be named explicitly here to be
        # part of the frozen historical model state.
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state(
            [("lab", "0029_labtest_hospital"), ("reception", "0022_service_lab_test_next")]
        ).apps

        OldHospital = old_apps.get_model("accounts", "Hospital")
        OldServiceCategory = old_apps.get_model("lab", "ServiceCategory")
        OldLabTest = old_apps.get_model("lab", "LabTest")
        OldParameter = old_apps.get_model("lab", "Parameter")
        OldParameterRange = old_apps.get_model("lab", "ParameterRange")
        OldService = old_apps.get_model("reception", "Service")
        OldPatient = old_apps.get_model("reception", "Patient")
        OldVisit = old_apps.get_model("reception", "Visit")
        OldVisitService = old_apps.get_model("reception", "VisitService")
        OldLabOrder = old_apps.get_model("lab", "LabOrder")

        keeper = OldHospital.objects.create(name="Keeper Hospital", subdomain="mig-keeper")
        other = OldHospital.objects.create(name="Other Hospital", subdomain="mig-other")
        category = OldServiceCategory.objects.create(name="Mig Test Category")

        shared_test = OldLabTest.objects.create(
            name="Complete Blood Count", code="CBC", category=category,
            result_type="parameter_panel",
        )
        self.assertIsNone(shared_test.hospital_id)

        parameter = OldParameter.objects.create(
            test=shared_test, name="Hemoglobin", unit="g/dL", value_type="numeric", sort_order=1,
        )
        OldParameterRange.objects.create(
            parameter=parameter, sex="ANY", ref_low=Decimal("12.0"), ref_high=Decimal("16.0"),
        )

        svc_keeper = OldService.objects.create(
            hospital=keeper, name="CBC", category="lab", price=Decimal("10000"), lab_test_next=shared_test,
        )
        svc_other = OldService.objects.create(
            hospital=other, name="CBC", category="lab", price=Decimal("12000"), lab_test_next=shared_test,
        )

        def make_order(hospital, service):
            patient = OldPatient.objects.create(
                hospital=hospital, name=f"Mig Patient {hospital.pk}-{OldLabOrder.objects.count()}",
                registration_date=date.today(), age="30YRS", sex="M",
            )
            visit = OldVisit.objects.create(
                patient=patient, hospital=hospital, total_amount=service.price, status="in_progress",
            )
            visit_service = OldVisitService.objects.create(visit=visit, service=service, price_at_time=service.price)
            return OldLabOrder.objects.create(visit_service=visit_service, test=shared_test, hospital=hospital)

        for _ in range(3):
            make_order(keeper, svc_keeper)
        make_order(other, svc_other)

        # Roll all the way forward again to the current head before making
        # any assertions, so the shared test DB is left correctly migrated
        # even if an assertion below fails.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

        shared_test_current = LabTest.objects.get(pk=shared_test.pk)
        self.assertEqual(
            shared_test_current.hospital_id, keeper.pk,
            "the hospital with the most order history should keep the original row",
        )

        # Scoped to this test's own hospital, not a bare name filter — the
        # lab app also carries a starter-template seed migration (0032)
        # that creates its own "Complete Blood Count" elsewhere, and this
        # test's own rollback/rollforward can cause that one to re-run too.
        clones = LabTest.objects.filter(hospital_id=other.pk, name="Complete Blood Count")
        self.assertEqual(clones.count(), 1, "the other hospital should get exactly one clone")
        clone = clones.first()

        clone_params = list(Parameter.objects.filter(test=clone))
        self.assertEqual(len(clone_params), 1)
        clone_range = ParameterRange.objects.get(parameter=clone_params[0])
        self.assertEqual(clone_range.ref_low, Decimal("12.000"))
        self.assertEqual(clone_range.ref_high, Decimal("16.000"))

        svc_other_current = Service.objects.get(pk=svc_other.pk)
        self.assertEqual(svc_other_current.lab_test_next_id, clone.pk, "the other hospital's service should repoint to its clone")
        svc_keeper_current = Service.objects.get(pk=svc_keeper.pk)
        self.assertEqual(svc_keeper_current.lab_test_next_id, shared_test.pk)

        other_orders = LabOrder.objects.filter(hospital_id=other.pk)
        self.assertTrue(all(o.test_id == clone.pk for o in other_orders))
        keeper_orders = LabOrder.objects.filter(hospital_id=keeper.pk)
        self.assertTrue(all(o.test_id == shared_test.pk for o in keeper_orders))

        self.assertEqual(LabTest.objects.filter(hospital__isnull=True).count(), 0)


class SeedStarterTemplatesMigrationTests(TestCase):
    """
    lab.0032 seeds the 5 curated starter templates (CBC, Malaria, Typhoid,
    Urinalysis, Urine C&S) onto the Ternah Books tenant so 'Clone a Test'
    has the same starting catalog on any database, not just this one dev
    sqlite file. The migration already ran when the test DB was built, so
    this just re-invokes its RunPython function directly against the
    current schema (which it's written against anyway — no historical
    model trickery needed here, unlike the 0030 backfill) and checks the
    idempotency guard, since this function needs to survive re-runs.
    """

    def test_seed_function_is_idempotent_and_builds_full_cbc_panel(self):
        import importlib

        from django.apps import apps as real_apps

        module = importlib.import_module("lab.migrations.0032_seed_starter_templates")

        # The migration already seeded these once when the test DB was
        # created; clear them so we can observe a from-scratch run.
        LabTest.objects.filter(is_starter_template=True).delete()

        module.seed_starter_templates(real_apps, None)

        template_hospital = Hospital.objects.get(subdomain="ternah-books")
        starters = LabTest.objects.filter(hospital=template_hospital, is_starter_template=True)
        self.assertEqual(
            {t.name for t in starters},
            {"Complete Blood Count", "Malaria Rapid Test", "Typhoid Test", "Urinalysis", "Urine Culture & Sensitivity"},
        )

        cbc = starters.get(name="Complete Blood Count")
        self.assertEqual(cbc.parameters.count(), 20)
        hgb = cbc.parameters.get(name="HGB")
        self.assertEqual(hgb.ranges.count(), 3)
        male_range = hgb.ranges.get(sex=Sex.MALE)
        self.assertEqual(male_range.ref_low, Decimal("12.000"))
        self.assertEqual(male_range.ref_high, Decimal("16.000"))

        ucs = starters.get(name="Urine Culture & Sensitivity")
        self.assertEqual(ucs.parameters.filter(group_label="Penicillins").count(), 2)

        malaria = starters.get(name="Malaria Rapid Test")
        self.assertEqual(malaria.options.count(), 2)

        # Re-running must not create duplicates.
        module.seed_starter_templates(real_apps, None)
        self.assertEqual(
            LabTest.objects.filter(hospital=template_hospital, is_starter_template=True).count(), 5,
        )


class LinkLegacyLabServicesCommandTests(TestCase):
    """
    link_legacy_lab_services is the one-time fixup for lab Services billed
    under the old flow that never got linked to a LabTest, so billing them
    doesn't quietly produce nothing in the Lab Queue. CBC/Urinalysis should
    match the real curated templates; anything else falls back to a simple
    Positive/Negative test, same shape as Malaria/Typhoid.
    """

    def setUp(self):
        self.hospital = Hospital.objects.create(name="Legacy Link Hospital", subdomain="legacy-link")
        self.cbc_service = Service.objects.create(
            hospital=self.hospital, name="Complete Blood Count", category=Service.CATEGORY_LAB, price=Decimal("15000"),
        )
        self.urinalysis_service = Service.objects.create(
            hospital=self.hospital, name="Urinalysis", category=Service.CATEGORY_LAB, price=Decimal("8000"),
        )
        self.other_service = Service.objects.create(
            hospital=self.hospital, name="Random Antigen Test", category=Service.CATEGORY_LAB, price=Decimal("5000"),
        )
        self.stool_service = Service.objects.create(
            hospital=self.hospital, name="Stool Analysis", category=Service.CATEGORY_LAB, price=Decimal("6000"),
        )
        # A non-lab service must never be touched by this command.
        self.consultation_service = Service.objects.create(
            hospital=self.hospital, name="General Consultation", category=Service.CATEGORY_CONSULTATION, price=Decimal("10000"),
        )

    def test_dry_run_reports_without_saving(self):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("link_legacy_lab_services", "--dry-run", stdout=out)
        self.assertIn("Complete Blood Count", out.getvalue())
        self.cbc_service.refresh_from_db()
        self.assertIsNone(self.cbc_service.lab_test_next)

    def test_links_cbc_and_urinalysis_to_cloned_templates_and_rest_to_predefined(self):
        from django.core.management import call_command

        call_command("link_legacy_lab_services")

        self.cbc_service.refresh_from_db()
        self.urinalysis_service.refresh_from_db()
        self.other_service.refresh_from_db()
        self.consultation_service.refresh_from_db()

        self.assertIsNotNone(self.cbc_service.lab_test_next)
        cbc_test = self.cbc_service.lab_test_next
        self.assertEqual(cbc_test.hospital_id, self.hospital.pk, "the clone must belong to this hospital, not the Ternah template tenant")
        self.assertEqual(cbc_test.parameters.count(), 20)

        self.assertIsNotNone(self.urinalysis_service.lab_test_next)
        self.assertEqual(self.urinalysis_service.lab_test_next.hospital_id, self.hospital.pk)
        self.assertEqual(self.urinalysis_service.lab_test_next.parameters.count(), 20)

        self.assertIsNotNone(self.other_service.lab_test_next)
        fallback_test = self.other_service.lab_test_next
        self.assertEqual(fallback_test.hospital_id, self.hospital.pk)
        self.assertEqual(fallback_test.result_type, ResultType.DEFINED_OPTION)
        self.assertEqual(
            set(fallback_test.options.values_list("label", flat=True)), {"Positive", "Negative"},
        )

        self.stool_service.refresh_from_db()
        self.assertIsNotNone(self.stool_service.lab_test_next)
        stool_test = self.stool_service.lab_test_next
        self.assertEqual(stool_test.hospital_id, self.hospital.pk)
        self.assertEqual(stool_test.result_type, ResultType.FREE_ENTRY, "Stool Analysis should match live's existing free-text behavior")
        self.assertEqual(stool_test.options.count(), 0)

        self.assertIsNone(self.consultation_service.lab_test_next, "non-lab services must never be touched")

    def test_idempotent_on_rerun(self):
        from django.core.management import call_command

        call_command("link_legacy_lab_services")
        call_command("link_legacy_lab_services")

        self.assertEqual(
            LabTest.objects.filter(hospital=self.hospital, name="Complete Blood Count").count(), 1,
            "re-running must not create a second clone",
        )
        self.assertEqual(
            LabTest.objects.filter(hospital=self.hospital, name="Random Antigen Test").count(), 1,
        )
