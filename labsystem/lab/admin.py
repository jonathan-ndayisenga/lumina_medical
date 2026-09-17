from django.contrib import admin

from .models import (
    LabReport,
    ReferenceRangeDefault,
    TestCatalog,
    TestProfile,
    TestProfileParameter,
    TestResult,
)
# Merged in from the former `lab_next` app.
from .models import (
    CultureAntibiotic,
    DefinedOption,
    LabOrder,
    LabResult,
    LabSettings,
    LabTest,
    Parameter,
    ParameterRange,
    ResultValue,
    ServiceCategory,
    SpecimenType,
)


class TestResultInline(admin.TabularInline):
    model = TestResult
    extra = 0
    fields = ('section_name', 'test', 'result_value', 'reference_range', 'unit', 'comment')


class TestProfileParameterInline(admin.TabularInline):
    model = TestProfileParameter
    extra = 0
    fields = (
        'display_order',
        'section_name',
        'test',
        'input_type',
        'choice_options',
        'default_reference_range',
        'default_unit',
        'default_comment',
        'is_required',
        'allow_range_learning',
    )


@admin.register(LabReport)
class LabReportAdmin(admin.ModelAdmin):
    list_display = ('patient_name', 'profile', 'sample_date', 'referred_by', 'printed', 'created_at')
    list_filter = ('profile', 'printed', 'patient_sex')
    search_fields = ('patient_name', 'referred_by', 'specimen_type')
    inlines = [TestResultInline]


@admin.register(TestCatalog)
class TestCatalogAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'display_order')
    search_fields = ('name',)
    ordering = ('display_order', 'name')


@admin.register(ReferenceRangeDefault)
class ReferenceRangeDefaultAdmin(admin.ModelAdmin):
    list_display = ('test', 'age_category', 'reference_range', 'unit')
    list_filter = ('age_category', 'test')
    search_fields = ('test__name',)


@admin.register(TestProfile)
class TestProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'default_specimen_type', 'is_active', 'display_order')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')
    ordering = ('display_order', 'name')
    inlines = [TestProfileParameterInline]


# ===========================================================================
# Merged in from the former `lab_next` app.
# ===========================================================================

class DefinedOptionInline(admin.TabularInline):
    model = DefinedOption
    extra = 1


class ParameterRangeInline(admin.TabularInline):
    model = ParameterRange
    extra = 1


class ParameterInline(admin.TabularInline):
    model = Parameter
    extra = 1
    show_change_link = True


class CultureAntibioticInline(admin.TabularInline):
    model = CultureAntibiotic
    extra = 1


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "description"]
    search_fields = ["name"]


@admin.register(SpecimenType)
class SpecimenTypeAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    list_display = ["name", "hospital", "code", "category", "result_type", "active_for_ordering", "is_starter_template"]
    list_filter = ["hospital", "result_type", "category", "active_for_ordering", "is_starter_template"]
    search_fields = ["name", "code"]
    filter_horizontal = ["accepted_specimens"]
    inlines = [DefinedOptionInline, ParameterInline, CultureAntibioticInline]


@admin.register(Parameter)
class ParameterAdmin(admin.ModelAdmin):
    list_display = ["name", "test", "unit", "value_type", "sort_order"]
    list_filter = ["test"]
    inlines = [ParameterRangeInline]


@admin.register(LabSettings)
class LabSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "hospital", "phlebotomy_enabled", "service_entry_mode",
        "payment_required_before_lab", "updated_at",
    ]
    list_filter = ["phlebotomy_enabled", "service_entry_mode"]


class ResultValueInline(admin.TabularInline):
    model = ResultValue
    extra = 0


@admin.register(LabResult)
class LabResultAdmin(admin.ModelAdmin):
    list_display = ["order", "result_type", "entered_by", "entered_at", "reviewed_by"]
    list_filter = ["result_type"]
    inlines = [ResultValueInline]


@admin.register(LabOrder)
class LabOrderAdmin(admin.ModelAdmin):
    list_display = [
        "id", "test", "hospital", "stage", "specimen", "is_outside_sample",
        "entry_mode_at_creation", "created_at",
    ]
    list_filter = ["stage", "hospital", "is_outside_sample", "entry_mode_at_creation"]
    search_fields = ["test__name"]
    autocomplete_fields = ["visit_service"]
