from django.conf import settings
from django.db import models


class BaseRefraction(models.Model):
    """The eye pre-exam: visual acuity, neuro/psych, pupils, refraction,
    keratometry, add and PD. Recorded by the doctor before the main exam.
    `data` holds every field by its form name (see ophthalmology.exam)."""

    visit = models.OneToOneField("reception.Visit", on_delete=models.CASCADE, related_name="base_refraction")
    data = models.JSONField(default=dict, blank=True)
    comments = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Base refraction - visit {self.visit_id}"


class EyeExam(models.Model):
    """The ophthalmology main exam. The visit outcome and follow-up date live
    on the shared doctor.Consultation saved alongside it, so outcomes and
    consultation reports cover eye visits too."""

    visit = models.OneToOneField("reception.Visit", on_delete=models.CASCADE, related_name="eye_exam")
    external_right = models.TextField(blank=True)
    external_left = models.TextField(blank=True)
    # {"cornea": {"right": ["Clear"], "left": ["Ulcer"], "right_other": "", "left_other": "..."}, ...}
    slit_lamp = models.JSONField(default=dict, blank=True)
    cdr_right = models.CharField(max_length=20, blank=True)
    cdr_left = models.CharField(max_length=20, blank=True)
    iop_right = models.CharField(max_length=20, blank=True)
    iop_left = models.CharField(max_length=20, blank=True)
    diagnoses = models.JSONField(default=list, blank=True)
    history_comments = models.TextField(blank=True)
    management_plan = models.TextField(blank=True)
    examined_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Eye exam - visit {self.visit_id}"
