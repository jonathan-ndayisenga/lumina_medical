import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import User
from reception.models import QueueEntry, Service, Visit, VisitService
from reception.workflow import (
    ensure_pending_queue_entry,
    record_admin_override,
    require_admin_override,
    send_to_reception_queue,
    sync_visit_status,
)
from doctor.models import LabRequest, Notification

from .forms import LabReportForm, TestResultFormSet
from .models import (
    LabReport,
    ReferenceRangeDefault,
    TestCatalog,
    TestProfile,
    TestProfileParameter,
)

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


def sync_report_snapshot_from_visit(report: LabReport) -> None:
    if not report.visit_id:
        return
    patient = report.visit.patient
    report.patient_name = patient.name
    report.patient_age = patient.age
    report.patient_sex = patient.sex
    report.hospital = report.visit.hospital


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


def serialize_requested_service(visit_service):
    profile = getattr(visit_service.service, "test_profile", None)
    report = getattr(visit_service, "lab_report", None)
    return {
        "visit_service_id": visit_service.pk,
        "service_id": visit_service.service_id,
        "service_name": visit_service.service.name,
        "performed": visit_service.performed,
        "performed_at": visit_service.performed_at.isoformat() if visit_service.performed_at else "",
        "test_profile_id": profile.pk if profile else None,
        "test_profile_name": profile.name if profile else "",
        "report_id": report.pk if report else None,
    }


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


def ensure_report_for_visit_service(visit_service, *, attendant=None):
    report = getattr(visit_service, "lab_report", None)
    if report:
        return report

    visit = visit_service.visit
    patient = visit.patient
    profile = getattr(visit_service.service, "test_profile", None)
    doctor_entry = (
        QueueEntry.objects.filter(
            visit=visit,
            queue_type=QueueEntry.TYPE_LAB_DOCTOR,
        )
        .select_related("requested_by")
        .order_by("-created_at")
        .first()
    )
    referred_by = ""
    if doctor_entry and doctor_entry.requested_by:
        referred_by = doctor_entry.requested_by.get_full_name() or doctor_entry.requested_by.username

    report = LabReport.objects.create(
        profile=profile,
        hospital=visit.hospital,
        visit=visit,
        requested_visit_service=visit_service,
        patient_name=patient.name,
        patient_age=patient.age,
        patient_sex=patient.sex,
        referred_by=referred_by,
        sample_date=timezone.now().date(),
        specimen_type=(profile.default_specimen_type if profile and profile.default_specimen_type else "BLOOD"),
        attendant=attendant,
        attendant_name=(attendant.get_full_name() or attendant.username) if attendant else "",
    )
    return report


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


def report_ready_to_send_to_doctor(report: LabReport) -> bool:
    return bool(report_needs_doctor_send(report) and not lab_visit_services(report.visit, performed=False).exists())


def mark_lab_queue_complete(report: LabReport) -> bool:
    if not report.visit_id:
        return False
    pending_lab_entries = list(
        QueueEntry.objects.filter(
            visit=report.visit,
            queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
            processed=False,
        )
    )
    mark_open_lab_queue_entries_processed(report.visit)
    doctor_request_entry = next(
        (entry for entry in pending_lab_entries if entry.queue_type == QueueEntry.TYPE_LAB_DOCTOR),
        None,
    )
    if doctor_request_entry:
        ensure_pending_queue_entry(
            visit=report.visit,
            hospital=report.visit.hospital,
            queue_type=QueueEntry.TYPE_DOCTOR,
            reason=f"Lab results ready for: {report_test_summary(report)}",
            requested_by=doctor_request_entry.requested_by,
            notes="Laboratory work completed and ready for doctor review.",
        )
    else:
        send_to_reception_queue(
            visit=report.visit,
            hospital=report.visit.hospital,
            source="Lab",
            detail=f"Lab completed: {report_test_summary(report)}",
            notes="Lab work completed from a reception referral. Reception should review billing, dispensing, or doctor follow-up.",
            requested_by=report.attendant,
        )
    sync_visit_status(report.visit)
    return doctor_request_entry is not None


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


def get_age_category(age_str: str, sex: str = "") -> str:
    """Convert age text into an age category for defaults."""
    if not age_str:
        return 'general'
    age_str = age_str.upper().strip()
    digits = ''.join(filter(str.isdigit, age_str))
    if not digits:
        return 'general'
    sex = (sex or "").upper().strip()
    age_value = int(digits)
    if any(token in age_str for token in ("DAY", "DYS", "DAYS")):
        return "neonate" if age_value <= 28 else "child"
    if 'M' in age_str:
        return 'neonate' if age_value == 0 else 'child'
    if 'Y' in age_str:
        if age_value < 18:
            return 'child'
        if sex == "F":
            return "woman"
        if sex == "M":
            return "man"
        return "general"
    return 'general'


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
    age_category = get_age_category(report.patient_age, report.patient_sex)

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
        instance.source_profile = cleaned.get("source_profile")
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


def build_report_form_context(form, formset, **extra_context):
    profiles = TestProfile.objects.filter(is_active=True).prefetch_related('parameters__test')
    pending_requested_services = extra_context.pop("pending_requested_services", [])
    completed_requested_services = extra_context.pop("completed_requested_services", [])
    selected_requested_service_id = extra_context.pop("selected_requested_service_id", "")

    context = {
        'form': form,
        'formset': formset,
        'existing_tests': list(TestCatalog.objects.order_by('name').values_list('name', flat=True)),
        'test_profiles': profiles,
        'test_profile_payload': serialize_profile_payload(profiles),
        'active_nav': 'lab_queue',
        'pending_requested_services': pending_requested_services,
        'completed_requested_services': completed_requested_services,
        'selected_requested_service_id': str(selected_requested_service_id or ""),
    }
    context.update(extra_context)
    return context


def handle_report_form(request, report=None):
    """Shared create/edit handler for report entry."""
    is_edit = report is not None
    report = report or LabReport(attendant=request.user)

    if report.visit_id:
        sync_report_snapshot_from_visit(report)
    elif not report.hospital_id:
        report.hospital = get_active_hospital(request)

    pending_requested_services = []
    completed_requested_services = []
    selected_requested_service_id = (request.POST.get("requested_service_id") or request.GET.get("requested_service_id") or "").strip()
    if report.visit_id and selected_requested_service_id and not report.requested_visit_service_id:
        selected_service = VisitService.objects.filter(
            visit=report.visit,
            service__category="lab",
            pk=selected_requested_service_id,
        ).select_related("service__test_profile").first()
        if selected_service and not getattr(selected_service, "lab_report", None):
            report.requested_visit_service = selected_service
            if selected_service.service.test_profile_id and not report.profile_id:
                report.profile = selected_service.service.test_profile
            if report.pk:
                report.save(update_fields=["requested_visit_service", "profile"])
    if report.visit_id:
        pending_services_qs = list(lab_visit_services(report.visit, performed=False))
        completed_services_qs = list(lab_visit_services(report.visit, performed=True))
        for visit_service in pending_services_qs:
            ensure_report_for_visit_service(visit_service, attendant=request.user)
        pending_requested_services = [serialize_requested_service(item) for item in pending_services_qs]
        completed_requested_services = [serialize_requested_service(item) for item in completed_services_qs]

    if (
        request.method == "GET"
        and report.visit_id
        and report.requested_visit_service_id
        and selected_requested_service_id
        and str(report.requested_visit_service_id) != str(selected_requested_service_id)
    ):
        selected_service = VisitService.objects.filter(
            visit=report.visit,
            service__category="lab",
            pk=selected_requested_service_id,
        ).first()
        if selected_service:
            target_report = ensure_report_for_visit_service(selected_service, attendant=request.user)
            return redirect(
                f"{reverse('report_edit', kwargs={'pk': target_report.pk})}?requested_service_id={selected_service.pk}"
            )

    if report.requested_visit_service_id and not selected_requested_service_id:
        selected_requested_service_id = str(report.requested_visit_service_id)

    can_send_to_doctor = report_ready_to_send_to_doctor(report)

    if request.method == 'POST':
        form = LabReportForm(request.POST, instance=report)
        formset = TestResultFormSet(request.POST, instance=report)
        if form.is_valid() and formset.is_valid():
            action = request.POST.get("action", "save_report")
            selected_visit_service = None
            if report.visit_id and pending_requested_services:
                selected_visit_service = next(
                    (item for item in lab_visit_services(report.visit, performed=False) if str(item.pk) == selected_requested_service_id),
                    None,
                )
                if action == "send_to_doctor":
                    form.add_error(None, "Finish every requested lab test before sending results to the doctor.")
                elif not selected_visit_service:
                    form.add_error(None, "Choose the requested lab test you are working on before saving this report.")

        if form.is_valid() and formset.is_valid() and not form.non_field_errors():
            action = request.POST.get("action", "save_report")
            report = form.save(commit=False)
            if report.visit_id:
                sync_report_snapshot_from_visit(report)
            elif not report.hospital_id:
                report.hospital = get_active_hospital(request)
            if report.profile and not report.specimen_type:
                report.specimen_type = report.profile.default_specimen_type
            if not report.attendant:
                report.attendant = request.user
            if not report.attendant_name:
                report.attendant_name = request.user.get_full_name() or request.user.username
            report.save()
            save_results_from_formset(report, formset)

            selected_visit_service = None
            if report.visit_id and selected_requested_service_id:
                selected_visit_service = lab_visit_services(report.visit, performed=False).filter(pk=selected_requested_service_id).first()
                if selected_visit_service:
                    if not report.requested_visit_service_id:
                        report.requested_visit_service = selected_visit_service
                    if selected_visit_service.service.test_profile_id and not report.profile_id:
                        report.profile = selected_visit_service.service.test_profile
                    report.save(update_fields=["requested_visit_service", "profile"])
                    mark_visit_service_performed(selected_visit_service)
                    refresh_lab_doctor_queue_reason(report.visit)

            remaining_pending_services = []
            if report.visit_id:
                remaining_pending_services = list(lab_visit_services(report.visit, performed=False))

            if selected_visit_service and remaining_pending_services:
                next_pending_service = remaining_pending_services[0]
                next_report = ensure_report_for_visit_service(next_pending_service, attendant=request.user)
                messages.success(
                    request,
                    f"{selected_visit_service.service.name} saved. Loading the next pending test so you can finish this request in one pass.",
                )
                return redirect(
                    f"{reverse('report_edit', kwargs={'pk': next_report.pk})}?requested_service_id={next_pending_service.pk}"
                )

            if action == "send_to_doctor" and report_ready_to_send_to_doctor(report):
                send_report_results_to_doctor(report)
                cleanup_stale_lab_queue_entries(visit=report.visit)
                messages.success(request, "Report saved and sent to doctor.")
                return redirect('report_detail', pk=report.pk)

            if report_needs_doctor_send(report) and not remaining_pending_services:
                messages.success(
                    request,
                    "Report saved. All requested tests are now completed. Review the report and use Send to Doctor when you are ready.",
                )
                return redirect('report_edit', pk=report.pk)

            if not report_needs_doctor_send(report) and not remaining_pending_services:
                returned_to_doctor = mark_lab_queue_complete(report)
                cleanup_stale_lab_queue_entries(visit=report.visit)
                if returned_to_doctor:
                    messages.success(
                        request,
                        ('Report updated. ' if is_edit else 'Report saved successfully. ')
                        + 'Results sent back to doctor.',
                    )
                else:
                    messages.success(
                        request,
                        ('Report updated.' if is_edit else 'Report saved successfully.')
                        + ' Lab work is complete and the patient is ready for reception billing.',
                    )
                return redirect('report_detail', pk=report.pk)

            if selected_visit_service:
                messages.success(request, f"{selected_visit_service.service.name} saved successfully.")
                return redirect("lab_queue")

            messages.success(request, 'Report updated.' if is_edit else 'Report saved successfully.')
            return redirect('report_edit', pk=report.pk)
        messages.error(request, 'Please fix the errors below.')
    else:
        form = LabReportForm(instance=report)
        formset = TestResultFormSet(instance=report)

    context = {
        'edit_mode': is_edit,
        'report': report if is_edit else None,
        'linked_visit': report.visit if report and report.visit_id else None,
        'can_send_to_doctor': can_send_to_doctor,
        'pending_requested_services': pending_requested_services,
        'completed_requested_services': completed_requested_services,
        'selected_requested_service_id': selected_requested_service_id,
    }

    return render(
        request,
        'lab/report_form.html',
        build_report_form_context(
            form,
            formset,
            **context
        ),
    )


@login_required
@staff_required
def lab_queue(request):
    hospital = get_active_hospital(request)
    cleanup_stale_lab_queue_entries(hospital=hospital)
    queue_entries = QueueEntry.objects.filter(
        queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
        processed=False,
    ).select_related('visit__patient', 'hospital', 'requested_by').prefetch_related('visit__visit_services__service')
    
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        queue_entries = queue_entries.filter(hospital=hospital)
        
    return render(
        request,
        'lab/lab_queue.html',
        {
            'queue_entries': queue_entries.order_by('created_at'),
            'active_nav': 'lab_queue',
        },
    )


@login_required
@staff_required
def report_list(request):
    base_qs = scoped_reports_queryset(request).order_by('-created_at')
    qs = base_qs.prefetch_related("results__source_profile")

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

    # Optimize: Calculate counts only for the entire base_qs once.
    # Note: avoid naming aggregate keys the same as model fields (e.g. "printed"),
    # otherwise Django can treat Q(printed=...) as referring to the aggregate alias.
    from django.db.models import Count
    base_stats = base_qs.aggregate(
        total=Count("id"),
        printed_total=Count("id", filter=Q(printed=True)),
        draft_total=Count("id", filter=Q(printed=False)),
    )

    paginator = Paginator(qs, 10)
    reports = paginator.get_page(request.GET.get('page'))
    context = {
        'reports': reports,
        'total_reports': base_stats['total'],
        'printed_count': base_stats['printed_total'],
        'draft_count': base_stats['draft_total'],
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
    messages.info(
        request,
        "Start lab work from the live queue only. Manual report creation has been removed to keep the workflow clean.",
    )
    return redirect("lab_queue")


@login_required
@staff_required
@transaction.atomic
def report_create_from_lab_request(request, lab_request_id):
    """Create a lab report from a doctor's lab request with auto-filled doctor info"""
    hospital = get_active_hospital(request)
    
    # Get the lab request - allow both PENDING and IN_PROGRESS statuses
    lab_requests = LabRequest.objects.filter(
        requested_by_role=LabRequest.REQUESTED_BY_DOCTOR,
        status__in=[LabRequest.STATUS_PENDING, LabRequest.STATUS_IN_PROGRESS],
    ).select_related('visit__patient', 'visit__hospital', 'requested_by')
    
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        lab_requests = lab_requests.filter(visit__hospital=hospital)
    
    lab_request = get_object_or_404(lab_requests, pk=lab_request_id)
    visit = lab_request.visit
    
    # Create or get the report for this lab request
    report = LabReport.objects.filter(lab_request=lab_request).first()
    
    if not report:
        # Create new report with auto-filled data
        doctor_name = lab_request.requested_by.get_full_name() or lab_request.requested_by.username
        report = LabReport(
            lab_request=lab_request,
            visit=visit,
            patient_name=visit.patient.name,
            patient_age=visit.patient.age,
            patient_sex=visit.patient.sex,
            referred_by=doctor_name,  # Auto-filled with doctor's name
            specimen_type='BLOOD',
            attendant=request.user,
            attendant_name=request.user.get_full_name() or request.user.username,
            hospital=visit.hospital,
        )
        # Update lab request status to in_progress
        lab_request.status = LabRequest.STATUS_IN_PROGRESS
        lab_request.save(update_fields=['status'])
    
    return redirect("report_edit", pk=report.pk)


@login_required
@staff_required
def perform_lab_test(request, queue_entry_id):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.select_related('visit__patient', 'hospital').filter(
        pk=queue_entry_id,
        queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
    )
    if hospital and getattr(request.user, 'role', '') != 'superadmin':
        queue_entries = queue_entries.filter(hospital=hospital)
    queue_entry = get_object_or_404(queue_entries)
    visit = queue_entry.visit
    if visit.status == Visit.STATUS_CANCELLED:
        messages.error(request, "This visit was terminated by an administrator and cannot continue in the lab queue.")
        queue_entry.processed = True
        queue_entry.processed_at = timezone.now()
        queue_entry.save(update_fields=['processed', 'processed_at'])
        return redirect('lab_queue')
    pending_services = list(lab_visit_services(visit, performed=False))
    if pending_services:
        requested_service_id = (request.GET.get("requested_service_id") or "").strip()
        selected_visit_service = next(
            (item for item in pending_services if str(item.pk) == requested_service_id),
            pending_services[0],
        )
        report = ensure_report_for_visit_service(selected_visit_service, attendant=request.user)
        return redirect(
            f"{reverse('report_edit', kwargs={'pk': report.pk})}?requested_service_id={selected_visit_service.pk}"
        )

    report = LabReport.objects.filter(visit=visit, requested_visit_service__isnull=True).first()
    if not report:
        report = LabReport.objects.create(
            hospital=visit.hospital,
            visit=visit,
            patient_name=visit.patient.name,
            patient_age=visit.patient.age,
            patient_sex=visit.patient.sex,
            sample_date=timezone.now().date(),
            specimen_type='BLOOD',
            attendant=request.user,
            attendant_name=request.user.get_full_name() or request.user.username,
        )
    return redirect('report_edit', pk=report.pk)


@login_required
@staff_required
def report_edit(request, pk):
    report = get_object_or_404(scoped_reports_queryset(request), pk=pk)
    if report.visit_id and report.visit and report.visit.status == Visit.STATUS_CANCELLED:
        messages.error(request, "This visit was terminated by an administrator and the lab report can no longer be edited.")
        return redirect('report_detail', pk=report.pk)
    return handle_report_form(request, report=report)


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
def default_range(request):
    """AJAX endpoint to fetch default reference range for a test name + age."""
    test_name = ' '.join((request.GET.get('test') or '').split())
    age = request.GET.get('age', '')
    sex = request.GET.get('sex', '')
    if not test_name:
        return JsonResponse({})

    test = TestCatalog.objects.filter(name__iexact=test_name).first()
    if not test:
        return JsonResponse({})

    age_cat = get_age_category(age, sex)
    default = ReferenceRangeDefault.objects.filter(test=test, age_category=age_cat).first()
    if not default:
        return JsonResponse({})
    return JsonResponse({
        'reference_range': default.reference_range,
        'unit': default.unit,
    })


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
