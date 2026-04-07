import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import LabReportForm, TestResultFormSet
from .models import (
    LabReport,
    ReferenceRangeDefault,
    TestCatalog,
    TestProfile,
    TestProfileParameter,
)

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
        if cleaned.get('DELETE', False):
            if instance and instance.pk:
                instance.delete()
            continue

        test_name = ' '.join((cleaned.get('test_name') or '').split())
        result_value = cleaned.get('result_value') or ''
        reference_range = cleaned.get('reference_range') or ''
        unit = cleaned.get('unit') or ''
        comment = cleaned.get('comment') or ''
        section_name = cleaned.get('section_name') or ''
        display_order = cleaned.get('display_order') or 0

        # Skip untouched blank extra rows.
        if not any([test_name, result_value, reference_range, unit, comment]):
            continue

        instance = result_form.save(commit=False)
        instance.lab_report = report
        instance.test = get_or_create_test_definition(test_name, unit)
        instance.section_name = section_name
        instance.display_order = int(display_order or 0)
        instance.reference_range = reference_range
        instance.unit = unit
        instance.comment = comment
        instance.save()

        default_exists = ReferenceRangeDefault.objects.filter(
            test=instance.test,
            age_category=age_category,
        ).exists()
        if not default_exists and reference_range:
            ReferenceRangeDefault.objects.create(
                test=instance.test,
                age_category=age_category,
                reference_range=reference_range,
                unit=unit,
            )


def serialize_profile_payload(profiles):
    payload = {}
    for profile in profiles:
        parameters = []
        for parameter in profile.parameters.select_related('test').all():
            parameters.append({
                'test_name': parameter.test.name,
                'section_name': parameter.section_name,
                'display_order': parameter.display_order,
                'reference_range': parameter.default_reference_range,
                'unit': parameter.default_unit or parameter.test.unit,
                'comment': parameter.default_comment,
                'input_type': parameter.input_type,
                'choice_options': parameter.choice_list(),
            })
        payload[str(profile.pk)] = {
            'name': profile.name,
            'code': profile.code,
            'default_specimen_type': profile.default_specimen_type,
            'description': profile.description,
            'parameters': parameters,
        }
    return payload


def group_results(report, results):
    grouped = []
    for result in results:
        section_name = result.section_name or (report.profile.name if report.profile else 'Laboratory Results')
        if grouped and grouped[-1]['name'] == section_name:
            grouped[-1]['results'].append(result)
        else:
            grouped.append({'name': section_name, 'results': [result]})
    return grouped


def build_report_form_context(form, formset, **extra_context):
    profiles = TestProfile.objects.filter(is_active=True).prefetch_related('parameters__test')
    context = {
        'form': form,
        'formset': formset,
        'existing_tests': list(TestCatalog.objects.order_by('name').values_list('name', flat=True)),
        'test_profiles': profiles,
        'test_profile_payload': serialize_profile_payload(profiles),
        'active_nav': 'new_report',
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
            if report.profile and not report.specimen_type:
                report.specimen_type = report.profile.default_specimen_type
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
    base_qs = LabReport.objects.select_related('attendant', 'profile').all().order_by('-created_at')
    qs = base_qs

    search = (request.GET.get('search') or '').strip()
    selected_filter = (request.GET.get('filter') or '').strip()

    if search:
        filters = (
            Q(patient_name__icontains=search) |
            Q(referred_by__icontains=search) |
            Q(specimen_type__icontains=search)
        )
        if search.isdigit():
            filters |= Q(pk=int(search))
        qs = qs.filter(filters)

    if selected_filter == 'printed':
        qs = qs.filter(printed=True)
    elif selected_filter == 'draft':
        qs = qs.filter(printed=False)

    paginator = Paginator(qs, 10)
    reports = paginator.get_page(request.GET.get('page'))
    context = {
        'reports': reports,
        'total_reports': base_qs.count(),
        'printed_count': base_qs.filter(printed=True).count(),
        'draft_count': base_qs.filter(printed=False).count(),
        'active_nav': 'dashboard',
    }
    return render(request, 'lab/report_list.html', context)


@login_required
@staff_required
def template_library(request):
    profiles = TestProfile.objects.filter(is_active=True).prefetch_related('parameters__test')
    return render(
        request,
        'lab/template_library.html',
        {'profiles': profiles, 'active_nav': 'templates'},
    )


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
    report = get_object_or_404(LabReport.objects.select_related('attendant', 'profile'), pk=pk)
    results = report.results.select_related('test').all()
    if request.GET.get('mark_printed'):
        report.printed = True
        report.printed_at = timezone.now()
        report.save(update_fields=['printed', 'printed_at'])
    return render(
        request,
        'lab/report_detail.html',
        {
            'report': report,
            'results': results,
            'result_groups': group_results(report, results),
            'active_nav': 'dashboard',
        },
    )


@login_required
@staff_required
def report_print(request, pk):
    report = get_object_or_404(LabReport.objects.select_related('attendant', 'profile'), pk=pk)
    results = report.results.select_related('test').all()
    if not report.printed or not report.printed_at:
        report.printed = True
        report.printed_at = timezone.now()
        report.save(update_fields=['printed', 'printed_at'])
    return render(
        request,
        'lab/report_print.html',
        {
            'report': report,
            'results': results,
            'result_groups': group_results(report, results),
        },
    )


@login_required
@staff_required
def report_delete(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
    if request.method == 'POST':
        report.delete()
        messages.success(request, 'Report deleted.')
        return redirect('report_list')
    return render(request, 'lab/report_confirm_delete.html', {'report': report, 'active_nav': 'dashboard'})


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
