from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import LabReportForm, TestResultFormSet
from .models import LabReport, ReferenceRangeDefault, TestCatalog

staff_required = user_passes_test(lambda u: u.is_active and (u.is_staff or u.is_superuser))


def get_age_category(age_str: str) -> str:
    """Convert age text into an age category for defaults."""
    if not age_str:
        return 'adult'
    age_str = age_str.upper().strip()
    digits = ''.join(filter(str.isdigit, age_str))
    if not digits:
        return 'adult'
    if 'Y' in age_str:
        years = int(digits)
        if years >= 18:
            return 'adult'
        if 12 <= years <= 17:
            return 'child_12_17'
        if 6 <= years <= 11:
            return 'child_6_11'
        if 2 <= years <= 5:
            return 'child_1_5'
        return 'infant'
    if 'M' in age_str:
        months = int(digits)
        return 'neonate' if months <= 1 else 'infant'
    return 'adult'


def get_or_create_test_definition(test_name: str, unit: str = '') -> TestCatalog:
    """Normalize free-text test names into our known-test table."""
    normalized_name = ' '.join((test_name or '').split())
    test = TestCatalog.objects.filter(name__iexact=normalized_name).first()
    if test:
        if unit and not test.unit:
            test.unit = unit
            test.save(update_fields=['unit'])
        return test
    return TestCatalog.objects.create(name=normalized_name, unit=unit or '')


def save_results_from_formset(report: LabReport, formset) -> None:
    """Persist edited/new rows and handle row deletion in one place."""
    age_category = get_age_category(report.patient_age)

    for result_form in formset.forms:
        cleaned = getattr(result_form, 'cleaned_data', None)
        if not cleaned:
            continue

        instance = result_form.instance
        should_delete = cleaned.get('DELETE', False)
        if should_delete:
            if instance and instance.pk:
                instance.delete()
            continue

        test_name = ' '.join((cleaned.get('test_name') or '').split())
        result_value = cleaned.get('result_value')
        reference_range = cleaned.get('reference_range') or ''
        unit = cleaned.get('unit') or ''

        # Skip untouched blank extra rows.
        if not any([test_name, result_value, reference_range, unit]):
            continue

        instance = result_form.save(commit=False)
        instance.lab_report = report
        instance.test = get_or_create_test_definition(test_name, unit)
        instance.reference_range = reference_range
        instance.unit = unit
        instance.save()

        default_exists = ReferenceRangeDefault.objects.filter(
            test=instance.test,
            age_category=age_category,
        ).exists()
        if not default_exists and reference_range and unit:
            ReferenceRangeDefault.objects.create(
                test=instance.test,
                age_category=age_category,
                reference_range=reference_range,
                unit=unit,
            )


def build_report_form_context(form, formset, **extra_context):
    context = {
        'form': form,
        'formset': formset,
        'existing_tests': TestCatalog.objects.order_by('name').values_list('name', flat=True),
    }
    context.update(extra_context)
    return context


def handle_report_form(request, report=None):
    """Shared create/edit handler for report entry."""
    is_edit = report is not None
    report = report or LabReport(attendant=request.user)

    if request.method == 'POST':
        form = LabReportForm(request.POST, instance=report)
        formset = TestResultFormSet(request.POST, instance=report)
        if form.is_valid() and formset.is_valid():
            report = form.save(commit=False)
            if not report.attendant:
                report.attendant = request.user
            if not report.attendant_name:
                report.attendant_name = request.user.get_full_name() or request.user.username
            report.save()
            save_results_from_formset(report, formset)
            messages.success(request, 'Report updated.' if is_edit else 'Report saved successfully.')
            return redirect('report_detail', pk=report.pk)
        messages.error(request, 'Please fix the errors below.')
    else:
        form = LabReportForm(instance=report)
        formset = TestResultFormSet(instance=report)

    return render(
        request,
        'lab/report_form.html',
        build_report_form_context(form, formset, edit_mode=is_edit, report=report if is_edit else None),
    )


@login_required
@staff_required
def report_list(request):
    qs = LabReport.objects.all().order_by('-created_at')
    paginator = Paginator(qs, 10)
    reports = paginator.get_page(request.GET.get('page'))
    context = {
        'reports': reports,
        'total_reports': qs.count(),
        'printed_count': qs.filter(printed=True).count(),
        'draft_count': qs.filter(printed=False).count(),
    }
    return render(request, 'lab/report_list.html', context)


@login_required
@staff_required
@transaction.atomic
def report_create(request):
    return handle_report_form(request)


@login_required
@staff_required
@transaction.atomic
def report_edit(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
    return handle_report_form(request, report=report)


@login_required
@staff_required
def report_detail(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
    results = report.results.select_related('test').all()
    if not report.printed and request.GET.get('mark_printed'):
        report.printed = True
        report.save(update_fields=['printed'])
    return render(request, 'lab/report_detail.html', {'report': report, 'results': results})


@login_required
@staff_required
def report_print(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
    results = report.results.select_related('test').all()
    if not report.printed:
        report.printed = True
        report.save(update_fields=['printed'])
    return render(request, 'lab/report_print.html', {'report': report, 'results': results})


@login_required
@staff_required
def report_delete(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
    if request.method == 'POST':
        report.delete()
        messages.success(request, 'Report deleted.')
        return redirect('report_list')
    return render(request, 'lab/report_confirm_delete.html', {'report': report})


@login_required
@staff_required
def default_range(request):
    """AJAX endpoint to fetch default reference range for a test name + age."""
    test_name = ' '.join((request.GET.get('test') or '').split())
    age = request.GET.get('age', '')
    if not test_name:
        return JsonResponse({})

    test = TestCatalog.objects.filter(name__iexact=test_name).first()
    if not test:
        return JsonResponse({})

    age_cat = get_age_category(age)
    default = ReferenceRangeDefault.objects.filter(test=test, age_category=age_cat).first()
    if not default:
        return JsonResponse({})
    return JsonResponse({
        'reference_range': default.reference_range,
        'unit': default.unit,
    })
