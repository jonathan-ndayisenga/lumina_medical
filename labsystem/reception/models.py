import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from accounts.models import Hospital


AGE_TEXT_RE = re.compile(r"^\s*(\d*\.?\d+)\s*([A-Z]+)?\s*$")


def parse_age_text(age_text):
    """'22YRS' -> (Decimal('22'), 'YRS'); '6 mth' -> (6, 'MTH'). Unknown units
    read as years. Returns (None, None) when the text has no number."""
    match = AGE_TEXT_RE.match((age_text or "").strip().upper())
    if not match:
        return None, None
    unit = match.group(2) or "YRS"
    if unit.startswith("D"):
        unit = "DAYS"
    elif unit.startswith("W"):
        unit = "WKS"
    elif unit.startswith("M"):
        unit = "MTH"
    else:
        unit = "YRS"
    return Decimal(match.group(1)), unit


def _months_before(day, months):
    """The same day-of-month `months` earlier, clamped to the month's end."""
    import calendar
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def estimate_birth_date(value, unit, reference):
    """Date of birth for someone who was `value` `unit` old on `reference`.
    Keeps the reference's day of the month, so a baby registered at 6 months
    on the 20th turns 7 months on the 20th, not on the 1st."""
    from datetime import timedelta
    if value is None:
        return None
    value = Decimal(value)
    if unit == "DAYS":
        return reference - timedelta(days=int(value))
    if unit == "WKS":
        return reference - timedelta(days=int(value * 7))
    if unit == "MTH":
        return _months_before(reference, int(value))
    return _months_before(reference, int(value * 12))


def hospital_initials(name: str, fallback: str = "RCT") -> str:
    """Extract uppercase initials from a hospital name.
    'Lumina Medical Services' -> 'LMS'
    'Mercy Hospital'          -> 'MH'
    'Home_care'               -> 'HC'  (splits on underscore/hyphen too)
    Words like 'and', 'the', 'of' are skipped.
    """
    stop = {"and", "the", "of", "a", "an", "for", "in", "at", "by"}
    parts = re.split(r"[\s_\-]+", name or "")
    initials = "".join(
        w[0].upper() for w in parts if w and w.lower() not in stop and w[0].isalpha()
    )
    return initials or fallback


def format_doctor_name(name: str) -> str:
    if not name:
        return ""
    # Strip a typed-in title ("Dr.", "Dr ", "Doctor") but not names that just
    # start with those letters ("Drake", "doctor1").
    cleaned = re.sub(r"^(?:dr\.|dr\s+|doctor\b\.?)\s*", "", name.strip(), flags=re.IGNORECASE).strip()
    return f"Dr. {cleaned}" if cleaned else ""


class Patient(models.Model):
    SEX_CHOICES = [
        ("M", "Male"),
        ("F", "Female"),
        ("O", "Other"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="patients")
    name = models.CharField(max_length=200)
    registration_date = models.DateField(null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    age = models.CharField(max_length=20, help_text="Examples: 22YRS, 6MTH, 3WKS, 5DAYS")
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    sex = models.CharField(max_length=10, choices=SEX_CHOICES)
    contact = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    next_of_kin = models.CharField(max_length=200, blank=True)
    next_of_kin_contact = models.CharField(max_length=50, blank=True)
    next_of_kin_email = models.EmailField(blank=True)
    next_of_kin_address = models.CharField(max_length=255, blank=True)
    nin = models.CharField(max_length=50, blank=True, help_text="National Identification Number (optional).")
    id_verified = models.BooleanField(default=False)
    insurance_provider = models.CharField(max_length=200, blank=True)
    insurance_policy_number = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    # ── Age helpers ───────────────────────────────────────────────────────────

    @property
    def estimated_date_of_birth(self):
        """The real date of birth, or one worked out from the age typed at
        registration and the day it was typed, so the age still moves on."""
        if self.date_of_birth:
            return self.date_of_birth
        value, unit = parse_age_text(self.age)
        if value is None:
            return None
        recorded = timezone.localtime(self.created_at).date() if self.created_at else timezone.localdate()
        return estimate_birth_date(value, unit, recorded)

    def age_years_at(self, reference_date=None):
        """Whole years of age on reference_date (today by default), or None."""
        dob = self.estimated_date_of_birth
        if not dob:
            return None
        ref = reference_date or timezone.localdate()
        return max(ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day)), 0)

    def _age_string(self, reference_date):
        """Human-readable age from the date of birth vs reference_date. Without
        a date of birth the registered age is carried forward from the day it
        was recorded (see estimated_date_of_birth)."""
        dob = self.estimated_date_of_birth
        if not dob:
            return self.age
        years = reference_date.year - dob.year - (
            (reference_date.month, reference_date.day) < (dob.month, dob.day)
        )
        if years >= 2:
            return f"{years} yrs"
        total_months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)
        if reference_date.day < dob.day:
            total_months -= 1
        total_months = max(total_months, 0)
        if total_months >= 12:
            y, m = divmod(total_months, 12)
            return f"{y} yr{'s' if y != 1 else ''} {m} mo" if m else f"{y} yr{'s' if y != 1 else ''}"
        if total_months >= 1:
            return f"{total_months} mo"
        # Under one month old -- weeks/days, for newborns.
        total_days = max((reference_date - dob).days, 0)
        if total_days >= 7:
            weeks = total_days // 7
            return f"{weeks} wk{'s' if weeks != 1 else ''}"
        return f"{total_days} day{'s' if total_days != 1 else ''}"

    @property
    def current_age(self):
        """Age calculated from DOB vs today — updates automatically over time."""
        return self._age_string(timezone.localdate())

    def age_at(self, visit_date):
        """Age calculated from DOB vs a specific visit date."""
        # A stored visit time is UTC; the hospital's calendar day is local
        # (Kampala is UTC+3), so a visit just after midnight belongs to today.
        if hasattr(visit_date, "date"):
            visit_date = timezone.localtime(visit_date).date() if timezone.is_aware(visit_date) else visit_date.date()
        return self._age_string(visit_date)


class Visit(models.Model):
    TYPE_NORMAL = "normal"
    TYPE_FOLLOW_UP = "follow_up"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_PACKAGE = "package_visit"

    VISIT_TYPE_CHOICES = [
        (TYPE_NORMAL, "Normal Visit"),
        (TYPE_FOLLOW_UP, "Follow-up"),
        (TYPE_ADJUSTMENT, "Adjustment Visit (Medication Swap)"),
        (TYPE_PACKAGE, "Package Visit"),
    ]

    STATUS_IN_PROGRESS = "in_progress"
    STATUS_READY_FOR_BILLING = "ready_for_billing"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_READY_FOR_BILLING, "Ready For Billing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="visits")
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="visits")
    visit_date = models.DateTimeField(auto_now_add=True)
    visit_type = models.CharField(max_length=20, choices=VISIT_TYPE_CHOICES, default=TYPE_NORMAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    parent_visit = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="follow_up_visits",
    )
    package_source = models.ForeignKey(
        "VisitService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reused_by_visits",
        help_text="The specific package purchase VisitService line (on an earlier, completed and "
                  "fully paid visit) being reused on this visit, without re-billing it.",
    )
    adjustment_origin_prescription = models.ForeignKey(
        "doctor.Prescription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adjustment_visits",
    )
    adjustment_days_used = models.PositiveIntegerField(default=0)
    adjustment_remaining_days = models.PositiveIntegerField(default=0)
    adjustment_reason = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_visits",
    )
    notes = models.TextField(blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True, default="")
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, verbose_name="Weight at visit (kg)")
    # Debtors: a visit that only owes money leaves the reception queue for
    # the finance Debtor Ledger (by hand after DEBTOR_MANUAL_MOVE_DAYS, on
    # its own after DEBTOR_AUTO_MOVE_DAYS). Finance can later clear debts
    # older than a year; the balance stays on record, it just stops showing.
    moved_to_debtors_at = models.DateTimeField(null=True, blank=True)
    debt_cleared_at = models.DateTimeField(null=True, blank=True)
    debt_cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    DEBTOR_MANUAL_MOVE_DAYS = 3
    DEBTOR_AUTO_MOVE_DAYS = 30
    DEBT_CLEARABLE_AFTER_DAYS = 365

    class Meta:
        ordering = ["-visit_date"]

    def __str__(self):
        return f"{self.patient.name} - {self.visit_date:%Y-%m-%d %H:%M}"

    def adjust_total(self, delta):
        """Add `delta` to the bill (negative to take a charge off), never below 0.

        Done as one UPDATE in the database, not read-add-save: two people adding
        charges to the same visit at once (doctor and pharmacy, say) used to
        each save their own sum, so one charge silently fell off the bill. The
        UPDATE also locks the row until the surrounding transaction ends."""
        delta = Decimal(str(delta or 0))
        if not self.pk or delta == 0:
            return self.total_amount
        Visit.objects.filter(pk=self.pk).update(
            total_amount=Greatest(
                F("total_amount") + Value(delta),
                Value(Decimal("0")),
                output_field=models.DecimalField(max_digits=10, decimal_places=2),
            )
        )
        self.total_amount = Visit.objects.values_list("total_amount", flat=True).get(pk=self.pk)
        return self.total_amount

    def removal_blocked_message(self, reduce_by):
        """Why the bill can't come down by `reduce_by`, or "" if it can.

        Taking a charge off after money was paid for it used to push the bill
        below what was paid; the balance showed 0 and the patient's money just
        vanished from view. The removal is refused until a refund is made."""
        reduce_by = Decimal(str(reduce_by or 0))
        if reduce_by <= 0:
            return ""
        paid = self.total_paid
        new_total = max(Decimal(self.total_amount or 0) - reduce_by, Decimal("0"))
        if new_total >= paid:
            return ""
        return (
            f"UGX {paid:,.0f} has already been paid on this visit, and taking this off would bring the bill to "
            f"UGX {new_total:,.0f}. Refund the patient UGX {paid - new_total:,.0f} first, then remove it."
        )

    @property
    def total_paid(self):
        # Avoid importing Payment here (it is defined later in this module).
        total = self.payments.exclude(status="waived").aggregate(total=models.Sum("amount_paid"))["total"]
        return total or Decimal("0")

    @property
    def balance_due(self):
        return max((self.total_amount or Decimal("0")) - self.total_paid, Decimal("0"))

    @property
    def is_fully_paid(self):
        return self.balance_due <= 0

    @property
    def is_unbilled(self):
        """No payment has been made yet — genuinely awaiting first billing."""
        return self.total_paid <= 0

    @property
    def has_outstanding_balance(self):
        """At least one payment has been made, but the balance isn't fully cleared yet."""
        return self.total_paid > 0 and not self.is_fully_paid

    @property
    def is_adjustment_visit(self):
        return self.visit_type == self.TYPE_ADJUSTMENT

    @property
    def is_package_visit(self):
        return self.visit_type == self.TYPE_PACKAGE

    @property
    def is_follow_up_visit(self):
        return self.visit_type == self.TYPE_FOLLOW_UP

    @property
    def is_in_debtors(self):
        return bool(self.moved_to_debtors_at) and not self.debt_cleared_at

    @property
    def specialty(self):
        """The specialty of the consultation service billed on this visit,
        which decides the doctor's exam form. "general" when none is set."""
        specialties = (
            self.visit_services.filter(service__category=Service.CATEGORY_CONSULTATION)
            .exclude(service__specialty=Service.SPECIALTY_GENERAL)
            .values_list("service__specialty", flat=True)
        )
        return specialties.first() or Service.SPECIALTY_GENERAL

    @property
    def is_eye_visit(self):
        return self.specialty == Service.SPECIALTY_OPHTHALMOLOGY

    def validate_billing_structure(self):
        """
        Validate that the visit has proper billing structure.
        Prevents receptionist loopholes (skipping services, faking follow-ups, etc.)
        
        EXCEPTION: Quick dispense visits (READY_FOR_BILLING with zero amount and no services)
        are allowed to bypass this check because they use a special workflow where drugs are 
        added and billed after dispensing.
        """
        # EXCEPTION: Quick dispense workflow - allow zero amount with no services
        # These visits will be populated with prescriptions and billed later
        if (
            self.status == self.STATUS_READY_FOR_BILLING
            and self.total_amount <= 0
            and self.visit_services.count() == 0
            and self.visit_type == self.TYPE_NORMAL
        ):
            # This is likely a quick dispense visit - skip validation
            return
        
        if self.visit_type == self.TYPE_NORMAL:
            # Normal visits MUST have at least one service
            service_count = self.visit_services.count()
            if service_count == 0:
                raise ValidationError(
                    "Normal visits must have at least one billable service before billing can be completed."
                )
            if self.total_amount <= 0:
                raise ValidationError(
                    "Normal visits must have a positive total amount. Check that services have been properly added."
                )

        elif self.visit_type == self.TYPE_FOLLOW_UP:
            # Follow-up visits MUST have a valid completed, fully paid parent
            # visit. That link is what earns the free trip to the doctor
            # queue — no consultation fee. Reception can't add services to
            # one (VisitForm blocks it); whatever the doctor or lab adds
            # afterwards is billed unless they mark it free_follow_up, and
            # prescriptions are always billed.
            if not self.parent_visit:
                raise ValidationError(
                    "Follow-up visits must be linked to a completed previous visit."
                )
            if self.parent_visit.status != self.STATUS_COMPLETED:
                raise ValidationError(
                    "Follow-up visits must link to a completed previous visit."
                )
            if not self.parent_visit.is_fully_paid:
                raise ValidationError(
                    "Follow-up visits must link to a fully paid previous visit."
                )

        elif self.visit_type == self.TYPE_ADJUSTMENT:
            # Adjustment visits MUST be linked to a valid dispensed prescription
            if not self.adjustment_origin_prescription:
                raise ValidationError(
                    "Adjustment visits must be linked to the prescription being adjusted."
                )
            if not self.adjustment_origin_prescription.dispensed:
                raise ValidationError(
                    "Adjustment visits can only be created for already-dispensed prescriptions."
                )
            
            origin_visit = self.adjustment_origin_prescription.visit
            if origin_visit.status != self.STATUS_COMPLETED:
                raise ValidationError(
                    "The original prescription must come from a completed visit."
                )
            if not origin_visit.is_fully_paid:
                raise ValidationError(
                    "The original prescription must come from a fully paid visit."
                )

        elif self.visit_type == self.TYPE_PACKAGE:
            # Package visits reuse an earlier, completed, fully paid package
            # purchase without re-billing it. They're created with zero queue
            # entries (there's no single destination), so they're also
            # required to have been sent for at least one covered service
            # before they can be billed -- otherwise a fresh, never-used
            # package visit (zero services, zero queue entries, UGX 0
            # balance) could be one-click completed immediately, which
            # permanently blocks it from ever being routed anywhere.
            if not self.package_source_id:
                raise ValidationError(
                    "Package visits must be linked to the package purchase being reused."
                )
            if self.package_source.service.category != Service.CATEGORY_PACKAGE:
                raise ValidationError(
                    "The linked purchase must be a Package-category service."
                )
            source_visit = self.package_source.visit
            if source_visit.status != self.STATUS_COMPLETED:
                raise ValidationError(
                    "The reused package must come from a completed visit."
                )
            if not source_visit.is_fully_paid:
                raise ValidationError(
                    "The reused package must come from a fully paid visit."
                )
            if (
                self.package_source.package_expires_on
                and self.package_source.package_expires_on < timezone.localdate()
            ):
                raise ValidationError(
                    "This package has expired and can no longer be reused."
                )
            if not self.visit_services.filter(covered_by_package=True).exists():
                raise ValidationError(
                    "Send this visit for at least one package-covered service before it can be billed."
                )


class Triage(models.Model):
    """
    Shared vital signs captured per visit.

    This is intentionally visit-scoped (not patient-scoped) so that weight/vitals
    can vary across visits and can be edited by both nurse and doctor.
    """

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="triage")
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    bp_systolic = models.IntegerField(null=True, blank=True)
    bp_diastolic = models.IntegerField(null=True, blank=True)
    pulse = models.IntegerField(null=True, blank=True)  # beats per minute
    respiratory_rate = models.IntegerField(null=True, blank=True)  # breaths per minute
    temperature_celsius = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    oxygen_saturation = models.IntegerField(null=True, blank=True)  # SpO2 %
    glucose_mg_dl = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triage_recorded",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triage_updated",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"Triage - {self.visit.patient.name} ({self.visit_id})"

    def is_complete(self) -> bool:
        """Minimum required for nurse sign-off."""
        return (
            self.weight_kg is not None
            and self.bp_systolic is not None
            and self.bp_diastolic is not None
        )


class Service(models.Model):
    CATEGORY_CONSULTATION = "consultation"
    CATEGORY_LAB = "lab"
    CATEGORY_TRIAGE = "triage"
    CATEGORY_PROCEDURE = "procedure"
    CATEGORY_PHARMACY = "pharmacy"
    CATEGORY_SCAN = "scan"
    CATEGORY_PACKAGE = "package"
    CATEGORY_OTHER = "other"

    CATEGORY_CHOICES = [
        (CATEGORY_CONSULTATION, "Consultation"),
        (CATEGORY_LAB, "Laboratory"),
        (CATEGORY_TRIAGE, "Triage"),
        (CATEGORY_PROCEDURE, "Procedure"),
        (CATEGORY_PHARMACY, "Pharmacy"),
        (CATEGORY_SCAN, "Scan / Ultrasound"),
        (CATEGORY_PACKAGE, "Package"),
        (CATEGORY_OTHER, "Other"),
    ]

    # Only meaningful for consultation services: decides which exam the
    # doctor gets when they open the visit (general form vs eye exam).
    SPECIALTY_GENERAL = "general"
    SPECIALTY_OPHTHALMOLOGY = "ophthalmology"
    SPECIALTY_DENTISTRY = "dentistry"
    SPECIALTY_CHOICES = [
        (SPECIALTY_GENERAL, "General"),
        (SPECIALTY_OPHTHALMOLOGY, "Ophthalmology (eye)"),
        (SPECIALTY_DENTISTRY, "Dentistry (coming soon)"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    specialty = models.CharField(
        max_length=20, choices=SPECIALTY_CHOICES, default=SPECIALTY_GENERAL,
        help_text="For consultation services: which exam the doctor fills in for this visit.",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    is_per_day = models.BooleanField(
        default=False,
        help_text="If checked, billing this service asks for number of days and multiplies the price (e.g. Nursing Fee, Bed Fee).",
    )
    test_profile = models.ForeignKey(
        "lab.TestProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="services",
        help_text="Lab service linked to a test profile (e.g., CBC, Urinalysis)"
    )
    lab_tests_next = models.ManyToManyField(
        "lab.LabTest",
        related_name="services",
        blank=True,
        help_text="Which lab.LabTest(es) this billed service creates orders for. Billed once, but "
                   "can fan out into more than one order -- e.g. a bundled \"Malaria Test\" service "
                   "linking both MRDT and B/S, each entered independently. Required for the service "
                   "to be picked up by the lab queue; unrelated to `test_profile` above, which only "
                   "the historical report archive still reads.",
    )
    package_services = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="included_in_packages",
        blank=True,
        help_text="Only meaningful for category=Package -- the exact service(s) this package "
                   "includes (e.g. Antenatal -> Consultation, CBC, Urinalysis, Obstetric "
                   "Ultrasound). A visit with this package billed can be sent for any of these "
                   "specific services, without billing anything extra for them -- services not "
                   "on this list still bill normally even if their category matches.",
    )
    package_drugs = models.ManyToManyField(
        "admin_dashboard.InventoryItem",
        related_name="included_in_packages",
        blank=True,
        help_text="Only meaningful for category=Package -- specific pharmacy drug(s) this "
                   "package includes, free for whatever quantity/duration the doctor "
                   "prescribes. Drugs not on this list still bill normally.",
    )
    max_visits = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Only meaningful for category=Package -- maximum total visits this package "
                   "covers, counting the purchase visit itself as visit 1. Leave blank for no "
                   "limit.",
    )
    validity_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Only meaningful for category=Package -- how many months after purchase this "
                   "package stays valid for reuse. Leave blank for no expiry.",
    )

    class Meta:
        ordering = ["category", "name"]
        unique_together = ("hospital", "name")

    def __str__(self):
        return f"{self.name} ({self.hospital.name})"


class VisitService(models.Model):
    REQUESTED_BY_SELF = "self"
    REQUESTED_BY_INTERNAL_DOCTOR = "internal_doctor"
    REQUESTED_BY_EXTERNAL_DOCTOR = "external_doctor"

    REQUESTED_BY_CHOICES = [
        (REQUESTED_BY_SELF, "Self-Requested"),
        (REQUESTED_BY_INTERNAL_DOCTOR, "Doctor (This Facility)"),
        (REQUESTED_BY_EXTERNAL_DOCTOR, "Doctor (Outside Facility)"),
    ]

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="visit_services")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="visit_services")
    price_at_time = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    is_approved = models.BooleanField(default=False, help_text="Whether this service has been approved (e.g., for payment) by reception")
    performed = models.BooleanField(default=False, help_text="Whether this service has been performed")
    created_at = models.DateTimeField(auto_now_add=True)
    performed_at = models.DateTimeField(null=True, blank=True)

    # Who asked for this service — mainly meaningful for lab tests, but kept
    # generic on VisitService rather than lab-specific, same as everything
    # else billing-related. A doctor ordering through their own consultation
    # already sets internal_doctor + requested_by_user = whoever's logged
    # in automatically; reception billing a service directly (no doctor in
    # the loop) is asked to pick self vs. an outside doctor's referral.
    requested_by_type = models.CharField(max_length=20, choices=REQUESTED_BY_CHOICES, blank=True)
    requested_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Set when requested_by_type is internal_doctor — the doctor who was logged in at request time.",
    )
    external_requester_name = models.CharField(
        max_length=150, blank=True,
        help_text="Set when requested_by_type is external_doctor — the referring doctor's name.",
    )
    external_requester_facility = models.CharField(
        max_length=150, blank=True,
        help_text="Set when requested_by_type is external_doctor — the referring doctor's facility.",
    )

    # Package coverage — same "still record the real price, just don't bill
    # it" shape as Prescription.covered_by_previous (doctor/models.py), just
    # for a service line covered by an active Package instead of a prior
    # payment. covering_package is usually a line on this same visit, but for
    # a Visit.TYPE_PACKAGE reuse visit it points at the original purchase's
    # line on an earlier visit instead (see Visit.package_source).
    covered_by_package = models.BooleanField(default=False)
    covering_package = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="covered_services",
        help_text="The package's own VisitService line (this visit or an earlier one) that covers "
                   "this one, when covered_by_package is set.",
    )
    package_expires_on = models.DateField(
        null=True, blank=True,
        help_text="Only meaningful when this line's service is category=Package -- optional expiry "
                   "captured at purchase time. Once set and past, this package can no longer be "
                   "reused on a new visit.",
    )
    # Follow-up visits: the doctor (or lab) chooses per service whether it's
    # billed or given free as part of the follow-up. Same shape as package
    # coverage -- the real price is still recorded, just not charged.
    free_follow_up = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.visit} - {self.service.name}"

    @property
    def billing_label(self):
        # No currency-code prefix -- receipts already show bare numbers
        # everywhere else (visit.total_amount, payments, ...), so this
        # matches that instead of Prescription.billing_label's own
        # convention, which is used in a different, "UGX "-prefixed context.
        if self.covered_by_package and self.covering_package_id:
            return f"Covered by {self.covering_package.service.name}"
        if self.free_follow_up:
            return "Free (follow-up)"
        return str(self.price_at_time)

    @property
    def is_charged(self):
        """Whether price_at_time is part of the visit's total_amount."""
        return not (self.covered_by_package or self.free_follow_up)

    @property
    def requested_by_display(self):
        if self.requested_by_type == self.REQUESTED_BY_SELF:
            return "Self-requested"
        if self.requested_by_type == self.REQUESTED_BY_INTERNAL_DOCTOR:
            if self.requested_by_user_id:
                return format_doctor_name(self.requested_by_user.get_full_name() or self.requested_by_user.username)
            return "Doctor (this facility)"
        if self.requested_by_type == self.REQUESTED_BY_EXTERNAL_DOCTOR:
            name = format_doctor_name(self.external_requester_name or "External doctor")
            if self.external_requester_facility:
                return f"{name} — {self.external_requester_facility}"
            return name
        return ""


class QueueEntry(models.Model):
    TYPE_LAB_RECEPTION = "lab_reception"
    TYPE_LAB_DOCTOR = "lab_doctor"
    TYPE_DOCTOR = "doctor"
    TYPE_NURSE = "nurse"
    TYPE_SONOGRAPHER = "sonographer"
    TYPE_RECEPTION = "reception"
    TYPE_PHLEBOTOMY = "phlebotomy"

    QUEUE_TYPE_CHOICES = [
        (TYPE_LAB_RECEPTION, "Lab Reception"),
        (TYPE_LAB_DOCTOR, "Lab Request From Doctor"),
        (TYPE_DOCTOR, "Doctor"),
        (TYPE_NURSE, "Nurse"),
        (TYPE_SONOGRAPHER, "Sonographer"),
        (TYPE_RECEPTION, "Reception Queue"),
        (TYPE_PHLEBOTOMY, "Phlebotomy"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="queue_entries")
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="queue_entries")
    queue_type = models.CharField(max_length=30, choices=QUEUE_TYPE_CHOICES)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    reason = models.TextField(blank=True, help_text="Why was this patient sent?")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_queue_entries",
    )
    # A doctor-queue entry made because lab results are back. Reception sees it
    # on its dashboard so the patient in the waiting area is told to go to the
    # doctor, until reception marks them told or the doctor sees them.
    results_ready = models.BooleanField(default=False)
    reception_told_at = models.DateTimeField(null=True, blank=True)
    reception_told_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["processed", "created_at", "id"]

    def __str__(self):
        return f"{self.visit.patient.name} - {self.queue_type}"


class Payment(models.Model):
    MODE_CASH = "cash"
    MODE_CARD = "card"
    MODE_MOBILE_MONEY = "mobile_money"
    MODE_INSURANCE = "insurance"

    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_PART_PAID = "part_paid"
    STATUS_WAIVED = "waived"

    MODE_CHOICES = [
        (MODE_CASH, "Cash"),
        (MODE_CARD, "Card"),
        (MODE_MOBILE_MONEY, "Mobile Money"),
        (MODE_INSURANCE, "Insurance"),
    ]

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PAID, "Paid"),
        (STATUS_PART_PAID, "Part Paid"),
        (STATUS_WAIVED, "Waived"),
    ]

    # Multiple receipts/payments can be recorded for the same visit (partial payments).
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
    )
    bank_account = models.ForeignKey(
        "admin_dashboard.BankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    mobile_account = models.ForeignKey(
        "admin_dashboard.MobileMoneyAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    notes = models.TextField(blank=True)
    # Sent with the payment form; the same form submitted twice (a double
    # click, a retried request) finds this and doesn't record a second payment.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.visit.patient.name} - {self.amount}"

    @property
    def receipt_number(self):
        stamp = timezone.localtime(self.paid_at or timezone.now()).strftime("%Y%m%d")
        suffix = f"{self.pk:06d}" if self.pk else "NEW"
        try:
            prefix = hospital_initials(self.visit.hospital.name)
        except Exception:
            prefix = "RCT"
        return f"{prefix}{stamp}-{suffix}"

    @property
    def balance_due(self):
        # Remaining balance is computed at the visit level (supports multiple receipts per visit).
        if self.visit_id:
            try:
                return self.visit.balance_due
            except Exception:
                pass
        return max(self.amount - self.amount_paid, Decimal("0"))

    def clean(self):
        super().clean()
        if self.amount_paid < 0:
            raise ValidationError({"amount_paid": "Amount paid cannot be negative."})
        if self.amount_paid > self.amount:
            raise ValidationError({"amount_paid": "Amount paid cannot exceed the billed amount."})
        if self.mode == self.MODE_CARD and not self.bank_account_id:
            raise ValidationError({"bank_account": "Bank account is required for card payments."})
        if self.mode != self.MODE_CARD:
            self.bank_account = None
        if self.mode == self.MODE_MOBILE_MONEY and not self.mobile_account_id:
            raise ValidationError({"mobile_account": "Mobile money account is required for mobile money payments."})
        if self.mode != self.MODE_MOBILE_MONEY:
            self.mobile_account = None

    @transaction.atomic
    def save(self, *args, **kwargs):
        """
        Per-receipt save logic.

        Payment records represent individual receipts. Partial payment is achieved by
        creating multiple Payment rows for the same Visit.
        """
        if self.status == self.STATUS_WAIVED:
            self.amount_paid = Decimal("0")
            self.paid_at = None
        else:
            if self.amount_paid > 0:
                self.status = self.STATUS_PAID
                self.paid_at = self.paid_at or timezone.now()
            else:
                self.status = self.STATUS_PENDING
                self.paid_at = None

        self.full_clean()
        super().save(*args, **kwargs)
        from admin_dashboard.models import mirror_cash, sync_hospital_account_balance

        sync_hospital_account_balance(self.visit.hospital)

        # Cash receipts show as cash in on the drawer (one per payment). Closed
        # drawers keep what was counted; see admin_dashboard.models.mirror_cash.
        is_cash = self.mode == self.MODE_CASH and self.paid_at and self.amount_paid > 0 and self.status != self.STATUS_WAIVED
        mirror_cash(
            {"payment": self}, self.visit.hospital,
            timezone.localdate(self.paid_at) if self.paid_at else timezone.localdate(),
            self.amount_paid if is_cash else Decimal("0"),
            f"Receipt {self.receipt_number} - {self.visit.patient.name}", inflow=True,
        )

        # Bank/Mobile statements are reconciled against external statement lines (BankTransaction / MobileMoneyTransaction).
        # We do not auto-create those statement lines from internal receipts; instead, reconciliation pages match
        # external credits against these Payment records via receipt references/amount/date.

    @transaction.atomic
    def delete(self, *args, **kwargs):
        hospital = self.visit.hospital
        from admin_dashboard.models import BankTransaction, MobileMoneyTransaction, mirror_cash

        # Bank and mobile-money statement lines are what the bank reported: they
        # stay, and just go back to being unmatched. (They used to be deleted.)
        BankTransaction.objects.filter(reconciled_with=self).update(reconciled_with=None, is_reconciled=False)
        MobileMoneyTransaction.objects.filter(reconciled_with=self).update(reconciled_with=None, is_reconciled=False)
        mirror_cash({"payment": self}, hospital, timezone.localdate(), Decimal("0"), f"Deleted receipt {self.receipt_number}", inflow=True)
        super().delete(*args, **kwargs)
        from admin_dashboard.models import sync_hospital_account_balance

        sync_hospital_account_balance(hospital)
