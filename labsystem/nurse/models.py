from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum


class NurseNote(models.Model):
    visit = models.ForeignKey("reception.Visit", on_delete=models.CASCADE, related_name="nurse_notes")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nurse_notes",
    )
    notes = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Nurse Note - {self.visit.patient.name}"


class NursingAdmission(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_DISCHARGED = "discharged"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_DISCHARGED, "Discharged"),
    ]

    visit = models.OneToOneField(
        "reception.Visit",
        on_delete=models.CASCADE,
        related_name="nursing_admission",
    )
    hospital = models.ForeignKey(
        "accounts.Hospital",
        on_delete=models.CASCADE,
        related_name="nursing_admissions",
    )
    admitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nursing_admissions_started",
    )
    admitted_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    discharged_at = models.DateTimeField(null=True, blank=True)
    discharged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nursing_discharges",
    )
    discharge_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-admitted_at"]

    def __str__(self):
        return f"Nursing Admission — {self.visit.patient.name} ({self.admitted_at.date()})"

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE


class NursingCareItem(models.Model):
    admission = models.ForeignKey(
        NursingAdmission,
        on_delete=models.CASCADE,
        related_name="care_items",
    )
    prescription = models.OneToOneField(
        "doctor.Prescription",
        on_delete=models.CASCADE,
        related_name="nursing_care_item",
    )
    doses_planned = models.PositiveIntegerField()
    per_dose_quantity = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal("0"))
    is_active = models.BooleanField(default=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stopped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stopped_care_items",
    )
    stop_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-is_active", "id"]

    def __str__(self):
        return f"{self.prescription.drug.name} — {self.doses_given}/{self.doses_planned} doses"

    @property
    def doses_given(self):
        return self.doses.count()

    @property
    def quantity_given_total(self):
        return self.doses.aggregate(total=Sum("quantity_given"))["total"] or Decimal("0")

    @property
    def is_complete(self):
        return self.doses_given >= self.doses_planned

    @property
    def doses_remaining(self):
        return max(0, self.doses_planned - self.doses_given)

    @property
    def progress_pct(self):
        if not self.doses_planned:
            return 0
        return min(100, int((self.doses_given / self.doses_planned) * 100))


class ScanReport(models.Model):
    SCAN_ABDOMINAL = "abdominal"
    SCAN_OBSTETRIC = "obstetric"
    SCAN_PELVIC = "pelvic"
    SCAN_CARDIAC = "cardiac"
    SCAN_RENAL = "renal"
    SCAN_THYROID = "thyroid"
    SCAN_MUSCULOSKELETAL = "musculoskeletal"
    SCAN_OTHER = "other"

    SCAN_TYPE_CHOICES = [
        (SCAN_ABDOMINAL, "Abdominal Ultrasound"),
        (SCAN_OBSTETRIC, "Obstetric Ultrasound"),
        (SCAN_PELVIC, "Pelvic Ultrasound"),
        (SCAN_CARDIAC, "Cardiac Ultrasound (Echo)"),
        (SCAN_RENAL, "Renal Ultrasound"),
        (SCAN_THYROID, "Thyroid Ultrasound"),
        (SCAN_MUSCULOSKELETAL, "Musculoskeletal Ultrasound"),
        (SCAN_OTHER, "Other"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_FINAL = "final"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_FINAL, "Finalized"),
    ]

    visit = models.ForeignKey(
        "reception.Visit",
        on_delete=models.CASCADE,
        related_name="scan_reports",
    )
    sonographer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_reports",
    )
    scan_type = models.CharField(max_length=30, choices=SCAN_TYPE_CHOICES, default=SCAN_OTHER)
    clinical_indication = models.TextField(blank=True, help_text="Reason for the scan / clinical notes from referring doctor")
    findings = models.TextField(help_text="Detailed scan findings")
    impression = models.TextField(help_text="Summary / conclusion")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Scan Report — {self.visit.patient.name} ({self.get_scan_type_display()})"


class NursingDose(models.Model):
    care_item = models.ForeignKey(
        NursingCareItem,
        on_delete=models.CASCADE,
        related_name="doses",
    )
    administered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nursing_doses_given",
    )
    administered_at = models.DateTimeField(auto_now_add=True)
    quantity_given = models.DecimalField(max_digits=10, decimal_places=4)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["administered_at"]

    def __str__(self):
        nurse = self.administered_by.get_full_name() if self.administered_by else "Unknown"
        return f"Dose {self.care_item.prescription.drug.name} — {nurse} @ {self.administered_at:%Y-%m-%d %H:%M}"
