from decimal import Decimal, ROUND_CEILING

from django.conf import settings
from django.db import models
from django.utils import timezone


class Consultation(models.Model):
    visit = models.OneToOneField("reception.Visit", on_delete=models.CASCADE, related_name="consultation")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    vitals = models.JSONField(default=dict, blank=True)
    signs_symptoms = models.TextField()
    diagnosis = models.TextField()
    treatment = models.TextField()
    lab_requests = models.JSONField(default=list, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Consultation - {self.visit.patient.name}"


class LabRequest(models.Model):
    """Lab request created by doctor or receptionist"""
    
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
    ]
    
    URGENCY_ROUTINE = "routine"
    URGENCY_URGENT = "urgent"
    
    URGENCY_CHOICES = [
        (URGENCY_ROUTINE, "Routine"),
        (URGENCY_URGENT, "Urgent"),
    ]
    
    REQUESTED_BY_DOCTOR = "doctor"
    REQUESTED_BY_RECEPTIONIST = "receptionist"
    
    REQUESTED_BY_CHOICES = [
        (REQUESTED_BY_DOCTOR, "Doctor"),
        (REQUESTED_BY_RECEPTIONIST, "Receptionist"),
    ]
    
    visit = models.ForeignKey(
        "reception.Visit",
        on_delete=models.CASCADE,
        related_name="lab_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_lab_requests",
    )
    requested_by_role = models.CharField(max_length=20, choices=REQUESTED_BY_CHOICES)
    tests_requested = models.TextField(help_text="Specific tests or clinical procedures requested")
    clinical_notes = models.TextField(blank=True, help_text="Reason for request and clinical context")
    urgency = models.CharField(max_length=20, choices=URGENCY_CHOICES, default=URGENCY_ROUTINE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-created_at"]
    
    def __str__(self):
        return f"Lab Request - {self.visit.patient.name} - {self.tests_requested[:30]}"


class Notification(models.Model):
    """System notifications for users"""
    
    TYPE_LAB_RESULT = "lab_result"
    TYPE_LAB_REQUEST = "lab_request"
    TYPE_MESSAGE = "message"
    
    TYPE_CHOICES = [
        (TYPE_LAB_RESULT, "Lab Result Available"),
        (TYPE_LAB_REQUEST, "New Lab Request"),
        (TYPE_MESSAGE, "Message"),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    reference_id = models.PositiveIntegerField(null=True, blank=True, help_text="ID of related object (lab report, lab request, etc)")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ["-created_at"]
    
    def __str__(self):
        return f"{self.get_notification_type_display()} - {self.user.username}"
    
    def mark_as_read(self):
        self.is_read = True
        self.save(update_fields=["is_read"])


class Prescription(models.Model):
    visit = models.ForeignKey("reception.Visit", on_delete=models.CASCADE, related_name="prescriptions")
    drug = models.ForeignKey(
        "admin_dashboard.InventoryItem",
        on_delete=models.CASCADE,
        related_name="prescriptions",
    )
    dosage_mg = models.DecimalField(max_digits=8, decimal_places=2, help_text="Dose per intake, interpreted from the medicine form.")
    frequency_per_day = models.PositiveIntegerField(help_text="How many times per day.")
    duration_days = models.PositiveIntegerField(default=1)
    total_quantity = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    number_of_packs = models.PositiveIntegerField(default=0, editable=False)
    total_price = models.DecimalField(max_digits=10, decimal_places=2, editable=False, default=0)
    notes = models.TextField(blank=True)
    prescribed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions_created",
    )
    prescribed_at = models.DateTimeField(auto_now_add=True)
    dispensed = models.BooleanField(default=False)
    dispensed_at = models.DateTimeField(null=True, blank=True)
    dispensed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispensed_prescriptions",
    )
    billing_visit_service = models.ForeignKey(
        "reception.VisitService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prescriptions",
    )

    class Meta:
        ordering = ["-prescribed_at", "-id"]

    def __str__(self):
        return f"{self.drug.name} for {self.visit.patient.name}"

    @property
    def is_liquid(self):
        return self.drug.category in {
            "syrup",
            "iv",
            "im",
        }

    @property
    def is_tube(self):
        return self.drug.category == "tube"

    def _display_quantity(self, value):
        value = Decimal(value or 0)
        return str(int(value)) if value == value.to_integral_value() else f"{value.normalize()}"

    @property
    def quantity_display(self):
        if self.is_liquid and self.number_of_packs:
            total_ml = (Decimal(self.dosage_mg or 0) * Decimal(self.frequency_per_day or 0) * Decimal(self.duration_days or 0)).quantize(Decimal("0.01"))
            return f"{self.number_of_packs} {self.drug.unit}(s) covering {self._display_quantity(total_ml)} ml"
        if self.is_tube:
            return f"{self.number_of_packs} tube(s)"
        return f"{self._display_quantity(self.total_quantity)} {self.drug.unit}(s)"

    @property
    def regimen_display(self):
        dose_unit = "mg"
        if self.is_liquid:
            dose_unit = "ml"
        elif self.is_tube:
            dose_unit = "application unit(s)"
        return f"{self.dosage_mg} {dose_unit} x {self.frequency_per_day}/day for {self.duration_days} day(s)"

    def calculate_totals(self):
        frequency = Decimal(self.frequency_per_day or 0)
        duration = Decimal(self.duration_days or 0)
        dosage = Decimal(self.dosage_mg or 0)
        selling_price = Decimal(self.drug.selling_price or 0)

        if self.is_liquid:
            units_per_pack = Decimal(self.drug.units_per_pack or 0)
            if units_per_pack <= 0:
                self.total_quantity = Decimal("0")
                self.number_of_packs = 0
                self.total_price = Decimal("0")
                return

            total_ml = dosage * frequency * duration
            packs = (total_ml / units_per_pack).quantize(Decimal("1"), rounding=ROUND_CEILING) if total_ml else Decimal("0")
            self.total_quantity = packs.quantize(Decimal("0.01"))
            self.number_of_packs = int(packs)
            self.total_price = (Decimal(self.number_of_packs) * selling_price).quantize(Decimal("0.01"))
            return

        if self.is_tube:
            coverage_days = Decimal(self.drug.days_covered_per_pack or 1)
            packs = (duration / coverage_days).quantize(Decimal("1"), rounding=ROUND_CEILING) if duration else Decimal("0")
            self.number_of_packs = int(packs)
            self.total_quantity = Decimal(self.number_of_packs).quantize(Decimal("0.01"))
            self.total_price = (Decimal(self.number_of_packs) * selling_price).quantize(Decimal("0.01"))
            return

        strength = Decimal(self.drug.strength_mg_per_unit or 0)
        if strength > 0:
            total_units = ((dosage / strength) * frequency * duration).quantize(Decimal("1"), rounding=ROUND_CEILING)
        else:
            total_units = (frequency * duration).quantize(Decimal("1"), rounding=ROUND_CEILING)
        self.total_quantity = total_units
        self.number_of_packs = int(total_units) if total_units else 0
        self.total_price = (self.total_quantity * selling_price).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        self.calculate_totals()
        super().save(*args, **kwargs)
