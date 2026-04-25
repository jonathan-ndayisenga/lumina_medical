from django.conf import settings
from django.db import models


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
