from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Hospital, HospitalModuleSubscription, Module, User
from admin_dashboard.revenue import by_department, split_payment
from reception.models import Patient, Payment, Service, Visit, VisitService


class RevenueByDepartmentTests(TestCase):
    def setUp(self):
        self.hospital = Hospital.objects.create(name="Revenue Clinic", subdomain="revenue-clinic")
        for code in ("finance", "doctor", "reception", "inventory", "hospital_mgmt"):
            module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
            HospitalModuleSubscription.objects.get_or_create(hospital=self.hospital, module=module, defaults={"is_active": True})
        self.admin = User.objects.create_user(username="rev-admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=self.hospital, is_active=True)
        patient = Patient.objects.create(hospital=self.hospital, name="Rev Patient", age="40YRS", sex="M")
        self.visit = Visit.objects.create(patient=patient, hospital=self.hospital, total_amount=Decimal("100000"))
        consult = Service.objects.create(hospital=self.hospital, name="Consultation", category=Service.CATEGORY_CONSULTATION, price=Decimal("20000"))
        drug = Service.objects.create(hospital=self.hospital, name="Pharmacy Item: Amoxil", category=Service.CATEGORY_PHARMACY, price=Decimal("1"))
        lab = Service.objects.create(hospital=self.hospital, name="CBC", category=Service.CATEGORY_LAB, price=Decimal("30000"))
        VisitService.objects.create(visit=self.visit, service=consult, price_at_time=Decimal("20000"))
        VisitService.objects.create(visit=self.visit, service=drug, price_at_time=Decimal("50000"))
        VisitService.objects.create(visit=self.visit, service=lab, price_at_time=Decimal("30000"))
        self.today = timezone.localdate()

    def pay(self, amount):
        return Payment.objects.create(visit=self.visit, amount=Decimal("100000"), amount_paid=Decimal(amount), mode=Payment.MODE_CASH)

    def test_a_part_payment_is_split_in_proportion(self):
        self.pay("50000")
        revenue = by_department(self.hospital, self.today, self.today)
        collected = {row["key"]: row["collected"] for row in revenue["rows"]}
        self.assertEqual(collected, {"consultation": Decimal("10000.00"), "pharmacy": Decimal("25000.00"), "lab": Decimal("15000.00")})
        self.assertEqual(revenue["pharmacy_collected"], Decimal("25000.00"))
        self.assertEqual(revenue["total_billed"], Decimal("100000"))
        self.assertEqual(sum(collected.values()), revenue["total_collected"])

    def test_shares_always_add_up_to_the_payment(self):
        lines = list(self.visit.visit_services.select_related("service"))
        for amount in ("1", "33333", "99999.99", "7"):
            self.assertEqual(sum(split_payment(Decimal(amount), lines).values()), Decimal(amount))

    def test_waived_and_out_of_period_payments_are_left_out(self):
        self.pay("40000")
        waived = self.pay("10000")
        Payment.objects.filter(pk=waived.pk).update(status=Payment.STATUS_WAIVED)
        old = self.pay("10000")
        Payment.objects.filter(pk=old.pk).update(paid_at=timezone.now() - timezone.timedelta(days=60))
        self.assertEqual(by_department(self.hospital, self.today, self.today)["total_collected"], Decimal("40000"))

    def test_finance_doctor_and_patient_reports_show_the_same_numbers(self):
        self.pay("50000")
        self.client.force_login(self.admin)
        day = self.today.isoformat()
        finance = self.client.get(reverse("financial_report"), {"start_date": day, "end_date": day})
        doctors = self.client.get(reverse("report_consultations"), {"start": day, "end": day})
        patients = self.client.get(reverse("patient_demographic_report"), {"start": day, "end": day})
        for response in (finance, doctors, patients):
            self.assertEqual(response.status_code, 200)
            revenue = response.context["department_revenue"]
            self.assertEqual(revenue["total_collected"], Decimal("50000"))
            self.assertEqual(revenue["pharmacy_collected"], Decimal("25000.00"))
            self.assertContains(response, "Revenue by department")
        self.assertEqual(finance.context["income_period"], Decimal("50000"))
