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
    if request.method == 'POST':
        form = LabReportForm(request.POST)
        formset = TestResultFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            report = form.save(commit=False)
            report.attendant = request.user
            if not report.attendant_name:
                report.attendant_name = request.user.get_full_name() or request.user.username
            report.save()
            formset.instance = report
            results = formset.save(commit=False)
            age_category = get_age_category(report.patient_age)
            for res in results:
                res.lab_report = report
                res.save()
                if res.test:
                    exists = ReferenceRangeDefault.objects.filter(test=res.test, age_category=age_category).exists()
                    if not exists:
                        ReferenceRangeDefault.objects.create(
                            test=res.test,
                            age_category=age_category,
                            reference_range=res.reference_range,
                            unit=res.unit,
                        )
            formset.save_m2m()
            messages.success(request, 'Report saved successfully.')
            return redirect('report_detail', pk=report.pk)
        messages.error(request, 'Please fix the errors below.')
    else:
        form = LabReportForm()
        formset = TestResultFormSet()
    return render(request, 'lab/report_form.html', {'form': form, 'formset': formset})


@login_required
@staff_required
@transaction.atomic
def report_edit(request, pk):
    report = get_object_or_404(LabReport, pk=pk)
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
            results = formset.save(commit=False)
            age_category = get_age_category(report.patient_age)
            for res in results:
                res.lab_report = report
                res.save()
                if res.test:
                    exists = ReferenceRangeDefault.objects.filter(test=res.test, age_category=age_category).exists()
                    if not exists:
                        ReferenceRangeDefault.objects.create(
                            test=res.test,
                            age_category=age_category,
                            reference_range=res.reference_range,
                            unit=res.unit,
                        )
            formset.save_m2m()
            messages.success(request, 'Report updated.')
            return redirect('report_detail', pk=report.pk)
        messages.error(request, 'Please fix the errors below.')
    else:
        form = LabReportForm(instance=report)
        formset = TestResultFormSet(instance=report)
    return render(request, 'lab/report_form.html', {'form': form, 'formset': formset, 'edit_mode': True, 'report': report})


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
    """AJAX endpoint to fetch default reference range for a test+age."""
    test_id = request.GET.get('test')
    age = request.GET.get('age', '')
    try:
        test = TestCatalog.objects.get(id=test_id)
    except (TestCatalog.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'found': False})
    age_cat = get_age_category(age)
    default = ReferenceRangeDefault.objects.filter(test=test, age_category=age_cat).first()
    if not default:
        return JsonResponse({'found': False})
    return JsonResponse({
        'found': True,
        'reference_range': default.reference_range,
        'unit': default.unit,
    })
