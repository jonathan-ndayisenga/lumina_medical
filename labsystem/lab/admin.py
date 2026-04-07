from django.contrib import admin

from .models import (
    LabReport,
    ReferenceRangeDefault,
    TestCatalog,
    TestProfile,
    TestProfileParameter,
    TestResult,
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
