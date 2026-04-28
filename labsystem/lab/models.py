from django.conf import settings
from django.db import models

from accounts.models import Hospital


class TestProfile(models.Model):
    """Reusable starter templates such as CBC or urinalysis."""

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=50, unique=True)
    default_specimen_type = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class LabReport(models.Model):
    """Main lab report record."""

    profile = models.ForeignKey(
        TestProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reports',
    )
    lab_request = models.ForeignKey(
        'doctor.LabRequest',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reports',
        help_text="Link to the original lab request"
    )
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lab_reports',
    )
    visit = models.ForeignKey(
        'reception.Visit',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lab_reports',
    )
    requested_visit_service = models.OneToOneField(
        'reception.VisitService',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lab_report',
        help_text="The specific requested visit service this report satisfies when tests are worked one-by-one.",
    )
    patient_name = models.CharField(max_length=200)
    patient_age = models.CharField(max_length=20, help_text="e.g., 22YRS")
    patient_sex = models.CharField(max_length=10, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')])
    referred_by = models.CharField(max_length=150, blank=True)
    sample_date = models.DateField()
    specimen_type = models.CharField(max_length=50, default='BLOOD')
    attendant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    attendant_name = models.CharField(max_length=100, blank=True, help_text="Lab attendant's name")
    comments = models.TextField(blank=True)
    sent_to_doctor = models.BooleanField(default=False, help_text="Whether results have been sent to requesting doctor")
    sent_to_doctor_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    printed = models.BooleanField(default=False)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.patient_name} - {self.sample_date}"

    @property
    def template_label(self) -> str:
        """
        UI label for report list/detail headers.

        The report can contain rows loaded from multiple templates (see TestResult.source_profile).
        In that case, showing a single LabReport.profile name is misleading (it gets overwritten
        by the last-loaded template). We therefore derive the label from the stored rows.
        """
        # Use prefetched results when available to avoid N+1 queries.
        results = None
        cache = getattr(self, "_prefetched_objects_cache", {}) or {}
        if "results" in cache:
            results = cache["results"]
        if results is None:
            results = self.results.select_related("source_profile").all()

        profile_names = []
        for result in results:
            if result.source_profile_id and result.source_profile:
                profile_names.append(result.source_profile.name)
        distinct = list(dict.fromkeys(profile_names))  # preserve first-seen order

        if len(distinct) == 1:
            return distinct[0]
        if len(distinct) > 1:
            return "Test Results"
        if self.profile_id and self.profile:
            return self.profile.name
        return "Test Results"


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
        ('child_6_11', 'Child (6-12 years)'),
        ('adult', 'Adult (13+ years)'),
    ]
    test = models.ForeignKey(TestCatalog, on_delete=models.CASCADE, related_name='default_ranges')
    age_category = models.CharField(max_length=20, choices=AGE_CATEGORIES)
    reference_range = models.CharField(max_length=50)
    unit = models.CharField(max_length=20, blank=True)

    class Meta:
        unique_together = ('test', 'age_category')

    def __str__(self):
        return f"{self.test.name} ({self.age_category}): {self.reference_range} {self.unit}"


class TestProfileParameter(models.Model):
    """Parameters that belong to a reusable test profile."""

    INPUT_TYPE_CHOICES = [
        ('text', 'Text'),
        ('numeric', 'Numeric'),
        ('choice', 'Choice'),
    ]

    profile = models.ForeignKey(TestProfile, on_delete=models.CASCADE, related_name='parameters')
    test = models.ForeignKey(TestCatalog, on_delete=models.CASCADE, related_name='profile_parameters')
    section_name = models.CharField(max_length=100, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    input_type = models.CharField(max_length=20, choices=INPUT_TYPE_CHOICES, default='text')
    choice_options = models.TextField(blank=True, help_text="Optional newline-separated values for choice inputs.")
    default_reference_range = models.CharField(max_length=50, blank=True)
    default_unit = models.CharField(max_length=20, blank=True)
    default_comment = models.CharField(max_length=255, blank=True)
    is_required = models.BooleanField(default=False)
    allow_range_learning = models.BooleanField(default=True)

    class Meta:
        ordering = ['profile__display_order', 'display_order', 'id']

    def __str__(self):
        return f"{self.profile.name} - {self.test.name}"

    def choice_list(self):
        return [item.strip() for item in self.choice_options.splitlines() if item.strip()]


class TestResult(models.Model):
    """Test result tied to catalog + stored range/unit."""

    lab_report = models.ForeignKey(LabReport, on_delete=models.CASCADE, related_name='results')
    source_profile = models.ForeignKey(
        TestProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results",
        help_text="Template/profile that created this row (used for removing a whole template block).",
    )
    test = models.ForeignKey(TestCatalog, on_delete=models.CASCADE)
    section_name = models.CharField(max_length=100, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    result_value = models.CharField(max_length=50)
    reference_range = models.CharField(max_length=50, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['display_order', 'id']

    def __str__(self):
        return f"{self.test.name}: {self.result_value}"
