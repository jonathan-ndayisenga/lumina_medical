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
