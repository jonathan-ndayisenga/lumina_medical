from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.templatetags.static import static
from django.db import models
from django.utils.functional import cached_property


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=100, unique=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2)
    max_users = models.PositiveIntegerField(default=10)
    max_storage_mb = models.PositiveIntegerField(default=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Hospital(models.Model):
    name = models.CharField(max_length=200)
    subdomain = models.CharField(max_length=100, unique=True)
    location = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    box_number = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="hospital_logos/", blank=True, null=True)
    tagline = models.CharField(max_length=120, blank=True)
    subscription_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hospitals",
    )
    is_active = models.BooleanField(default=True)
    subscription_end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def logo_url(self):
        if self.logo:
            try:
                return self.logo.url
            except ValueError:
                pass
        return static("images/default_hospital_logo.png")

    @cached_property
    def active_module_codes(self):
        return set(
            self.module_subscriptions.filter(is_active=True).values_list("module__code", flat=True)
        )

    def has_module(self, code):
        return code in self.active_module_codes


class Module(models.Model):
    """A sellable platform module (Reception, Doctor, Lab, Inventory, Finance, etc.)."""

    CODE_RECEPTION = "reception"
    CODE_DOCTOR = "doctor"
    CODE_NURSE = "nurse"
    CODE_LAB = "lab"
    CODE_INVENTORY = "inventory"
    CODE_FINANCE = "finance"

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_core = models.BooleanField(
        default=False,
        help_text="Core modules (e.g. Reception) are included with every hospital and cannot be unselected.",
    )
    url_name = models.CharField(max_length=100, blank=True, help_text="Django URL name for this module's sidebar entry.")
    icon_svg = models.TextField(blank=True, help_text="Inner SVG path markup for the sidebar icon.")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name


class HospitalModuleSubscription(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="module_subscriptions")
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="hospital_subscriptions")
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("hospital", "module")
        ordering = ["module__display_order"]

    def __str__(self):
        return f"{self.hospital.name} — {self.module.name}"


class User(AbstractUser):
    ROLE_SUPERADMIN = "superadmin"
    ROLE_HOSPITAL_ADMIN = "hospital_admin"
    ROLE_ACCOUNTANT = "accountant"
    ROLE_RECEPTIONIST = "receptionist"
    ROLE_LAB_ATTENDANT = "lab_attendant"
    ROLE_DOCTOR = "doctor"
    ROLE_NURSE = "nurse"

    ROLE_CHOICES = [
        (ROLE_SUPERADMIN, "Super Admin"),
        (ROLE_HOSPITAL_ADMIN, "Hospital Admin"),
        (ROLE_ACCOUNTANT, "Accountant"),
        (ROLE_RECEPTIONIST, "Receptionist"),
        (ROLE_LAB_ATTENDANT, "Lab Attendant"),
        (ROLE_DOCTOR, "Doctor"),
        (ROLE_NURSE, "Nurse"),
    ]

    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_LAB_ATTENDANT)

    class Meta:
        ordering = ["username"]

    @property
    def is_superadmin(self):
        return self.is_superuser or self.role == self.ROLE_SUPERADMIN

    @property
    def is_hospital_admin(self):
        return self.role == self.ROLE_HOSPITAL_ADMIN

    @cached_property
    def module_group_names(self):
        return set(self.groups.values_list("name", flat=True))

    def has_module_group(self, name):
        return name in self.module_group_names

    @property
    def can_access_hospital_admin(self):
        if self.is_superadmin:
            return True
        if self.role != self.ROLE_HOSPITAL_ADMIN:
            return False
        return self._hospital_has_module("hospital_mgmt")

    def _hospital_has_module(self, code):
        """Superadmins bypass module gating entirely; everyone else needs their hospital subscribed."""
        if self.is_superadmin:
            return True
        if not self.hospital_id:
            return False
        return self.hospital.has_module(code)

    @property
    def can_access_reception(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.role == self.ROLE_RECEPTIONIST or self.has_module_group("Reception")
        return eligible and self._hospital_has_module("reception")

    @property
    def can_access_doctor(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.role == self.ROLE_DOCTOR or self.has_module_group("Doctor")
        return eligible and self._hospital_has_module("doctor")

    @property
    def can_access_nurse(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.role == self.ROLE_NURSE or self.has_module_group("Nurse")
        return eligible and self._hospital_has_module("nurse")

    @property
    def can_access_lab(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.role == self.ROLE_LAB_ATTENDANT or self.has_module_group("Lab")
        return eligible and self._hospital_has_module("lab")

    @property
    def can_access_inventory(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.has_module_group("Inventory")
        return eligible and self._hospital_has_module("inventory")

    @property
    def is_accountant(self):
        return self.role == self.ROLE_ACCOUNTANT

    @property
    def can_access_finance(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.is_accountant or self.has_module_group("Finance")
        return eligible and self._hospital_has_module("finance")

    @property
    def can_access_home_care(self):
        if self.is_superadmin:
            return True
        eligible = self.is_hospital_admin or self.has_module_group("Home Care")
        return eligible and self._hospital_has_module("home_care")

    def get_full_name(self):
        full = f"{self.first_name} {self.last_name}".strip()
        if self.role == self.ROLE_DOCTOR and full and not full.startswith("Dr."):
            return f"Dr. {full}"
        return full or self.username

    @property
    def navigation_role_labels(self):
        labels = []
        if self.is_superadmin:
            labels.append("Super Admin")
        elif self.role:
            labels.append(self.get_role_display())

        group_label_map = {
            "Reception": "Reception",
            "Doctor": "Doctor",
            "Nurse": "Nurse",
            "Lab": "Lab",
            "Inventory": "Inventory",
            "Finance": "Finance",
            "Home Care": "Home Care",
        }
        for group_name in ("Reception", "Doctor", "Nurse", "Lab", "Inventory", "Finance", "Home Care"):
            label = group_label_map[group_name]
            if self.has_module_group(group_name) and label not in labels:
                labels.append(label)
        return labels

    def save(self, *args, **kwargs):
        # Treat Django superusers as platform superadmins even if the role field
        # was left at its default during createsuperuser or legacy account setup.
        if self.is_superuser or self.role == self.ROLE_SUPERADMIN:
            self.role = self.ROLE_SUPERADMIN
            self.hospital = None
            self.is_staff = True
            self.is_superuser = True
        elif self.role in {
            self.ROLE_HOSPITAL_ADMIN,
            self.ROLE_ACCOUNTANT,
            self.ROLE_RECEPTIONIST,
            self.ROLE_LAB_ATTENDANT,
            self.ROLE_DOCTOR,
            self.ROLE_NURSE,
        }:
            self.is_staff = True
            self.is_superuser = False
        super().save(*args, **kwargs)


class AuditLog(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=100)
    details = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.action} - {self.model_name}"


class HospitalSubscriptionPayment(models.Model):
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        related_name="subscription_payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    months_paid = models.PositiveIntegerField(default=1)
    period_start = models.DateField()
    period_end = models.DateField()
    paid_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    receipt_number = models.CharField(max_length=50, unique=True, blank=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.hospital.name} - {self.amount}"

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            from django.utils.timezone import now as _now
            stamp = _now().strftime("%Y%m%d")
            last = HospitalSubscriptionPayment.objects.filter(
                receipt_number__startswith=f"TH-RCT-{stamp}"
            ).count()
            self.receipt_number = f"TH-RCT-{stamp}-{str(last + 1).zfill(4)}"
        super().save(*args, **kwargs)


class HospitalInvoice(models.Model):
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    generated_at = models.DateTimeField(auto_now_add=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices_generated",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.invoice_number} — {self.hospital.name}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            from django.utils.timezone import now as _now
            stamp = _now().strftime("%Y%m%d")
            last = HospitalInvoice.objects.filter(
                invoice_number__startswith=f"INV-{stamp}"
            ).count()
            self.invoice_number = f"INV-{stamp}-{str(last + 1).zfill(4)}"
        super().save(*args, **kwargs)
