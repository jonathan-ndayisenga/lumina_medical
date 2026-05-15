from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounts.models import Hospital


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
    age = models.CharField(max_length=20, help_text="Examples: 22YRS, 6MTH")
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


class Visit(models.Model):
    TYPE_NORMAL = "normal"
    TYPE_FOLLOW_UP = "follow_up"
    TYPE_ADJUSTMENT = "adjustment"

    VISIT_TYPE_CHOICES = [
        (TYPE_NORMAL, "Normal Visit"),
        (TYPE_FOLLOW_UP, "Follow-up"),
        (TYPE_ADJUSTMENT, "Adjustment Visit (Medication Swap)"),
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
    def is_adjustment_visit(self):
        return self.visit_type == self.TYPE_ADJUSTMENT

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
            # Follow-up visits MUST have a valid completed parent visit and a consultation
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
            
            # Follow-up MUST have services with at least one consultation
            service_count = self.visit_services.count()
            if service_count == 0:
                raise ValidationError(
                    "Follow-up visits must have at least one service selection."
                )
            
            has_consultation = self.visit_services.filter(
                service__category=Service.CATEGORY_CONSULTATION
            ).exists()
            if not has_consultation:
                raise ValidationError(
                    "Follow-up visits must include a doctor consultation service."
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
    CATEGORY_OTHER = "other"

    CATEGORY_CHOICES = [
        (CATEGORY_CONSULTATION, "Consultation"),
        (CATEGORY_LAB, "Laboratory"),
        (CATEGORY_TRIAGE, "Triage"),
        (CATEGORY_PROCEDURE, "Procedure"),
        (CATEGORY_PHARMACY, "Pharmacy"),
        (CATEGORY_OTHER, "Other"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="services")
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    test_profile = models.ForeignKey(
        "lab.TestProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="services",
        help_text="Lab service linked to a test profile (e.g., CBC, Urinalysis)"
    )

    class Meta:
        ordering = ["category", "name"]
        unique_together = ("hospital", "name")

    def __str__(self):
        return f"{self.name} ({self.hospital.name})"


class VisitService(models.Model):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="visit_services")
    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="visit_services")
    price_at_time = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    is_approved = models.BooleanField(default=False, help_text="Whether this service has been approved (e.g., for payment) by reception")
    performed = models.BooleanField(default=False, help_text="Whether this service has been performed")
    created_at = models.DateTimeField(auto_now_add=True)
    performed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.visit} - {self.service.name}"


class QueueEntry(models.Model):
    TYPE_LAB_RECEPTION = "lab_reception"
    TYPE_LAB_DOCTOR = "lab_doctor"
    TYPE_DOCTOR = "doctor"
    TYPE_NURSE = "nurse"
    TYPE_RECEPTION = "reception"

    QUEUE_TYPE_CHOICES = [
        (TYPE_LAB_RECEPTION, "Lab Reception"),
        (TYPE_LAB_DOCTOR, "Lab Request From Doctor"),
        (TYPE_DOCTOR, "Doctor"),
        (TYPE_NURSE, "Nurse"),
        (TYPE_RECEPTION, "Reception Queue"),
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
        return f"RCT-{stamp}-{suffix}"

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
            paid_date = (self.paid_at or timezone.now()).date()
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
