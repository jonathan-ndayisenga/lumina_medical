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
