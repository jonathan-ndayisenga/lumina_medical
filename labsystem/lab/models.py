from django.conf import settings
from django.db import models


class LabReport(models.Model):
    """Main lab report record."""
    patient_name = models.CharField(max_length=200)
    patient_age = models.CharField(max_length=20, help_text="e.g., 22YRS")
    patient_sex = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    sample_date = models.DateField()
    specimen_type = models.CharField(max_length=50, default='BLOOD')
    attendant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    attendant_name = models.CharField(max_length=100, blank=True, help_text="Lab attendant's name")
    comments = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    printed = models.BooleanField(default=False)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.patient_name} - {self.sample_date}"


class TestCatalog(models.Model):
    """Learned and suggested test names from prior report entry."""
    name = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=20, blank=True)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class ReferenceRangeDefault(models.Model):
    AGE_CATEGORIES = [
        ('neonate', 'Neonate (0-30 days)'),
        ('infant', 'Infant (1-6 months)'),
        ('child_1_5', 'Child (1-5 years)'),
        ('child_6_11', 'Child (6-11 years)'),
        ('child_12_17', 'Child (12-17 years)'),
        ('adult', 'Adult (18+ years)'),
    ]
    test = models.ForeignKey(TestCatalog, on_delete=models.CASCADE, related_name='default_ranges')
    age_category = models.CharField(max_length=20, choices=AGE_CATEGORIES)
    reference_range = models.CharField(max_length=50)
    unit = models.CharField(max_length=20, blank=True)

    class Meta:
        unique_together = ('test', 'age_category')

    def __str__(self):
        return f"{self.test.name} ({self.age_category}): {self.reference_range} {self.unit}"


class TestResult(models.Model):
    """Test result tied to catalog + stored range/unit."""
    lab_report = models.ForeignKey(LabReport, on_delete=models.CASCADE, related_name='results')
    test = models.ForeignKey(TestCatalog, on_delete=models.CASCADE)
    result_value = models.CharField(max_length=50)
    reference_range = models.CharField(max_length=50, blank=True)
    unit = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.test.name}: {self.result_value}"
