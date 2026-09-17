from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import User
from reception.models import Patient, QueueEntry, Service, Visit, VisitService
from reception.workflow import (
    ensure_pending_queue_entry,
    mark_queue_entries_processed,
    record_admin_override,
    require_admin_override,
    send_to_reception_queue,
    sync_visit_status,
)
from doctor.models import LabRequest, Notification

from .forms import LabConsumableForm
from .models import LabConsumable, LabReport
# Merged in from the former `lab_next` app — the reworked engine's models.
from .models import LabOrder, OrderStage, ResultType, Sex
from .guards import release_visit_service_for_lab
from .services_next import hydrate_entry_form, save_results

def _lab_access_ok(user):
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    # Existing role-based access.
    if getattr(user, "role", "") in {
        User.ROLE_SUPERADMIN,
        User.ROLE_HOSPITAL_ADMIN,
        User.ROLE_LAB_ATTENDANT,
    }:
        return True
    # Multi-role access via groups.
    if user.groups.filter(name="Lab").exists():
        return True
    # Keep supporting staff-flagged users.
    return user.is_staff


staff_required = user_passes_test(_lab_access_ok)


def get_active_hospital(request):
    return getattr(request, 'hospital', None) or getattr(request.user, 'hospital', None)


def resolve_next_url(request, fallback_url):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback_url


def scoped_reports_queryset(request):
    qs = LabReport.objects.select_related('attendant', 'profile', 'hospital', 'visit').all()
    hospital = get_active_hospital(request)
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        qs = qs.filter(hospital=hospital)
    return qs


def report_test_summary(report: LabReport) -> str:
    test_names = [name for name in report.results.values_list("test__name", flat=True) if name]
    if test_names:
        return ", ".join(test_names[:4])
    if report.profile_id:
        return report.profile.name
    return "requested tests"


def lab_visit_services(visit, *, performed=None):
    if not visit:
        return VisitService.objects.none()
    qs = VisitService.objects.filter(
        visit=visit,
        service__category="lab",
    ).select_related("service__test_profile", "lab_report")
    if performed is True:
        qs = qs.filter(performed=True)
    elif performed is False:
        qs = qs.filter(performed=False)
    return qs.order_by("created_at", "id")


def mark_visit_service_performed(visit_service):
    if not visit_service.performed:
        visit_service.performed = True
        visit_service.performed_at = timezone.now()
        visit_service.save(update_fields=["performed", "performed_at"])


def reconcile_lab_visit_services(visit):
    if not visit:
        return 0

    repaired = 0
    pending_services = (
        lab_visit_services(visit, performed=False)
        .select_related("lab_report")
    )
    for visit_service in pending_services:
        report = getattr(visit_service, "lab_report", None)
        if report and (report.sent_to_doctor or report.results.exists()):
            mark_visit_service_performed(visit_service)
            repaired += 1

    if repaired:
        refresh_lab_doctor_queue_reason(visit)
    return repaired


def pending_lab_doctor_entry(report: LabReport):
    if not report.visit_id:
        return None
    return (
        QueueEntry.objects.filter(
            visit=report.visit,
            queue_type=QueueEntry.TYPE_LAB_DOCTOR,
            processed=False,
        )
        .select_related("requested_by")
        .order_by("created_at")
        .first()
    )


def pending_lab_service_names(visit):
    return list(
        VisitService.objects.filter(
            visit=visit,
            service__category=Service.CATEGORY_LAB,
            performed=False,
        ).values_list("service__name", flat=True)
    )


def refresh_lab_doctor_queue_reason(visit):
    pending_names = pending_lab_service_names(visit)
    reason = (
        f"Doctor requested: {', '.join(pending_names)}"
        if pending_names
        else "All requested tests are complete. Send results to doctor."
    )
    QueueEntry.objects.filter(
        visit=visit,
        queue_type=QueueEntry.TYPE_LAB_DOCTOR,
        processed=False,
    ).exclude(reason=reason).update(reason=reason)


def mark_open_lab_queue_entries_processed(visit):
    return QueueEntry.objects.filter(
        visit=visit,
        queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
        processed=False,
    ).update(processed=True, processed_at=timezone.now())


def direct_lab_can_route(report: LabReport) -> bool:
    if not report.visit_id:
        return False
    if report.sent_to_doctor:
        return False
    if report.visit.queue_entries.filter(queue_type=QueueEntry.TYPE_RECEPTION, processed=False).exists():
        return False
    if pending_lab_doctor_entry(report):
        return False
    reconcile_lab_visit_services(report.visit)
    return not lab_visit_services(report.visit, performed=False).exists()


def cleanup_stale_lab_queue_entries(*, visit=None, hospital=None) -> int:
    queue_entries = QueueEntry.objects.filter(
        queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
        processed=False,
    ).select_related("visit")
    if visit is not None:
        queue_entries = queue_entries.filter(visit=visit)
    elif hospital is not None:
        queue_entries = queue_entries.filter(hospital=hospital)

    processed_count = 0
    processed_at = timezone.now()
    for entry in queue_entries:
        current_visit = entry.visit
        reconcile_lab_visit_services(current_visit)
        if lab_visit_services(current_visit, performed=False).exists():
            continue

        has_results_ready_for_doctor = QueueEntry.objects.filter(
            visit=current_visit,
            queue_type=QueueEntry.TYPE_DOCTOR,
            processed=False,
            reason__icontains="lab results ready",
        ).exists() or LabReport.objects.filter(visit=current_visit, sent_to_doctor=True).exists()

        if entry.queue_type == QueueEntry.TYPE_LAB_DOCTOR and not has_results_ready_for_doctor:
            continue

        if entry.queue_type == QueueEntry.TYPE_LAB_RECEPTION:
            has_any_report = LabReport.objects.filter(visit=current_visit).exists()
            already_routed = has_results_ready_for_doctor or current_visit.status in {
                Visit.STATUS_READY_FOR_BILLING,
                Visit.STATUS_COMPLETED,
            } or QueueEntry.objects.filter(
                visit=current_visit,
                queue_type=QueueEntry.TYPE_RECEPTION,
                processed=False,
            ).exists()
            if not has_any_report or not already_routed:
                continue

        entry.processed = True
        entry.processed_at = processed_at
        entry.save(update_fields=["processed", "processed_at"])
        processed_count += 1

    if visit is not None:
        sync_visit_status(visit)
    return processed_count


def report_needs_doctor_send(report: LabReport) -> bool:
    return bool(
        report.visit_id
        and (
            (report.lab_request_id and not report.sent_to_doctor)
            or pending_lab_doctor_entry(report)
        )
    )


def send_report_results_to_doctor(report: LabReport) -> bool:
    if not report.visit_id:
        return False

    if report.requested_visit_service_id:
        mark_visit_service_performed(report.requested_visit_service)

    doctor_request_entry = pending_lab_doctor_entry(report)
    requested_by = None
    if report.lab_request_id and report.lab_request and report.lab_request.requested_by:
        requested_by = report.lab_request.requested_by
    elif doctor_request_entry and doctor_request_entry.requested_by:
        requested_by = doctor_request_entry.requested_by

    mark_open_lab_queue_entries_processed(report.visit)

    if report.lab_request_id and report.lab_request:
        report.lab_request.status = LabRequest.STATUS_COMPLETED
        report.lab_request.save(update_fields=["status"])

    if not report.sent_to_doctor:
        report.sent_to_doctor = True
        report.sent_to_doctor_at = timezone.now()
        report.save(update_fields=["sent_to_doctor", "sent_to_doctor_at"])

    ensure_pending_queue_entry(
        visit=report.visit,
        hospital=report.visit.hospital,
        queue_type=QueueEntry.TYPE_DOCTOR,
        reason=f"Lab results ready for review: {report_test_summary(report)}",
        requested_by=requested_by,
        notes="Laboratory results are ready for clinical review.",
    )

    if requested_by:
        Notification.objects.create(
            user=requested_by,
            notification_type=Notification.TYPE_LAB_RESULT,
            title=f"Lab Results Ready for {report.visit.patient.name}",
            message=f"Results for {report_test_summary(report)} are ready for review.",
            reference_id=report.pk,
        )

    sync_visit_status(report.visit)
    return True


def group_results(report, results):
    grouped = []
    for result in results:
        # Prefer the row's source template/profile for headings (multi-template reports),
        # otherwise fall back to a generic title.
        section_name = (
            result.section_name
            or (result.source_profile.name if getattr(result, "source_profile", None) else None)
            or "Test Results"
        )
        if grouped and grouped[-1]['name'] == section_name:
            grouped[-1]['results'].append(result)
        else:
            grouped.append({'name': section_name, 'results': [result]})
    if len(grouped) == 1:
        grouped[0]["name"] = ""
    return grouped


@login_required
@staff_required
def report_list(request):
    from datetime import date as date_cls
    from collections import defaultdict
    from django.db.models import Count, Max

    base_qs = scoped_reports_queryset(request)

    search = (request.GET.get('search') or '').strip()

    if search:
        filters = (
            Q(patient_name__icontains=search) |
            Q(referred_by__icontains=search) |
            Q(specimen_type__icontains=search)
        )
        base_qs = base_qs.filter(filters)

    base_stats = base_qs.aggregate(
        total=Count("id"),
        printed_total=Count("id", filter=Q(printed=True)),
        draft_total=Count("id", filter=Q(printed=False)),
    )

    patient_details = defaultdict(lambda: {'tests': [], 'technician': None, 'age': '', 'sex': '', 'engines': set()})

    # ---- Legacy (old lab app) side, grouped by patient name ----
    legacy_groups = {
        row['patient_name']: row
        for row in base_qs.values('patient_name').annotate(
            report_count=Count('id'), latest_date=Max('sample_date'), latest_id=Max('id'),
        )
    }
    legacy_reports = (
        base_qs.filter(patient_name__in=legacy_groups.keys())
        .select_related('profile', 'attendant').order_by('-sample_date')
    )
    for r in legacy_reports:
        pd = patient_details[r.patient_name]
        pd['engines'].add('legacy')
        label = r.profile.name if r.profile else r.specimen_type
        if label not in pd['tests']:
            pd['tests'].append(label)
        if not pd['technician']:
            pd['technician'] = r.attendant_name or (
                r.attendant.get_full_name() or r.attendant.username if r.attendant else None
            )
        # Age/sex from most recent report (first encountered, already ordered -sample_date)
        if not pd['age']:
            pd['age'] = r.patient_age
            pd['sex'] = r.get_patient_sex_display()

    # ---- New engine side, grouped by patient name — same hospital scoping ----
    hospital = get_active_hospital(request)
    next_qs = LabOrder.objects.select_related('test', 'visit_service__visit__patient', 'collected_by')
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        next_qs = next_qs.filter(hospital=hospital)
    if search:
        next_qs = next_qs.filter(visit_service__visit__patient__name__icontains=search)

    next_groups = {}
    for order in next_qs.order_by('-created_at'):
        patient = order.visit_service.visit.patient
        name = patient.name
        group = next_groups.setdefault(name, {'report_count': 0, 'latest_date': None, 'latest_visit_id': None, 'patient_id': patient.pk})
        group['report_count'] += 1
        order_date = order.created_at.date()
        if group['latest_date'] is None or order_date > group['latest_date']:
            group['latest_date'] = order_date
            group['latest_visit_id'] = order.visit_service.visit_id

        pd = patient_details[name]
        pd['engines'].add('next')
        if order.test.name not in pd['tests']:
            pd['tests'].append(order.test.name)
        if not pd['technician'] and order.collected_by_id:
            pd['technician'] = order.collected_by.get_full_name() or order.collected_by.username
        if not pd['age']:
            pd['age'] = patient.age
            pd['sex'] = patient.get_sex_display()

    # ---- Merge both sources into one row per patient ----
    all_names = set(legacy_groups.keys()) | set(next_groups.keys())
    merged_rows = []
    for name in all_names:
        legacy = legacy_groups.get(name)
        nxt = next_groups.get(name)
        # legacy sample_date is a DateTimeField (Max() returns a datetime); the new
        # engine's latest_date is already a plain date — normalize before comparing.
        legacy_date = legacy['latest_date'] if legacy else None
        if legacy_date is not None and hasattr(legacy_date, 'date'):
            legacy_date = legacy_date.date()
        dates = [d for d in (legacy_date, nxt['latest_date'] if nxt else None) if d]
        merged_rows.append({
            'patient_name': name,
            'latest_date': max(dates) if dates else None,
            'report_count': (legacy['report_count'] if legacy else 0) + (nxt['report_count'] if nxt else 0),
            'legacy_latest_id': legacy['latest_id'] if legacy else None,
            'next_latest_visit_id': nxt['latest_visit_id'] if nxt else None,
            'next_patient_id': nxt['patient_id'] if nxt else None,
        })
    merged_rows.sort(key=lambda r: r['patient_name'])
    merged_rows.sort(key=lambda r: r['latest_date'] or date_cls.min, reverse=True)

    paginator = Paginator(merged_rows, 20)
    patients = paginator.get_page(request.GET.get('page'))

    context = {
        'patients': patients,
        'patient_details': dict(patient_details),
        'total_reports': base_stats['total'],
        'printed_count': base_stats['printed_total'],
        'draft_count': base_stats['draft_total'],
        'next_engine_count': sum(g['report_count'] for g in next_groups.values()),
        'active_nav': 'dashboard',
        'search': search,
    }
    return render(request, 'lab/report_list.html', context)


@login_required
@staff_required
def patient_reports(request, report_id):
    base_qs = scoped_reports_queryset(request)
    anchor = get_object_or_404(base_qs, pk=report_id)
    patient_name = anchor.patient_name
    reports = (
        base_qs
        .filter(patient_name=patient_name)
        .select_related('profile', 'attendant')
        .order_by('-sample_date', '-created_at')
    )
    context = {
        'patient_name': patient_name,
        'reports': reports,
        'active_nav': 'dashboard',
    }
    return render(request, 'lab/patient_reports.html', context)


def _visit_resume_target(orders):
    """Where should 'continue this patient's lab work' land? The first
    order that hasn't reached ENTERED yet decides — pick_sample if no
    sample collected, enter_result if collected but not entered. Once
    every order is at least ENTERED, there's nothing left to resume: the
    only remaining step is review/release on the report itself."""
    for order in orders:
        if order.stage == OrderStage.PENDING:
            return 'pick_sample', order.pk
        if order.stage == OrderStage.SAMPLE_COLLECTED:
            return 'enter_result', order.pk
    return 'visit_report', None


@login_required
@staff_required
def patient_reports_next(request, patient_id):
    hospital = get_active_hospital(request)
    patients = Patient.objects.all()
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        patients = patients.filter(hospital=hospital)
    patient = get_object_or_404(patients, pk=patient_id)

    orders = (
        LabOrder.objects.filter(visit_service__visit__patient=patient)
        .select_related('test', 'result', 'visit_service__visit')
        .order_by('visit_service__visit_id', 'created_at')
    )

    visits = {}
    for order in orders:
        visit = order.visit_service.visit
        row = visits.setdefault(visit.pk, {'visit': visit, 'orders': []})
        row['orders'].append(order)

    rows = []
    for row in visits.values():
        stages = [o.stage for o in row['orders']]
        resume_url_name, resume_order_id = _visit_resume_target(row['orders'])
        rows.append({
            'visit': row['visit'],
            'orders': row['orders'],
            'all_released': all(s == OrderStage.RELEASED for s in stages),
            'in_progress': resume_url_name != 'visit_report',
            'resume_url_name': resume_url_name,
            'resume_order_id': resume_order_id,
        })
    rows.sort(key=lambda r: r['visit'].visit_date, reverse=True)

    return render(request, 'lab/patient_reports_next.html', {
        'patient': patient,
        'rows': rows,
        'active_nav': 'dashboard',
    })


@login_required
@staff_required
def report_detail(request, pk):
    report = get_object_or_404(scoped_reports_queryset(request), pk=pk)
    cleanup_stale_lab_queue_entries(visit=report.visit if report.visit_id else None)
    results = report.results.select_related('test', 'source_profile').all()
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
            'can_route_direct_lab': direct_lab_can_route(report),
            'active_nav': 'dashboard',
            'hospital': report.hospital,
        },
    )


@login_required
@staff_required
def report_print(request, pk):
    report = get_object_or_404(scoped_reports_queryset(request), pk=pk)
    results = report.results.select_related('test', 'source_profile').all()
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
            'hospital': report.hospital,
        },
    )


@login_required
@staff_required
def report_delete(request, pk):
    report = get_object_or_404(scoped_reports_queryset(request), pk=pk)
    require_admin_override(request.user)
    fallback_url = reverse('report_detail', args=[report.pk])
    cancel_url = resolve_next_url(request, fallback_url)
    if request.method == 'POST':
        reason = (request.POST.get('admin_reason') or '').strip()
        if not reason:
            messages.error(request, "Enter the reason for deleting this lab report.")
        else:
            details = {
                'patient_name': report.patient_name,
                'visit_id': report.visit_id,
                'requested_visit_service_id': report.requested_visit_service_id,
                'reason': reason,
            }
            if report.requested_visit_service_id:
                report.requested_visit_service.performed = False
                report.requested_visit_service.performed_at = None
                report.requested_visit_service.save(update_fields=['performed', 'performed_at'])
            report_id_value = report.pk
            report.delete()
            record_admin_override(
                actor=request.user,
                hospital=get_active_hospital(request),
                action='delete_lab_report',
                model_name='LabReport',
                object_id=report_id_value,
                details=details,
            )
            messages.success(request, 'Report deleted.')
            return redirect(resolve_next_url(request, reverse('report_list')))
    return render(
        request,
        'admin_override_confirm.html',
        {
            'dashboard_title': 'Delete Lab Report',
            'dashboard_intro': 'Remove this saved lab report from the hospital records.',
            'object_label': f'{report.patient_name} - Report #{report.pk}',
            'object_type': 'lab report',
            'danger_note': 'This permanently removes the saved report and reopens the linked lab service if one was attached.',
            'confirm_label': 'Delete Lab Report',
            'cancel_href': cancel_url,
            'next_url': resolve_next_url(request, reverse('report_list')),
        },
    )


@login_required
@staff_required
@transaction.atomic
def send_lab_result_to_doctor(request, report_id):
    """Send lab results to requesting doctor and create notification"""
    hospital = get_active_hospital(request)
    reports = LabReport.objects.select_related('lab_request__requested_by', 'visit__patient', 'visit__hospital')
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        reports = reports.filter(hospital=hospital)
    
    report = get_object_or_404(reports, pk=report_id)
    
    if report.sent_to_doctor:
        messages.warning(request, "This report has already been sent to the doctor.")
        return redirect('report_detail', pk=report.pk)
    send_report_results_to_doctor(report)
    cleanup_stale_lab_queue_entries(visit=report.visit if report.visit_id else None)
    messages.success(request, "Lab results sent to doctor. Results appear in doctor queue and notification created.")
    return redirect('report_detail', pk=report.pk)


@login_required
@staff_required
@transaction.atomic
def route_lab_report(request, report_id):
    if request.method != "POST":
        return redirect("report_detail", pk=report_id)

    hospital = get_active_hospital(request)
    reports = scoped_reports_queryset(request)
    if hospital and getattr(request.user, "role", "") != "superadmin":
        reports = reports.filter(hospital=hospital)
    report = get_object_or_404(reports, pk=report_id)
    if report.visit and report.visit.status == Visit.STATUS_CANCELLED:
        messages.error(request, "This visit was terminated by an administrator and cannot be routed further.")
        return redirect("report_detail", pk=report.pk)

    if not report.visit_id:
        messages.error(request, "This report is not linked to a visit, so it cannot be routed.")
        return redirect("report_detail", pk=report.pk)

    reconcile_lab_visit_services(report.visit)
    if lab_visit_services(report.visit, performed=False).exists():
        messages.error(request, "Finish all pending lab services before routing this patient out of the lab.")
        return redirect("report_detail", pk=report.pk)

    destination = (request.POST.get("destination") or "").strip()
    if destination == "doctor":
        ensure_pending_queue_entry(
            visit=report.visit,
            hospital=report.visit.hospital,
            queue_type=QueueEntry.TYPE_DOCTOR,
            reason=f"Lab results ready for review: {report_test_summary(report)}",
            requested_by=request.user,
            notes="Lab routed this patient to doctor review after completing direct lab work.",
        )
        if not report.sent_to_doctor:
            report.sent_to_doctor = True
            report.sent_to_doctor_at = timezone.now()
            report.save(update_fields=["sent_to_doctor", "sent_to_doctor_at"])
        message = "Patient sent to doctor for review."
    else:
        send_to_reception_queue(
            visit=report.visit,
            hospital=report.visit.hospital,
            source="Lab",
            detail=f"Lab completed: {report_test_summary(report)}",
            notes="Reception should decide whether to bill, dispense drugs, or send the patient to doctor review.",
            requested_by=request.user,
        )
        message = "Patient sent back to receptionist queue."

    mark_open_lab_queue_entries_processed(report.visit)
    sync_visit_status(report.visit)
    cleanup_stale_lab_queue_entries(visit=report.visit)
    messages.success(request, message)
    return redirect("report_detail", pk=report.pk)


@login_required
@staff_required
@transaction.atomic
def lab_settings(request):
    hospital = get_active_hospital(request)
    is_admin = getattr(request.user, 'role', '') in ('admin', 'superadmin') or request.user.can_access_hospital_admin

    consumables = LabConsumable.objects.filter(hospital=hospital).order_by('name')

    if request.method == 'POST' and is_admin:
        action = request.POST.get('action', '')

        if action == 'add':
            form = LabConsumableForm(request.POST)
            if form.is_valid():
                obj = form.save(commit=False)
                obj.hospital = hospital
                try:
                    obj.save()
                    messages.success(request, f'"{obj.name}" added to lab inventory.')
                except Exception:
                    messages.error(request, 'A consumable with that name already exists.')
            else:
                messages.error(request, 'Please fix the form errors below.')
            return redirect('lab_settings')

        if action == 'adjust':
            pk = request.POST.get('pk')
            new_qty = request.POST.get('quantity')
            obj = get_object_or_404(LabConsumable, pk=pk, hospital=hospital)
            try:
                obj.current_quantity = max(0, float(new_qty))
                obj.save(update_fields=['current_quantity', 'updated_at'])
                messages.success(request, f'Stock for "{obj.name}" updated to {obj.current_quantity}.')
            except (TypeError, ValueError):
                messages.error(request, 'Invalid quantity value.')
            return redirect('lab_settings')

        if action == 'toggle':
            pk = request.POST.get('pk')
            obj = get_object_or_404(LabConsumable, pk=pk, hospital=hospital)
            obj.is_active = not obj.is_active
            obj.save(update_fields=['is_active', 'updated_at'])
            state = 'activated' if obj.is_active else 'deactivated'
            messages.success(request, f'"{obj.name}" {state}.')
            return redirect('lab_settings')

        if action == 'delete':
            pk = request.POST.get('pk')
            obj = get_object_or_404(LabConsumable, pk=pk, hospital=hospital)
            name = obj.name
            obj.delete()
            messages.success(request, f'"{name}" deleted.')
            return redirect('lab_settings')

    form = LabConsumableForm() if is_admin else None
    return render(request, 'lab/lab_settings.html', {
        'consumables': consumables,
        'form': form,
        'is_admin': is_admin,
        'active_nav': 'lab_settings',
    })


# ===========================================================================
# Lab module rework — merged in from the former `lab_next` app. Reuses
# staff_required / get_active_hospital defined above; nothing duplicated.
# ===========================================================================

def _parse_age_years(age_str):
    """'22YRS' -> 22, '6MTH' -> 0 (under a year — range bands are in whole
    years, an infant reads as age 0 for matching purposes)."""
    if not age_str:
        return None
    raw = age_str.upper().strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    value = int(digits)
    if "MTH" in raw or "MON" in raw:
        return 0
    return value


def _pending_lab_visit_services(visit):
    return (
        VisitService.objects.filter(visit=visit, service__category=Service.CATEGORY_LAB, performed=False)
        .prefetch_related("service__lab_tests_next", "lab_orders_next")
    )


def _ensure_orders_for_visit(visit):
    """Every pending lab VisitService whose service has been mapped to one
    or more LabTests gets a LabOrder per linked test that doesn't have one
    yet -- a bundled service (e.g. "Malaria Test" -> MRDT + B/S) is billed
    once but fans out into one order per test, each entered independently.
    Services not yet mapped to any LabTest are reported back so the queue
    screen can flag them instead of silently doing nothing."""
    unmapped = []
    for vs in _pending_lab_visit_services(visit):
        tests = list(vs.service.lab_tests_next.all())
        if not tests:
            unmapped.append(vs)
            continue
        existing_test_ids = {o.test_id for o in vs.lab_orders_next.all()}
        patient = visit.patient
        for test in tests:
            if test.pk in existing_test_ids:
                continue
            LabOrder.objects.create(
                visit_service=vs,
                test=test,
                hospital=visit.hospital,
                patient_sex={"M": Sex.MALE, "F": Sex.FEMALE}.get(patient.sex, Sex.ANY),
                patient_age_years=_parse_age_years(patient.age),
            )
    return unmapped


@login_required
@staff_required
def queue(request):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.filter(
        queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
        processed=False,
    ).select_related("visit__patient", "hospital")
    if hospital and getattr(request.user, "role", "") != "superadmin":
        queue_entries = queue_entries.filter(hospital=hospital)

    rows = []
    for entry in queue_entries.order_by("created_at"):
        visit = entry.visit
        unmapped = _ensure_orders_for_visit(visit)
        # Only orders still needing sample collection or result entry belong
        # here — once entered, that's Reviewing Results' job, not this
        # queue's. Without this, an order sat on both screens at once from
        # the moment it was entered until it was actually released.
        orders = (
            LabOrder.objects.filter(visit_service__visit=visit)
            .filter(stage__in=[OrderStage.PENDING, OrderStage.SAMPLE_COLLECTED])
            .select_related("test", "result")
        )
        if not orders.exists() and not unmapped:
            continue
        if visit.is_fully_paid:
            payment_status = "paid"
        elif visit.is_unbilled:
            payment_status = "unpaid"
        else:
            payment_status = "partial"
        billed_service_names = ", ".join(visit.visit_services.values_list("service__name", flat=True))
        rows.append({
            "entry": entry, "orders": orders, "unmapped": unmapped,
            "payment_status": payment_status, "balance_due": visit.balance_due,
            "billed_service_names": billed_service_names,
        })

    return render(request, "lab/queue.html", {"rows": rows, "active_nav": "lab_queue"})


@login_required
@staff_required
def reviewing_results(request):
    """A focused, hospital-wide worklist of everything sitting at ENTERED —
    results are in but not yet reviewed/released. `queue` already lists
    in-flight orders per visit, but there's no single place to see just
    "what's waiting on me to review" across every patient at once; this is
    that screen. Review/release itself isn't duplicated here — each card
    links into the same visit_report page that already owns that logic
    (mark reviewed, release, and the doctor-vs-reception routing on
    release), so there's one source of truth for what release actually does."""
    hospital = get_active_hospital(request)
    orders = (
        LabOrder.objects.filter(stage=OrderStage.ENTERED)
        .select_related("test", "result", "visit_service__visit__patient")
        .order_by("visit_service__visit_id", "created_at")
    )
    if hospital and getattr(request.user, "role", "") != "superadmin":
        orders = orders.filter(hospital=hospital)

    settings_row = getattr(hospital, "lab_settings_next", None)
    payment_required_before_release = bool(settings_row and settings_row.payment_required_before_release)

    groups = {}
    ordered_visit_ids = []
    for order in orders:
        visit = order.visit_service.visit
        if visit.pk not in groups:
            is_doctor_request = QueueEntry.objects.filter(
                visit=visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False,
            ).exists()
            if visit.is_fully_paid:
                payment_status = "paid"
            elif visit.is_unbilled:
                payment_status = "unpaid"
            else:
                payment_status = "partial"
            groups[visit.pk] = {
                "visit": visit, "orders": [], "is_doctor_request": is_doctor_request,
                "payment_status": payment_status, "balance_due": visit.balance_due,
                # Doctor-requested results go back to the doctor even with a
                # balance outstanding — only printing stays gated on payment.
                "release_blocked_by_payment": payment_required_before_release and not visit.is_fully_paid and not is_doctor_request,
                "billed_service_names": ", ".join(visit.visit_services.values_list("service__name", flat=True)),
            }
            ordered_visit_ids.append(visit.pk)
        groups[visit.pk]["orders"].append(order)

    rows = [groups[vid] for vid in ordered_visit_ids]
    return render(request, "lab/reviewing_results.html", {"rows": rows, "active_nav": "lab_reviewing_results"})


def _payload_from_post(test, post):
    if test.result_type == ResultType.FREE_ENTRY:
        return {"free_text": post.get("free_text", "")}
    if test.result_type == ResultType.DEFINED_OPTION:
        return {"chosen_option": post.get("chosen_option", "")}
    if test.result_type == ResultType.PARAMETER_PANEL:
        values = [
            {"parameter_id": p.pk, "value": post.get(f"param_{p.pk}", "")}
            for p in test.parameters.all()
            if post.get(f"param_{p.pk}", "").strip()
        ]
        return {"values": values, "bench_notes": post.get("bench_notes", "")}
    if test.result_type == ResultType.CULTURE:
        sensitivities = [
            {"antibiotic_id": a.pk, "sir": post.get(f"abx_{a.pk}", "")}
            for a in test.antibiotics.all()
            if post.get(f"abx_{a.pk}", "")
        ]
        return {"organism": post.get("organism", ""), "sensitivities": sensitivities}
    return {}


@login_required
@staff_required
def pick_sample(request, order_id):
    hospital = get_active_hospital(request)
    orders = LabOrder.objects.select_related("test", "visit_service__visit__patient")
    if hospital and getattr(request.user, "role", "") != "superadmin":
        orders = orders.filter(hospital=hospital)
    order = get_object_or_404(orders, pk=order_id)
    visit = order.visit_service.visit

    settings_row = getattr(order.hospital, "lab_settings_next", None)
    outside_enabled = settings_row.outside_samples_enabled if settings_row else True

    if request.method == "POST":
        specimen_id = request.POST.get("specimen")
        specimen = order.test.accepted_specimens.filter(pk=specimen_id).first() if specimen_id else None
        outside_source = (request.POST.get("outside_source", "").strip() if outside_enabled else "")
        notes = request.POST.get("collection_notes", "").strip()
        order.mark_sample_collected(
            user=request.user, specimen=specimen, when=timezone.now(),
            outside_source=outside_source, notes=notes,
        )
        order.priority_routine = not request.POST.get("urgent")
        order.save(update_fields=["priority_routine"])
        messages.success(request, f"Sample collected for {order.test.name}.")
        return redirect("enter_result", order_id=order.pk)

    return render(request, "lab/pick_sample.html", {
        "order": order, "visit": visit, "outside_enabled": outside_enabled,
    })


@login_required
@staff_required
def enter_result(request, order_id):
    hospital = get_active_hospital(request)
    orders = LabOrder.objects.select_related("test", "visit_service__visit__patient", "visit_service__service")
    if hospital and getattr(request.user, "role", "") != "superadmin":
        orders = orders.filter(hospital=hospital)
    order = get_object_or_404(orders, pk=order_id)
    visit = order.visit_service.visit

    if order.stage == OrderStage.PENDING:
        messages.error(request, "Collect the sample before entering results.")
        return redirect("pick_sample", order_id=order.pk)

    if request.method == "POST":
        payload = _payload_from_post(order.test, request.POST)
        save_results(order, request.user, payload)

        # A bundled service (e.g. "Malaria Test" -> MRDT + B/S) shares one
        # VisitService across several LabOrders -- only mark it performed
        # once every linked order has actually moved past sample collection,
        # not the moment any single one of them gets entered.
        order.visit_service.performed = not (
            order.visit_service.lab_orders_next.exclude(pk=order.pk)
            .filter(stage__in=[OrderStage.PENDING, OrderStage.SAMPLE_COLLECTED])
            .exists()
        )
        order.visit_service.save(update_fields=["performed"])

        messages.success(request, f"{order.test.name} saved.")

        if not _pending_lab_visit_services(visit).exists():
            # Entered, not yet routed anywhere — routing happens at release
            # (see visit_report's "release" action), so reception/doctor
            # never act on a report still awaiting review or payment. But if
            # there's still a balance, flip the visit to READY_FOR_BILLING
            # right now rather than waiting for a blocked release attempt to
            # surface it — that's what puts it on reception's "Ready for
            # Billing" dashboard so someone actually goes and clears it,
            # instead of the report just sitting silently in Reviewing
            # Results until someone happens to try releasing it.
            if not visit.is_fully_paid and visit.status not in (Visit.STATUS_CANCELLED, Visit.STATUS_COMPLETED):
                visit.status = Visit.STATUS_READY_FOR_BILLING
                visit.save(update_fields=["status"])
            messages.success(request, "All requested tests are complete. Review and release the report to send this patient onward.")
            return redirect("visit_report", visit_id=visit.pk)

        return redirect("queue")

    hydrated = hydrate_entry_form(order)
    return render(request, "lab/enter_result.html", {"order": order, "visit": visit, "hydrated": hydrated})


# ---------------------------------------------------------------------------
# Phlebotomy — the pre-billing intake step. Only reachable for hospitals with
# LabSettings.phlebotomy_enabled; not tied to `engine`, since a hospital
# could plausibly want phlebotomy without being on the new result-entry
# engine yet. In practice today the queue/enter-result screens above are
# 'next'-only, so phlebotomy effectively implies 'next' for now.
# ---------------------------------------------------------------------------

def phlebotomy_enabled_for(hospital):
    """The one function reception imports (lazily) to decide whether to show
    its 'Send to Phlebotomy' button at all — see
    reception.views.receptionist_queue_send_to_phlebotomy, which owns the
    actual queue-routing action since it needs reception's own
    close_reception_queue_for_visit / reception_queue_other_open_work guards,
    already used by its send-to-doctor/send-to-sonographer siblings."""
    settings_row = getattr(hospital, "lab_settings_next", None)
    return bool(settings_row and settings_row.phlebotomy_enabled)


@login_required
@staff_required
def phlebotomy_queue(request):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.filter(
        queue_type=QueueEntry.TYPE_PHLEBOTOMY, processed=False,
    ).select_related("visit__patient", "hospital")
    if hospital and getattr(request.user, "role", "") != "superadmin":
        queue_entries = queue_entries.filter(hospital=hospital)
    return render(request, "lab/phlebotomy_queue.html", {
        "queue_entries": queue_entries.order_by("created_at"),
        "active_nav": "phlebotomy_queue",
    })


@login_required
@staff_required
def phlebotomy_intake(request, queue_entry_id):
    hospital = get_active_hospital(request)
    entries = QueueEntry.objects.filter(queue_type=QueueEntry.TYPE_PHLEBOTOMY).select_related(
        "visit__patient", "visit__hospital",
    )
    if hospital and getattr(request.user, "role", "") != "superadmin":
        entries = entries.filter(hospital=hospital)
    entry = get_object_or_404(entries, pk=queue_entry_id)
    visit = entry.visit

    added_services = VisitService.objects.filter(
        visit=visit, service__category=Service.CATEGORY_LAB,
    ).select_related("service")
    available_services = (
        Service.objects.filter(hospital=visit.hospital, category=Service.CATEGORY_LAB, is_active=True)
        .exclude(pk__in=added_services.values_list("service_id", flat=True))
        .order_by("name")
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_service":
            service = get_object_or_404(
                Service, pk=request.POST.get("service_id"), hospital=visit.hospital, category=Service.CATEGORY_LAB,
            )
            if VisitService.objects.filter(visit=visit, service=service).exists():
                messages.error(request, f"{service.name} is already on this visit — it's already under way, not added again.")
            else:
                VisitService.objects.create(visit=visit, service=service, price_at_time=service.price)
                visit.total_amount = (visit.total_amount or Decimal("0")) + service.price
                visit.save(update_fields=["total_amount"])
                messages.success(request, f"{service.name} added.")
            return redirect("phlebotomy_intake", queue_entry_id=entry.pk)

        if action == "remove_service":
            vs = get_object_or_404(
                VisitService, pk=request.POST.get("visit_service_id"), visit=visit, service__category=Service.CATEGORY_LAB,
            )
            try:
                release_visit_service_for_lab(vs)
            except DjangoValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("phlebotomy_intake", queue_entry_id=entry.pk)
            visit.total_amount = max(Decimal("0"), (visit.total_amount or Decimal("0")) - vs.price_at_time)
            visit.save(update_fields=["total_amount"])
            vs.delete()
            return redirect("phlebotomy_intake", queue_entry_id=entry.pk)

        if action == "send_to_reception":
            notes = request.POST.get("notes", "").strip()
            if not added_services.exists():
                messages.error(request, "Add at least one lab service before sending to reception.")
                return redirect("phlebotomy_intake", queue_entry_id=entry.pk)
            if notes:
                # Carry the assessment onto every test added here so it
                # shows up as Clinical Notes on the final report — the
                # queue entry's own notes field doesn't reach the report.
                added_services.update(notes=notes)
            entry.notes = notes
            entry.processed = True
            entry.processed_at = timezone.now()
            entry.save(update_fields=["notes", "processed", "processed_at"])
            send_to_reception_queue(
                visit=visit, hospital=visit.hospital, source="Phlebotomy",
                detail="Lab tests added during phlebotomy intake.",
                notes=notes, requested_by=request.user,
            )
            sync_visit_status(visit)
            messages.success(request, f"{visit.patient.name} sent to reception for approval and billing.")
            return redirect("phlebotomy_queue")

    return render(request, "lab/phlebotomy_intake.html", {
        "entry": entry, "visit": visit,
        "available_services": available_services, "added_services": added_services,
        "active_nav": "phlebotomy_queue",
    })


# ---------------------------------------------------------------------------
# Report — review, release, print. The tail end of the pipeline: results
# entered on individual LabOrders are grouped per-visit into one report here.
# ---------------------------------------------------------------------------

@login_required
@staff_required
def visit_report(request, visit_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("patient", "hospital")
    if hospital and getattr(request.user, "role", "") != "superadmin":
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)

    orders = (
        LabOrder.objects.filter(visit_service__visit=visit)
        .select_related("test", "result")
        .prefetch_related("result__values")
        .order_by("created_at")
    )
    if not orders.exists():
        messages.error(request, "No lab orders exist for this visit yet.")
        return redirect("queue")

    settings_row = getattr(visit.hospital, "lab_settings_next", None)
    require_review = settings_row.require_review_before_release if settings_row else True
    payment_gate = settings_row.payment_required_before_release if settings_row else False

    stages = [o.stage for o in orders]
    all_entered_or_later = all(s in (OrderStage.ENTERED, OrderStage.REVIEWED, OrderStage.RELEASED) for s in stages)
    all_reviewed_or_later = all(s in (OrderStage.REVIEWED, OrderStage.RELEASED) for s in stages)
    all_released = all(s == OrderStage.RELEASED for s in stages)

    can_review = require_review and all_entered_or_later and not all_reviewed_or_later
    can_release = (all_reviewed_or_later if require_review else all_entered_or_later) and not all_released

    is_doctor_request = QueueEntry.objects.filter(
        visit=visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False,
    ).exists()
    # Doctor-requested results are allowed back to the requesting doctor
    # even with a balance outstanding — only printing the report stays
    # gated on payment, for every visit regardless of who requested it.
    payment_blocked = payment_gate and not visit.is_fully_paid
    release_payment_blocked = payment_blocked and not is_doctor_request

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "mark_reviewed" and can_review:
            for order in orders:
                if order.stage == OrderStage.ENTERED:
                    order.stage = OrderStage.REVIEWED
                    order.save(update_fields=["stage"])
                    if hasattr(order, "result"):
                        order.result.reviewed_by = request.user
                        order.result.reviewed_at = timezone.now()
                        order.result.save(update_fields=["reviewed_by", "reviewed_at"])
            messages.success(request, "Results marked reviewed.")
            return redirect("visit_report", visit_id=visit.pk)

        if action == "release":
            if release_payment_blocked:
                messages.error(request, "This visit's balance must be settled before results can be released.")
                return redirect("visit_report", visit_id=visit.pk)
            if not can_release:
                messages.error(request, "Not all results are ready to release yet.")
                return redirect("visit_report", visit_id=visit.pk)
            release_time = timezone.now()
            for order in orders:
                order.stage = OrderStage.RELEASED
                order.save(update_fields=["stage"])
                if hasattr(order, "result"):
                    order.result.released_by = request.user
                    order.result.released_at = release_time
                    order.result.save(update_fields=["released_by", "released_at"])

            # Route onward only now that the report is actually final — not
            # at raw entry, so reception/doctor never act on a draft that's
            # still awaiting review or payment. Doctor-requested work goes
            # back to the requesting doctor; everything else (reception or
            # self-test) goes back to reception for billing/next step.
            doctor_entry = (
                QueueEntry.objects.filter(visit=visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR, processed=False)
                .select_related("requested_by")
                .order_by("created_at")
                .first()
            )
            mark_queue_entries_processed(visit=visit, queue_type=QueueEntry.TYPE_LAB_RECEPTION)
            mark_queue_entries_processed(visit=visit, queue_type=QueueEntry.TYPE_LAB_DOCTOR)
            test_names = ", ".join(o.test.name for o in orders[:4])

            if doctor_entry:
                ensure_pending_queue_entry(
                    visit=visit, hospital=visit.hospital, queue_type=QueueEntry.TYPE_DOCTOR,
                    reason=f"Lab results ready for review: {test_names}",
                    requested_by=doctor_entry.requested_by,
                    notes="Laboratory results are ready for clinical review.",
                )
                if doctor_entry.requested_by:
                    Notification.objects.create(
                        user=doctor_entry.requested_by,
                        notification_type=Notification.TYPE_LAB_RESULT,
                        title=f"Lab Results Ready for {visit.patient.name}",
                        message=f"Results for {test_names} are ready for review.",
                        reference_id=visit.pk,
                    )
                messages.success(request, "Report released and sent to the requesting doctor.")
            else:
                send_to_reception_queue(
                    visit=visit, hospital=visit.hospital, source="Lab",
                    detail=f"Lab completed: {test_names}",
                    notes="Lab work completed and released. Reception should decide billing/next step.",
                    requested_by=request.user,
                )
                messages.success(request, "Report released and sent back to reception.")

            sync_visit_status(visit)
            return redirect("visit_report", visit_id=visit.pk)

    combine_defined_option_reports = settings_row.combine_defined_option_reports if settings_row else False
    orders_list = list(orders)
    if combine_defined_option_reports:
        # order.result is a reverse OneToOne accessor -- it raises
        # RelatedObjectDoesNotExist (not None) when no LabResult exists yet,
        # e.g. an order still sitting unentered in the lab queue. getattr
        # with a default sidesteps that; a plain `order.result` crashed this
        # whole page the moment a visit had any order still awaiting entry.
        combined_orders = [
            o for o in orders_list
            if getattr(o, "result", None) and o.result.result_type == ResultType.DEFINED_OPTION
        ]
        standalone_orders = [o for o in orders_list if o not in combined_orders]
    else:
        combined_orders = []
        standalone_orders = orders_list

    return render(request, "lab/report.html", {
        "visit": visit,
        "orders": orders,
        "combined_orders": combined_orders,
        "standalone_orders": standalone_orders,
        "can_review": can_review,
        "can_release": can_release,
        "payment_blocked": payment_blocked,
        "release_payment_blocked": release_payment_blocked,
        "is_doctor_request": is_doctor_request,
        "require_review": require_review,
        "all_released": all_released,
        "show_report_footnote": settings_row.show_report_footnote if settings_row else True,
        "combine_defined_option_reports": combine_defined_option_reports,
    })
