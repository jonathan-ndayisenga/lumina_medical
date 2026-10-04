from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Hospital, HospitalModuleSubscription, Module, User
from admin_dashboard.forms import HospitalServiceForm
from doctor.models import Consultation, VisitOutcome
from ophthalmology.models import BaseRefraction, EyeExam
from reception.models import Patient, QueueEntry, Service, Visit, VisitService


def _enable_modules(hospital, *codes):
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
        HospitalModuleSubscription.objects.get_or_create(hospital=hospital, module=module, defaults={"is_active": True})


class SpecialtyTriggeredConsultationTests(TestCase):
    """Billing an Ophthalmology consultation gives the doctor the eye exam;
    General Consultation keeps the existing form."""

    def setUp(self):
        self.hospital = Hospital.objects.create(name="Town Eye Clinic", subdomain="town-eye")
        _enable_modules(self.hospital, "doctor", "reception")
        self.doctor = User.objects.create_user(username="eyedoc", password="x", role=User.ROLE_DOCTOR, hospital=self.hospital)
        self.general = Service.objects.create(hospital=self.hospital, name="General Consultation", category=Service.CATEGORY_CONSULTATION, price=Decimal("20000"))
        self.eye = Service.objects.create(
            hospital=self.hospital, name="Ophthalmology Consultation", category=Service.CATEGORY_CONSULTATION,
            specialty=Service.SPECIALTY_OPHTHALMOLOGY, price=Decimal("50000"),
        )
        self.eye_visit = self._visit(self.eye, "Kakai Fazira")
        self.general_visit = self._visit(self.general, "John General")
        self.client.force_login(self.doctor)

    def _visit(self, service, name):
        patient = Patient.objects.create(hospital=self.hospital, name=name, age="30YRS", sex="F")
        visit = Visit.objects.create(patient=patient, hospital=self.hospital, created_by=self.doctor, total_amount=service.price)
        VisitService.objects.create(visit=visit, service=service, price_at_time=service.price)
        QueueEntry.objects.create(hospital=self.hospital, visit=visit, queue_type=QueueEntry.TYPE_DOCTOR)
        return visit

    def _consult_url(self, visit):
        return reverse("consultation", args=[visit.pk])

    def test_general_consultation_is_unchanged_and_gains_visit_outcome(self):
        response = self.client.get(self._consult_url(self.general_visit))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clinical Assessment")
        self.assertNotContains(response, "Slit Lamp Examination")
        self.assertContains(response, "Visit outcome")
        self.assertFalse(self.general_visit.is_eye_visit)

    def test_eye_visit_opens_base_refraction_first(self):
        self.assertTrue(self.eye_visit.is_eye_visit)
        response = self.client.get(self._consult_url(self.eye_visit))
        self.assertRedirects(response, reverse("eye_base_refraction", args=[self.eye_visit.pk]))
        page = self.client.get(reverse("eye_base_refraction", args=[self.eye_visit.pk]))
        for text in ("Visual Acuity", "Pupils", "Autorefractor", "Retinoscope", "Subjective", "Keratometry", "6/60", "NPL"):
            self.assertContains(page, text)

    def test_skipping_refraction_shows_the_exam_with_a_warning_not_a_block(self):
        response = self.client.get(self._consult_url(self.eye_visit) + "?skip_refraction=1")
        self.assertContains(response, "Slit Lamp Examination")
        self.assertContains(response, "Base refraction has not been recorded")
        self.assertNotContains(response, "Clinical Assessment")
        # The skip is remembered for this visit, so the doctor isn't bounced again.
        self.assertEqual(self.client.get(self._consult_url(self.eye_visit)).status_code, 200)

    def test_base_refraction_saves_and_continues_to_the_main_exam(self):
        response = self.client.post(reverse("eye_base_refraction", args=[self.eye_visit.pk]), {
            "va_best_right": "6/9", "va_best_left": "6/12", "oriented": "on", "pupils_perrl": "on",
            "rx_subjective_right_sphere": "-2.00", "add_power": "+1.50", "comments": "Cooperative",
        })
        self.assertRedirects(response, self._consult_url(self.eye_visit), fetch_redirect_response=False)
        record = BaseRefraction.objects.get(visit=self.eye_visit)
        self.assertEqual(record.data["va_best_right"], "6/9")
        self.assertEqual(record.data["rx_subjective_right_sphere"], "-2.00")
        self.assertEqual(record.data["oriented"], "yes")
        page = self.client.get(self._consult_url(self.eye_visit))
        self.assertContains(page, "Base refraction recorded.")

    def test_main_exam_saves_findings_and_feeds_the_shared_consultation(self):
        response = self.client.post(self._consult_url(self.eye_visit), {
            "external_right": "Normal", "external_left": "Swollen lid",
            "sl_cornea_right": ["Clear"], "sl_cornea_left": ["Ulcer", "Not a real option"],
            "sl_cornea_left_other": "Central infiltrate", "sl_lens_left": ["Nuclear cataract"],
            "cdr_right": "0.3", "iop_left": "24",
            "eye_diagnosis": ["Corneal ulcer, left eye", "", "Cataract, left eye"],
            "history_comments": "Painful red left eye for 3 days", "management_plan": "Topical antibiotics, review in 1 week",
            "outcome": VisitOutcome.REFERRED, "outcome_notes": "Mulago Eye Unit",
        })
        self.assertRedirects(response, reverse("consultation_detail", args=[self.eye_visit.pk]), fetch_redirect_response=False)
        exam = EyeExam.objects.get(visit=self.eye_visit)
        self.assertEqual(exam.slit_lamp["cornea"]["left"], ["Ulcer"])  # unknown values are dropped
        self.assertEqual(exam.slit_lamp["cornea"]["left_other"], "Central infiltrate")
        self.assertEqual(exam.diagnoses, ["Corneal ulcer, left eye", "Cataract, left eye"])
        consultation = Consultation.objects.get(visit=self.eye_visit)
        self.assertEqual(consultation.diagnosis, "Corneal ulcer, left eye; Cataract, left eye")
        self.assertEqual(consultation.signs_symptoms, "Painful red left eye for 3 days")
        self.assertEqual(consultation.outcome, VisitOutcome.REFERRED)
        detail = self.client.get(reverse("consultation_detail", args=[self.eye_visit.pk]))
        for text in ("Eye Examination", "Ulcer, Central infiltrate", "Nuclear cataract", "Referred", "Mulago Eye Unit"):
            self.assertContains(detail, text)

    def test_review_appointment_needs_a_review_date(self):
        data = {"signs_symptoms": "Headache", "diagnosis": "Migraine", "treatment": "Rest", "outcome": VisitOutcome.REVIEW}
        response = self.client.post(self._consult_url(self.general_visit), data)
        self.assertContains(response, "A Review Appointment needs a review date.")
        self.assertFalse(Consultation.objects.filter(visit=self.general_visit).exists())
        data["follow_up_date"] = "2026-10-20"
        self.client.post(self._consult_url(self.general_visit), data)
        self.assertEqual(Consultation.objects.get(visit=self.general_visit).outcome, VisitOutcome.REVIEW)

    def test_only_consultation_services_keep_a_specialty(self):
        form = HospitalServiceForm(
            {"name": "Eye drops", "category": Service.CATEGORY_PROCEDURE, "specialty": Service.SPECIALTY_OPHTHALMOLOGY,
             "price": "5000", "is_active": "on"},
            hospital=self.hospital,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["specialty"], Service.SPECIALTY_GENERAL)
