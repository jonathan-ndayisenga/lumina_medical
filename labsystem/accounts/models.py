from django.contrib.auth.models import AbstractUser
from django.templatetags.static import static
from django.db import models


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
    box_number = models.CharField(max_length=50, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="hospital_logos/", blank=True, null=True)
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


class User(AbstractUser):
    ROLE_SUPERADMIN = "superadmin"
    ROLE_HOSPITAL_ADMIN = "hospital_admin"
    ROLE_RECEPTIONIST = "receptionist"
    ROLE_LAB_ATTENDANT = "lab_attendant"
    ROLE_DOCTOR = "doctor"
    ROLE_NURSE = "nurse"

    ROLE_CHOICES = [
        (ROLE_SUPERADMIN, "Super Admin"),
        (ROLE_HOSPITAL_ADMIN, "Hospital Admin"),
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
        return self.role == self.ROLE_SUPERADMIN

    @property
    def is_hospital_admin(self):
        return self.role == self.ROLE_HOSPITAL_ADMIN

    def save(self, *args, **kwargs):
        if self.role == self.ROLE_SUPERADMIN:
            self.hospital = None
            self.is_staff = True
            self.is_superuser = True
        elif self.role in {
            self.ROLE_HOSPITAL_ADMIN,
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
    period_start = models.DateField()
    period_end = models.DateField()
    paid_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.hospital.name} - {self.amount}"
