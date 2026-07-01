from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from reception.models import hospital_initials


class HomeCareNurse(models.Model):
    hospital = models.ForeignKey(
        "accounts.Hospital",
        on_delete=models.CASCADE,
        related_name="homecare_nurses",
    )
    name = models.CharField(max_length=200)
    age = models.PositiveIntegerField()
    tribe = models.CharField(max_length=100, blank=True)
    religion = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=255)
    qualification = models.CharField(max_length=200)
    nin = models.CharField(max_length=50, blank=True, verbose_name="NIN")
    contact = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homecare_nurses_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def active_placement(self):
        return self.placements.filter(status=HomeCarePlacement.STATUS_ACTIVE).first()


class HomeCareClient(models.Model):
    hospital = models.ForeignKey(
        "accounts.Hospital",
        on_delete=models.CASCADE,
        related_name="homecare_clients",
    )
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=255)
    contact = models.CharField(max_length=20)
    nin = models.CharField(max_length=50, blank=True, verbose_name="NIN")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homecare_clients_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class HomeCarePlacement(models.Model):
    SERVICE_LIVE_IN = "live_in"
    SERVICE_LIVE_OUT = "live_out"
    SERVICE_CHOICES = [
        (SERVICE_LIVE_IN, "Live-In (24 hours)"),
        (SERVICE_LIVE_OUT, "Live-Out (10 hours)"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_COMPLETED = "completed"
    STATUS_TERMINATED = "terminated"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_TERMINATED, "Terminated"),
    ]

    hospital = models.ForeignKey(
        "accounts.Hospital",
        on_delete=models.CASCADE,
        related_name="homecare_placements",
    )
    client = models.ForeignKey(
        HomeCareClient,
        on_delete=models.PROTECT,
        related_name="placements",
    )
    nurse = models.ForeignKey(
        HomeCareNurse,
        on_delete=models.PROTECT,
        related_name="placements",
    )
    RATE_PER_DAY = "day"
    RATE_PER_WEEK = "week"
    RATE_PER_MONTH = "month"
    RATE_PERIOD_CHOICES = [
        (RATE_PER_DAY, "Per Day"),
        (RATE_PER_WEEK, "Per Week"),
        (RATE_PER_MONTH, "Per Month"),
    ]

    service_type = models.CharField(max_length=20, choices=SERVICE_CHOICES)
    rate_period = models.CharField(
        max_length=20, choices=RATE_PERIOD_CHOICES, default=RATE_PER_MONTH,
        help_text="How often the rates are charged.",
    )
    nurse_rate = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Amount paid to the nurse (per selected rate period).",
    )
    client_rate = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Amount charged to the client (per selected rate period).",
    )
    contract_start = models.DateField()
    contract_end = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homecare_placements_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client.name} -> {self.nurse.name} ({self.get_service_type_display()})"

    @property
    def total_billed(self):
        return self.receipts.aggregate(total=models.Sum("amount_paid"))["total"] or Decimal("0")

    @property
    def balance_due(self):
        return max(self.client_rate - self.total_billed, Decimal("0"))

    @property
    def has_outstanding_balance(self):
        return self.total_billed > 0 and self.balance_due > 0

    @property
    def margin(self):
        return self.client_rate - self.nurse_rate


class HomeCareContract(models.Model):
    placement = models.OneToOneField(
        HomeCarePlacement,
        on_delete=models.CASCADE,
        related_name="contract",
    )
    contract_number = models.CharField(max_length=50, unique=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    terms_snapshot = models.TextField(
        blank=True,
        help_text="Frozen copy of placement terms at generation — preserved even if rates are later edited.",
    )

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return self.contract_number

    def save(self, *args, **kwargs):
        if not self.contract_number:
            self.contract_number = self._generate_number()
        if not self.terms_snapshot:
            self.terms_snapshot = self._snapshot()
        super().save(*args, **kwargs)

    def _generate_number(self):
        from django.utils.timezone import now as _now
        stamp = _now().strftime("%Y%m%d")
        try:
            prefix = hospital_initials(self.placement.hospital.name, fallback="CON")
        except Exception:
            prefix = "CON"
        last = HomeCareContract.objects.filter(contract_number__startswith=f"{prefix}{stamp}").count()
        return f"{prefix}{stamp}-{str(last + 1).zfill(4)}"

    def _snapshot(self):
        p = self.placement
        return (
            f"Client: {p.client.name} | Nurse: {p.nurse.name} | "
            f"Service: {p.get_service_type_display()} | "
            f"Nurse Rate: {p.nurse_rate}/mo | Client Rate: {p.client_rate}/mo | "
            f"Period: {p.contract_start} to {p.contract_end}"
        )


class HomeCareReceipt(models.Model):
    placement = models.ForeignKey(
        HomeCarePlacement,
        on_delete=models.CASCADE,
        related_name="receipts",
    )
    receipt_number = models.CharField(max_length=50, unique=True)
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    period_covered = models.CharField(
        max_length=100, blank=True,
        help_text="e.g. 'July 2026' or 'Jul 1–31 2026'",
    )
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="homecare_receipts_recorded",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return self.receipt_number

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self):
        from django.utils.timezone import now as _now
        stamp = _now().strftime("%Y%m%d")
        try:
            prefix = hospital_initials(self.placement.hospital.name, fallback="HCR")
        except Exception:
            prefix = "HCR"
        last = HomeCareReceipt.objects.filter(receipt_number__startswith=f"{prefix}{stamp}").count()
        return f"{prefix}{stamp}-{str(last + 1).zfill(4)}"
