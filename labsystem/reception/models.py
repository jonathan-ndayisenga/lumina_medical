from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import Hospital


def hospital_initials(name: str, fallback: str = "RCT") -> str:
    """Extract uppercase initials from a hospital name.
    'Lumina Medical Services' -> 'LMS'
    'Mercy Hospital'          -> 'MH'
    'Home_care'               -> 'HC'  (splits on underscore/hyphen too)
    Words like 'and', 'the', 'of' are skipped.
    """
    import re
    stop = {"and", "the", "of", "a", "an", "for", "in", "at", "by"}
    # Split on spaces, underscores, and hyphens
    parts = re.split(r"[\s_\-]+", name or "")
    initials = "".join(
        w[0].upper() for w in parts if w and w.lower() not in stop and w[0].isalpha()
    )
    return initials or fallback


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

    def _age_string(self, reference_date):
        """Human-readable age from date_of_birth vs reference_date.
        Falls back to stored `age` CharField when DOB is unavailable."""
        if not self.date_of_birth:
            return self.age
        dob = self.date_of_birth
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
        d = visit_date.date() if hasattr(visit_date, "date") else visit_date
        return self._age_string(d)


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

    class Meta:
        ordering = ["-visit_date"]

    def __str__(self):
        return f"{self.patient.name} - {self.visit_date:%Y-%m-%d %H:%M}"

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
            # queue — no consultation fee or any other service is billed.
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
            if self.visit_services.exists():
                raise ValidationError(
                    "Follow-up visits cannot include new billable services."
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
    glucose_mg_dl = models.IntegerField(null=True, blank=True)
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

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
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
        return str(self.price_at_time)

    @property
    def requested_by_display(self):
        if self.requested_by_type == self.REQUESTED_BY_SELF:
            return "Self-requested"
        if self.requested_by_type == self.REQUESTED_BY_INTERNAL_DOCTOR:
            if self.requested_by_user_id:
                return f"Dr. {self.requested_by_user.get_full_name() or self.requested_by_user.username}"
            return "Doctor (this facility)"
        if self.requested_by_type == self.REQUESTED_BY_EXTERNAL_DOCTOR:
            name = self.external_requester_name or "External doctor"
            if self.external_requester_facility:
                return f"Dr. {name} — {self.external_requester_facility}"
            return f"Dr. {name}"
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

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.visit.patient.name} - {self.amount}"

    @property
    def receipt_number(self):
        stamp = (self.paid_at or timezone.now()).strftime("%Y%m%d")
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
        from admin_dashboard.models import (
            BankTransaction,
            CashDrawer,
            CashTransaction,
            MobileMoneyTransaction,
            sync_hospital_account_balance,
        )

        sync_hospital_account_balance(self.visit.hospital)

        # If a cash drawer is open, mirror cash receipts as drawer cash-in transactions (idempotent per payment).
        if self.mode == self.MODE_CASH and self.paid_at and self.amount_paid > 0 and self.status != self.STATUS_WAIVED:
            paid_date = timezone.localdate(self.paid_at)
            open_drawer = CashDrawer.objects.filter(hospital=self.visit.hospital, date=paid_date).order_by("-id").first()
            if not open_drawer:
                last_with_balance = (
                    CashDrawer.objects.filter(hospital=self.visit.hospital, closing_balance__isnull=False)
                    .order_by("-date", "-id")
                    .first()
                )
                opening = (
                    last_with_balance.closing_balance
                    if last_with_balance and last_with_balance.closing_balance is not None
                    else Decimal("0")
                )
                open_drawer = CashDrawer.objects.create(
                    hospital=self.visit.hospital,
                    date=paid_date,
                    opening_balance=opening,
                )
            if open_drawer:
                existing = (
                    CashTransaction.objects.filter(
                        payment=self,
                        transaction_type=CashTransaction.TYPE_CASH_IN,
                    )
                    .order_by("id")
                    .first()
                )
                description = f"Receipt {self.receipt_number} - {self.visit.patient.name}"
                if existing:
                    if existing.cash_drawer_id != open_drawer.id:
                        existing.cash_drawer = open_drawer
                    existing.amount = self.amount_paid
                    existing.description = description
                    existing.save(update_fields=["cash_drawer", "amount", "description"])
                else:
                    CashTransaction.objects.create(
                        cash_drawer=open_drawer,
                        payment=self,
                        amount=self.amount_paid,
                        transaction_type=CashTransaction.TYPE_CASH_IN,
                        description=description,
                    )
        else:
            CashTransaction.objects.filter(payment=self).delete()

        # Bank/Mobile statements are reconciled against external statement lines (BankTransaction / MobileMoneyTransaction).
        # We do not auto-create those statement lines from internal receipts; instead, reconciliation pages match
        # external credits against these Payment records via receipt references/amount/date.

    def delete(self, *args, **kwargs):
        hospital = self.visit.hospital
        from admin_dashboard.models import BankTransaction, MobileMoneyTransaction

        BankTransaction.objects.filter(reconciled_with=self).delete()
        MobileMoneyTransaction.objects.filter(reconciled_with=self).delete()
        super().delete(*args, **kwargs)
        from admin_dashboard.models import sync_hospital_account_balance

        sync_hospital_account_balance(hospital)
