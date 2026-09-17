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
    sample_date = models.DateTimeField()
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
        ('infant', 'Infant (1-11 months)'),
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
        ('textarea', 'Text Area'),
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
    result_value = models.TextField(blank=True, default='')
    reference_range = models.CharField(max_length=50, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    comment = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['display_order', 'id']

    def __str__(self):
        return f"{self.test.name}: {self.result_value}"


class LabConsumable(models.Model):
    """Consumable or equipment item tracked per hospital lab (e.g. glass slides, reagent vials)."""

    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        related_name='lab_consumables',
    )
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=50, default='units')
    current_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    minimum_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Warn when stock falls at or below this level')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('hospital', 'name')
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_low(self):
        return self.minimum_quantity > 0 and self.current_quantity <= self.minimum_quantity


class LabConsumableUsage(models.Model):
    """Records each deduction of a consumable against a specific lab report."""

    consumable = models.ForeignKey(LabConsumable, on_delete=models.CASCADE, related_name='usages')
    lab_report = models.ForeignKey(LabReport, on_delete=models.CASCADE, related_name='consumable_usages')
    quantity_used = models.DecimalField(max_digits=10, decimal_places=2)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    used_at = models.DateTimeField(auto_now_add=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-used_at']

    def __str__(self):
        return f"{self.consumable.name} × {self.quantity_used}"


# ===========================================================================
# Lab module rework — merged in from the former `lab_next` app.
#
# Everything below this line was built and proven as a separate app
# (queue -> pick sample -> enter results -> review -> payment-gated release
# -> report, plus Phlebotomy intake) before being folded into `lab` so there
# is one lab app, not two living side by side indefinitely. This is now the
# only active engine — every hospital's queue/report screens are these views.
#
# Everything above this line (TestProfile, LabReport, TestCatalog,
# ReferenceRangeDefault, TestProfileParameter, TestResult) is the prior
# engine's schema. Its own create/edit screens have been retired; the models
# stay only so the historical report archive (report_detail/report_print/
# patient_reports) keeps reading correctly. See
# lab/management/commands/backfill_from_legacy_lab.py for converting old
# LabReport/TestResult rows into the models below.
# ===========================================================================

class ResultType(models.TextChoices):
    FREE_ENTRY = "free_entry", "Free Entry"
    DEFINED_OPTION = "defined_option", "Defined Option"
    PARAMETER_PANEL = "parameter_panel", "Parameter Panel"
    CULTURE = "culture", "Culture & Sensitivity"


class ValueType(models.TextChoices):
    NUMERIC = "numeric", "Numeric"
    TEXT = "text", "Text"
    CODED = "coded", "Coded (S / I / R)"
    GROWTH = "growth", "Growth / No Growth (dropdown)"
    LEVEL = "level", "Level (Negative / Trace / 1+ - 4+, dropdown)"


class Sex(models.TextChoices):
    ANY = "any", "Any"
    MALE = "male", "Male"
    FEMALE = "female", "Female"


class Flag(models.TextChoices):
    NORMAL = "N", "Normal"
    HIGH = "H", "High"
    LOW = "L", "Low"
    ABNORMAL = "A", "Abnormal"


class ServiceCategory(models.Model):
    """e.g. Chemistry, Hematology, Microbiology."""
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name_plural = "service categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class SpecimenType(models.Model):
    """Blood, Serum, Urine, Stool, Swab, HVS ..."""
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(
        default=False,
        help_text="System-seeded specimen type. Locked from editing/deletion in the "
                   "Lab Management screens so the shared catalog can't drift accidentally.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LabTest(models.Model):
    """
    An orderable lab test — owned by exactly one hospital. Each hospital
    builds its own templates (a CBC's parameters and reference ranges are
    that hospital's own bench/analyzer's, not a platform-wide guess) — a
    newly onboarded hospital starts with zero tests. Pricing/offering lives
    on `reception.Service.lab_test_next`, same as before; what changed is
    that the test definition itself is no longer shared across hospitals.
    `test_clone` lets a hospital copy an existing test (its own, or any
    other hospital's — definitions aren't sensitive clinical data) as a
    starting point instead of building one from nothing every time;
    `is_starter_template` rows are Ternah-curated ones pinned to the top of
    that picker (e.g. a ready-made Culture & Sensitivity panel).

    `result_type` decides which entry UI and which child config applies:
      FREE_ENTRY      -> no children (single text box at entry)
      DEFINED_OPTION  -> `options` (DefinedOption rows)
      PARAMETER_PANEL -> `parameters` (Parameter rows, each with ranges) —
                          this is also how Culture & Sensitivity panels are
                          built today: CODED parameters grouped by drug
                          class, plus GROWTH/TEXT parameters for the
                          organism. See test_clone_picker starter templates.
      CULTURE         -> retired from the create/edit picker in favor of the
                          PARAMETER_PANEL pattern above; kept only so a
                          pre-existing test of this type (if any) keeps
                          rendering — see LabTestForm.__init__.
    """
    hospital = models.ForeignKey(
        "accounts.Hospital", on_delete=models.CASCADE, related_name="lab_tests",
        help_text="The hospital that owns this test definition.",
    )
    name = models.CharField(max_length=160)
    code = models.CharField(max_length=40, blank=True)
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, related_name="tests",
    )
    description = models.TextField(blank=True)
    active_for_ordering = models.BooleanField(default=True)

    result_type = models.CharField(max_length=20, choices=ResultType.choices)
    accepted_specimens = models.ManyToManyField(SpecimenType, related_name="tests", blank=True)

    is_starter_template = models.BooleanField(
        default=False,
        help_text="Curated by Ternah Health — pinned to the top of every hospital's "
                   "'Clone a Test' picker as a ready-made starting point (e.g. a full "
                   "Culture & Sensitivity panel). Toggle from Django admin only.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_result_type_display()})"


class DefinedOption(models.Model):
    """A selectable result value for DEFINED_OPTION tests — e.g. Positive /
    Negative, or +, ++, +++."""
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_abnormal = models.BooleanField(default=False)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.test.name}: {self.label}"


class Parameter(models.Model):
    """A measured parameter inside a PARAMETER_PANEL test (e.g. Hemoglobin,
    WBC — or, for a culture-style panel, one antibiotic). Carries several
    ParameterRange rows keyed by sex + age band; the correct one is resolved
    per patient at entry time."""
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="parameters")
    name = models.CharField(max_length=120)
    unit = models.CharField(max_length=40, blank=True)
    value_type = models.CharField(max_length=10, choices=ValueType.choices, default=ValueType.NUMERIC)
    group_label = models.CharField(
        max_length=80, blank=True,
        help_text="Optional section heading shared by several parameters — e.g. a drug class "
                   "(Penicillins, Cephalosporins...) on a culture-style panel. Leave blank for a "
                   "flat panel like a CBC.",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.test.name} / {self.name}"

    def resolve_range(self, sex, age_years):
        """Return the single ParameterRange that best matches this patient,
        or None. Most specific match wins: sex-specific beats ANY, narrower
        age span beats wider."""
        candidates = [r for r in self.ranges.all() if r.matches(sex, age_years)]
        if not candidates:
            return None
        candidates.sort(
            key=lambda r: (
                0 if r.sex != Sex.ANY else 1,
                (r.age_max if r.age_max is not None else 999)
                - (r.age_min if r.age_min is not None else 0),
            )
        )
        return candidates[0]


class ParameterRange(models.Model):
    """One ref range for a Parameter, valid for a sex + age band. Global per
    test (same across hospitals) — numeric low/high, or a text range for
    non-numeric parameters (e.g. "<1:80")."""
    parameter = models.ForeignKey(Parameter, on_delete=models.CASCADE, related_name="ranges")
    sex = models.CharField(max_length=6, choices=Sex.choices, default=Sex.ANY)
    age_min = models.PositiveSmallIntegerField(null=True, blank=True, help_text="inclusive, years")
    age_max = models.PositiveSmallIntegerField(null=True, blank=True, help_text="inclusive, years")
    ref_low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ref_text = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["sex", "age_min"]

    def matches(self, sex, age_years):
        if self.sex != Sex.ANY and sex is not None and self.sex != sex:
            return False
        if self.age_min is not None and age_years is not None and age_years < self.age_min:
            return False
        if self.age_max is not None and age_years is not None and age_years > self.age_max:
            return False
        return True

    def flag_for(self, value):
        """Return H / L / N for a numeric value against this range."""
        if value is None or self.ref_low is None or self.ref_high is None:
            return Flag.NORMAL
        if value < self.ref_low:
            return Flag.LOW
        if value > self.ref_high:
            return Flag.HIGH
        return Flag.NORMAL

    def display(self):
        if self.ref_text:
            return self.ref_text
        # Decimal's :g format keeps stored trailing zeros (12.000 stays
        # "12.000"), unlike float's — go through float purely for display.
        lo = "" if self.ref_low is None else f"{float(self.ref_low):g}"
        hi = "" if self.ref_high is None else f"{float(self.ref_high):g}"
        return f"{lo} - {hi}".strip(" -")

    def __str__(self):
        return f"{self.parameter.name} [{self.sex} {self.age_min}-{self.age_max}]: {self.display()}"


class CultureAntibiotic(models.Model):
    """An antibiotic offered on a CULTURE test, grouped by class. At entry
    each becomes an S/I/R row."""
    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="antibiotics")
    drug_class = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=120)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.drug_class})"


class ServiceEntryMode(models.TextChoices):
    FROM_LAB = "from_lab", "From Lab (Phlebotomy intake)"
    FROM_RECEPTION = "from_reception", "From Reception (standard)"


class LabSettings(models.Model):
    """Lab-module behavior toggles for one hospital. Defaults everywhere are
    today's existing behavior — a hospital that never touches this page
    should see nothing change."""

    hospital = models.OneToOneField(
        "accounts.Hospital", on_delete=models.CASCADE, related_name="lab_settings_next",
    )

    phlebotomy_enabled = models.BooleanField(
        default=False,
        help_text="Adds a Phlebotomy consultation queue inside the Lab module, ahead of billing.",
    )
    service_entry_mode = models.CharField(
        max_length=20, choices=ServiceEntryMode.choices, default=ServiceEntryMode.FROM_RECEPTION,
        help_text="How new lab visits pick up their tests. Applies to newly created visits only — "
                   "in-flight visits keep whatever mode they started under.",
    )
    payment_required_before_lab = models.BooleanField(
        default=False,
        help_text="If on, the visit balance must be fully settled before reception can send the "
                   "patient to sample collection. If off (today's behavior), approval alone is enough "
                   "and credit is allowed.",
    )
    allow_reaccept_before_print = models.BooleanField(
        default=False,
        help_text="If on, a released report can be reopened and re-released (e.g. after a correction). "
                   "If off (default), release locks the results — matches normal LIS discipline.",
    )
    outside_samples_enabled = models.BooleanField(default=True)
    require_review_before_release = models.BooleanField(
        default=True,
        help_text="If on, entered results must be explicitly marked reviewed before they can be released.",
    )
    payment_required_before_release = models.BooleanField(
        default=False,
        help_text="If on, the visit balance must be fully settled before the report can be released/printed "
                   "— 'gated on payment for the print itself'. Separate from payment_required_before_lab, "
                   "which gates sample collection instead.",
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )

    class Meta:
        verbose_name = "Lab settings"
        verbose_name_plural = "Lab settings"

    def __str__(self):
        return f"Lab settings — {self.hospital.name}"


class OrderStage(models.TextChoices):
    PENDING = "pending", "Pending"
    SAMPLE_COLLECTED = "sample_collected", "Sample Collected"
    IN_PROGRESS = "in_progress", "In Progress"
    ENTERED = "entered", "Results Entered"
    REVIEWED = "reviewed", "Reviewed"
    RELEASED = "released", "Released"


class LabOrder(models.Model):
    """One ordered test for a visit — mirrors a row on the Enter Results
    screen. `visit_service` is the billable line (price, approval, payment —
    all read through this link, never copied). `test` is denormalized
    alongside it for query convenience and because a service's linked test
    shouldn't silently change if the service is edited later."""

    visit_service = models.OneToOneField(
        "reception.VisitService", on_delete=models.PROTECT, related_name="lab_order_next",
    )
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="orders")

    # Denormalized for query convenience — same pattern LabReport already
    # uses (visit + hospital both stored directly, not just reachable via FK
    # traversal).
    hospital = models.ForeignKey("accounts.Hospital", on_delete=models.CASCADE, related_name="lab_orders_next")

    stage = models.CharField(max_length=20, choices=OrderStage.choices, default=OrderStage.PENDING)
    priority_routine = models.BooleanField(default=True, help_text="False = Urgent")

    # Sampling
    specimen = models.ForeignKey(SpecimenType, on_delete=models.PROTECT, null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lab_collections_next",
    )
    is_outside_sample = models.BooleanField(default=False)
    outside_source = models.CharField(max_length=200, blank=True)
    collection_notes = models.TextField(blank=True)

    # Frozen at order-creation time — range resolution must stay reproducible
    # even if the patient's record is edited later.
    patient_sex = models.CharField(max_length=6, choices=Sex.choices, default=Sex.ANY)
    patient_age_years = models.PositiveSmallIntegerField(null=True, blank=True)

    # Frozen from LabSettings.service_entry_mode at the moment this order was
    # created — an admin flipping the facility toggle mid-day must not change
    # how orders already in flight behave. See LabSettings docstring.
    entry_mode_at_creation = models.CharField(max_length=20, choices=ServiceEntryMode.choices, blank=True)

    # Combine-at-print grouping — a per-visit choice, never a storage split.
    print_group_id = models.CharField(max_length=40, blank=True)

    # Set only by the legacy-data backfill command — which old LabReport this
    # order was converted from. Doubles as the idempotency check that lets
    # the backfill be re-run safely.
    legacy_report_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order#{self.pk} {self.test.name} [{self.get_stage_display()}]"

    def mark_sample_collected(self, user, specimen, when, outside_source="", notes=""):
        self.specimen = specimen
        self.collected_by = user
        self.collected_at = when
        self.is_outside_sample = bool(outside_source)
        self.outside_source = outside_source
        self.collection_notes = notes
        self.stage = OrderStage.SAMPLE_COLLECTED
        self.save()


class LabResult(models.Model):
    """The result envelope for one LabOrder. Child ResultValue rows hold
    parameter/antibiotic values; simple result types store directly here."""

    order = models.OneToOneField(LabOrder, on_delete=models.CASCADE, related_name="result")
    result_type = models.CharField(max_length=20, choices=ResultType.choices)

    free_text = models.TextField(blank=True)               # FREE_ENTRY
    chosen_option = models.CharField(max_length=60, blank=True)  # DEFINED_OPTION
    organism = models.CharField(max_length=160, blank=True)      # CULTURE

    bench_notes = models.TextField(blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lab_entries_next",
    )
    entered_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lab_reviews_next",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lab_releases_next",
    )
    released_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Result for {self.order}"


class ResultValue(models.Model):
    """One measured parameter value (PARAMETER_PANEL) OR one antibiotic
    sensitivity row (CULTURE). Range/unit/flag are FROZEN here at save time —
    never re-derived from the live definition at print time, so a later edit
    to a Parameter's ranges can't change what an old report shows."""

    result = models.ForeignKey(LabResult, on_delete=models.CASCADE, related_name="values")
    parameter = models.ForeignKey(Parameter, on_delete=models.SET_NULL, null=True, blank=True)
    antibiotic = models.ForeignKey(CultureAntibiotic, on_delete=models.SET_NULL, null=True, blank=True)

    label = models.CharField(max_length=120)
    group_label = models.CharField(max_length=80, blank=True)

    value_num = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    value_text = models.CharField(max_length=255, blank=True)
    value_code = models.CharField(max_length=10, blank=True)  # S / I / R etc.

    unit = models.CharField(max_length=40, blank=True)
    ref_display = models.CharField(max_length=120, blank=True)
    flag = models.CharField(max_length=1, choices=Flag.choices, blank=True)
    comment = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.label}: {self.value_num or self.value_text or self.value_code}"
