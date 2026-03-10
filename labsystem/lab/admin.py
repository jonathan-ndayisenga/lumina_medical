from django.contrib import admin
from .models import LabReport, TestResult, TestCatalog, ReferenceRangeDefault


class TestResultInline(admin.TabularInline):
    model = TestResult
    extra = 0


@admin.register(LabReport)
class LabReportAdmin(admin.ModelAdmin):
    list_display = ('patient_name', 'sample_date', 'printed', 'created_at')
    inlines = [TestResultInline]


@admin.register(TestCatalog)
class TestCatalogAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'display_order')
    ordering = ('display_order', 'name')


@admin.register(ReferenceRangeDefault)
class ReferenceRangeDefaultAdmin(admin.ModelAdmin):
    list_display = ('test', 'age_category', 'reference_range', 'unit')
    list_filter = ('age_category', 'test')
