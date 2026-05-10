from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import User
from admin_dashboard.models import InventoryItem, InventoryTransaction
from doctor.models import Consultation, Prescription
from lab.models import LabReport
from .forms import CompleteVisitForm, PatientForm, QuickDispenseStartForm, VisitCreateForm
from .models import Patient, Payment, QueueEntry, Service, Visit, VisitService
from .workflow import (
    ensure_pending_queue_entry,
    mark_queue_entries_processed,
    record_admin_override,
    reception_source_from_entry,
    require_admin_override,
    send_to_reception_queue,
    sync_visit_status,
    terminate_visit_workflow,
)


def reception_role_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        user = request.user
        allowed = getattr(user, "role", "") in {
            User.ROLE_SUPERADMIN,
            User.ROLE_HOSPITAL_ADMIN,
            User.ROLE_RECEPTIONIST,
        } or user.groups.filter(name="Reception").exists()
        if not allowed:
            return redirect("app_home")
        return view_func(request, *args, **kwargs)

    return wrapped


def get_active_hospital(request):
    return getattr(request, "hospital", None) or getattr(request.user, "hospital", None)


def resolve_next_url(request, fallback_url):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback_url


def admin_override_reason(request):
    return (request.POST.get("admin_reason") or "").strip()


def queue_types_for_service(service):
    mapping = {
        service.CATEGORY_LAB: [QueueEntry.TYPE_RECEPTION],
        service.CATEGORY_CONSULTATION: [QueueEntry.TYPE_DOCTOR],
        service.CATEGORY_TRIAGE: [QueueEntry.TYPE_NURSE],
    }
    return mapping.get(service.category, [])


def queue_reason_for_service(service):
    if service.category == service.CATEGORY_LAB:
        return f"Initial lab tests: {service.name}"
    if service.category == service.CATEGORY_CONSULTATION:
        return f"Initial consultation: {service.name}"
    if service.category == service.CATEGORY_TRIAGE:
        return f"Triage required: {service.name}"
    return f"Initial {service.category}: {service.name}"


def available_drug_payload(item):
    return {
        "id": item.pk,
        "name": item.name,
        "category": item.category,
        "unit": item.unit,
        "base_unit": item.base_unit,
        "units_per_pack": str(item.units_per_pack or ""),
        "strength_mg_per_unit": str(item.strength_mg_per_unit or ""),
        "selling_price": str(item.selling_price or 0),
        "current_quantity": str(item.current_quantity),
        "concentration_mg_per_ml": str(item.concentration_mg_per_ml or ""),
        "pack_size_ml": str(item.pack_size_ml or ""),
        "days_covered_per_pack": str(item.days_covered_per_pack or ""),
    }


def get_or_create_walk_in_patient(hospital):
    patient, created = Patient.objects.get_or_create(
        hospital=hospital,
        name="Walk-In Client",
        age="0YRS",
        sex="O",
        defaults={
            "registration_date": timezone.localdate(),
            "contact": "",
            "email": "",
            "address": "",
        },
    )
    if created and not patient.registration_date:
        patient.registration_date = timezone.localdate()
        patient.save(update_fields=["registration_date"])
    return patient


def reception_queue_queryset(hospital):
    return (
        QueueEntry.objects.filter(
            hospital=hospital,
            queue_type=QueueEntry.TYPE_RECEPTION,
            processed=False,
        )
        .select_related("visit__patient", "requested_by")
        .prefetch_related("visit__visit_services__service", "visit__prescriptions__drug", "visit__queue_entries")
        .order_by("created_at", "id")
    )


def consultation_services_queryset(hospital):
    return Service.objects.filter(
        hospital=hospital,
        category=Service.CATEGORY_CONSULTATION,
        is_active=True,
    ).order_by("name")


def reception_queue_other_open_work(visit):
    return visit.queue_entries.exclude(queue_type=QueueEntry.TYPE_RECEPTION).filter(processed=False)


def close_reception_queue_for_visit(visit):
    return mark_queue_entries_processed(visit=visit, queue_type=QueueEntry.TYPE_RECEPTION)


def reception_queue_status_payload(entry):
    visit = entry.visit
    pending_dispense_count = sum(1 for prescription in visit.prescriptions.all() if not prescription.dispensed)
    other_open_work = reception_queue_other_open_work(visit)
    if pending_dispense_count:
        return {
            "label": "Pending Dispense",
            "badge_class": "bg-purple-100 text-purple-700",
            "pending_dispense_count": pending_dispense_count,
            "other_open_work": other_open_work.count(),
        }
    if visit.status == Visit.STATUS_READY_FOR_BILLING:
        return {
            "label": "Ready for Billing",
            "badge_class": "bg-amber-100 text-amber-700",
            "pending_dispense_count": pending_dispense_count,
            "other_open_work": other_open_work.count(),
        }
    if other_open_work.exists():
        return {
            "label": "Awaiting Linked Work",
            "badge_class": "bg-blue-100 text-blue-700",
            "pending_dispense_count": pending_dispense_count,
            "other_open_work": other_open_work.count(),
        }
    return {
        "label": "Awaiting Action",
        "badge_class": "bg-slate-100 text-slate-700",
        "pending_dispense_count": pending_dispense_count,
        "other_open_work": other_open_work.count(),
    }


@reception_role_required
def reception_dashboard(request):
    hospital = get_active_hospital(request)
    patients = Patient.objects.filter(hospital=hospital).order_by("-created_at")[:5] if hospital else Patient.objects.none()
    visits = Visit.objects.filter(hospital=hospital).select_related("patient").order_by("-visit_date")[:5] if hospital else Visit.objects.none()
    ready_for_billing = (
        Visit.objects.filter(hospital=hospital, status=Visit.STATUS_READY_FOR_BILLING)
        .select_related("patient")
        .annotate(
            undispensed_prescription_count=Count(
                "prescriptions",
                filter=Q(prescriptions__dispensed=False),
                distinct=True,
            )
        )
        .order_by("-visit_date")[:6]
        if hospital else Visit.objects.none()
    )
    ready_for_dispense = [visit for visit in ready_for_billing if getattr(visit, "undispensed_prescription_count", 0) > 0]
    context = {
        "active_nav": "reception",
        "dashboard_title": "Reception Dashboard",
        "dashboard_intro": "Patient registration, visit creation, billing completion, and care queue routing all start here.",
        "hospital": hospital,
        "patient_count": Patient.objects.filter(hospital=hospital).count() if hospital else 0,
        "visit_count": Visit.objects.filter(hospital=hospital).count() if hospital else 0,
        "queue_count": QueueEntry.objects.filter(hospital=hospital, processed=False).count() if hospital else 0,
        "completed_visit_count": Visit.objects.filter(hospital=hospital, status=Visit.STATUS_COMPLETED).count() if hospital else 0,
        "ready_for_billing_count": Visit.objects.filter(hospital=hospital, status=Visit.STATUS_READY_FOR_BILLING).count() if hospital else 0,
        "ready_for_dispense_count": len(ready_for_dispense),
        "reception_queue_count": reception_queue_queryset(hospital).count() if hospital else 0,
        "recent_patients": patients,
        "recent_visits": visits,
        "ready_for_billing_visits": ready_for_billing,
        "ready_for_dispense_visits": ready_for_dispense,
    }
    return render(request, "reception/dashboard.html", context)


@reception_role_required
def receptionist_queue(request):
    hospital = get_active_hospital(request)
    consultation_services = list(consultation_services_queryset(hospital)) if hospital else []
    queue_entries = list(reception_queue_queryset(hospital)) if hospital else []
    queue_rows = []

    for entry in queue_entries:
        visit = entry.visit
        status_payload = reception_queue_status_payload(entry)
        pending_consultation_line = next(
            (
                visit_service
                for visit_service in visit.visit_services.all()
                if visit_service.service.category == Service.CATEGORY_CONSULTATION and not visit_service.performed
            ),
            None,
        )
        has_pending_lab_approval = visit.visit_services.filter(
            service__category=Service.CATEGORY_LAB,
            is_approved=False
        ).exists()
        queue_rows.append(
            {
                "entry": entry,
                "visit": visit,
                "source": reception_source_from_entry(entry),
                "status": status_payload["label"],
                "status_badge_class": status_payload["badge_class"],
                "pending_dispense_count": status_payload["pending_dispense_count"],
                "open_work_count": status_payload["other_open_work"],
                "pending_consultation_line": pending_consultation_line,
                "has_pending_lab_approval": has_pending_lab_approval,
                "selected_consultation_service_id": (
                    pending_consultation_line.service_id
                    if pending_consultation_line
                    else (consultation_services[0].pk if consultation_services else None)
                ),
            }
        )

    return render(
        request,
        "reception/reception_queue.html",
        {
            "active_nav": "reception_queue",
            "dashboard_title": "Receptionist Queue",
            "dashboard_intro": "Receive patients back from lab, doctor, and nurse, then decide the final billing, dispensing, or doctor-review step.",
            "hospital": hospital,
            "queue_rows": queue_rows,
            "queue_count": len(queue_rows),
            "pending_dispense_count": sum(row["pending_dispense_count"] for row in queue_rows),
            "consultation_services": consultation_services,
        },
    )


@reception_role_required
@transaction.atomic
def receptionist_queue_approve_lab(request, queue_entry_id):
    if request.method != "POST":
        raise PermissionDenied("Approving lab requests requires a POST request.")

    hospital = get_active_hospital(request)
    queue_entry = get_object_or_404(reception_queue_queryset(hospital), pk=queue_entry_id)
    visit = queue_entry.visit

    # 1. Mark all lab services as approved
    lab_services = visit.visit_services.filter(
        service__category=Service.CATEGORY_LAB,
        is_approved=False
    )
    
    if not lab_services.exists():
        messages.info(request, "No pending lab services found for approval.")
    else:
        lab_services.update(is_approved=True)
        
        # 2. Create the lab queue entry
        pending_names = list(lab_services.values_list("service__name", flat=True))
        reason = f"Doctor requested: {', '.join(pending_names)}" if pending_names else "Laboratory follow-up approved."
        
        from reception.models import QueueEntry as QE
        ensure_pending_queue_entry(
            visit=visit,
            hospital=visit.hospital,
            queue_type=QE.TYPE_LAB_DOCTOR,
            reason=reason,
            requested_by=queue_entry.requested_by or request.user,
            notes="Lab services approved by reception.",
        )
        
        # 3. Mark the reception queue entry as processed
        queue_entry.processed = True
        queue_entry.processed_at = timezone.now()
        queue_entry.save(update_fields=["processed", "processed_at"])
        
        sync_visit_status(visit)
        messages.success(request, f"Lab requests for {visit.patient.name} have been approved and sent to the lab.")

    return redirect("reception_queue")


@reception_role_required
@transaction.atomic
def receptionist_queue_finish(request, queue_entry_id):
    if request.method != "POST":
        raise PermissionDenied("Finishing a receptionist queue task requires a POST request.")

    hospital = get_active_hospital(request)
    queue_entry = get_object_or_404(reception_queue_queryset(hospital), pk=queue_entry_id)
    visit = queue_entry.visit

    if reception_queue_other_open_work(visit).exists():
        messages.error(request, "This visit still has other open queue work and cannot be finished for billing yet.")
        return redirect("reception_queue")

    close_reception_queue_for_visit(visit)
    visit.status = Visit.STATUS_READY_FOR_BILLING
    visit.save(update_fields=["status"])
    sync_visit_status(visit)
    messages.success(request, f"{visit.patient.name} is now ready for billing.")
    return redirect("reception_queue")


@reception_role_required
@transaction.atomic
def receptionist_queue_bill(request, queue_entry_id):
    if request.method != "POST":
        raise PermissionDenied("Opening billing from the receptionist queue requires a POST request.")

    hospital = get_active_hospital(request)
    queue_entry = get_object_or_404(reception_queue_queryset(hospital), pk=queue_entry_id)
    visit = queue_entry.visit

    if reception_queue_other_open_work(visit).exists():
        messages.error(request, "This visit still has other open queue work and cannot move to billing yet.")
        return redirect("reception_queue")

    close_reception_queue_for_visit(visit)
    visit.status = Visit.STATUS_READY_FOR_BILLING
    visit.save(update_fields=["status"])
    sync_visit_status(visit)
    messages.success(
        request,
        "Visit opened for reception billing and dispensing."
        if visit.prescriptions.filter(dispensed=False).exists()
        else "Visit opened for reception billing.",
    )
    return redirect("complete_visit", visit_id=visit.pk)


@reception_role_required
@transaction.atomic
def receptionist_queue_send_to_doctor(request, queue_entry_id):
    if request.method != "POST":
        raise PermissionDenied("Sending a patient to doctor from receptionist queue requires a POST request.")

    hospital = get_active_hospital(request)
    queue_entry = get_object_or_404(reception_queue_queryset(hospital), pk=queue_entry_id)
    visit = queue_entry.visit

    if reception_queue_other_open_work(visit).exists():
        messages.error(request, "This visit still has other open queue work and cannot be routed to doctor yet.")
        return redirect("reception_queue")

    consultation_service_qs = consultation_services_queryset(hospital)
    consultation_service = consultation_service_qs.filter(pk=request.POST.get("consultation_service_id")).first()
    if consultation_service is None:
        consultation_service = consultation_service_qs.first()
    if consultation_service is None:
        messages.error(request, "Create at least one active consultation service before sending patients to doctor.")
        return redirect("reception_queue")

    pending_consultation_line = visit.visit_services.filter(
        service=consultation_service,
        service__category=Service.CATEGORY_CONSULTATION,
        performed=False,
    ).first()
    if pending_consultation_line is None:
        VisitService.objects.create(
            visit=visit,
            service=consultation_service,
            price_at_time=consultation_service.price,
            notes=f"Added from receptionist queue after {reception_source_from_entry(queue_entry).lower()} handoff.",
        )
        visit.total_amount = (visit.total_amount or Decimal("0.00")) + consultation_service.price
        visit.save(update_fields=["total_amount"])

    close_reception_queue_for_visit(visit)
    ensure_pending_queue_entry(
        visit=visit,
        hospital=visit.hospital,
        queue_type=QueueEntry.TYPE_DOCTOR,
        reason=f"Reception requested doctor review after {reception_source_from_entry(queue_entry).lower()} handoff.",
        requested_by=request.user,
        notes=f"Reception returned the patient to doctor review from receptionist queue. Previous handoff: {queue_entry.reason}",
    )
    sync_visit_status(visit)
    messages.success(request, f"{visit.patient.name} sent to doctor queue.")
    return redirect("reception_queue")


@reception_role_required
@transaction.atomic
def quick_dispense_start(request):
    hospital = get_active_hospital(request)
    if request.method == "POST":
        form = QuickDispenseStartForm(request.POST, hospital=hospital)
        if form.is_valid():
            client_type = form.cleaned_data["client_type"]
            patient = (
                form.cleaned_data["patient"]
                if client_type == QuickDispenseStartForm.CLIENT_EXISTING
                else get_or_create_walk_in_patient(hospital)
            )
            visit = Visit.objects.create(
                patient=patient,
                hospital=hospital,
                status=Visit.STATUS_READY_FOR_BILLING,
                total_amount=Decimal("0.00"),
                created_by=request.user,
                notes=form.cleaned_data.get("notes") or "Created from reception quick dispense desk.",
            )
            messages.success(
                request,
                f"Dispense visit started for {patient.name}. Add medicines, dispense them, then finish billing.",
            )
            return redirect("complete_visit", visit_id=visit.pk)
        messages.error(request, "Please fix the quick dispense details below.")
    else:
        form = QuickDispenseStartForm(hospital=hospital, initial={"client_type": QuickDispenseStartForm.CLIENT_WALK_IN})

    return render(
        request,
        "reception/quick_dispense_start.html",
        {
            "active_nav": "reception",
            "dashboard_title": "Start Dispense",
            "dashboard_intro": "Open a quick dispense visit for a walk-in client or an existing patient, then move straight into prescribing and billing.",
            "hospital": hospital,
            "form": form,
        },
    )


@reception_role_required
def patient_list(request):
    hospital = get_active_hospital(request)
    patients = (
        Patient.objects.filter(hospital=hospital)
        .annotate(visit_count=Count("visits"), last_visit_date=Max("visits__visit_date"))
        .prefetch_related("visits__queue_entries", "visits__visit_services__service")
        .order_by("name")
        if hospital
        else Patient.objects.none()
    )
    query = (request.GET.get("q") or "").strip()
    if query:
        patients = patients.filter(
            Q(name__icontains=query)
            | Q(contact__icontains=query)
            | Q(age__icontains=query)
        )
    patient_rows = []
    for patient in patients:
        visits = list(patient.visits.all())
        latest_editable_visit = next(
            (
                visit
                for visit in visits
                if visit.status != Visit.STATUS_COMPLETED and not visit.queue_entries.filter(processed=True).exists()
            ),
            None,
        )
        patient_rows.append(
            {
                "patient": patient,
                "recent_visits": visits[:3],
                "latest_editable_visit": latest_editable_visit,
            }
        )
    return render(
        request,
        "reception/patient_list.html",
        {
            "active_nav": "reception_patients",
            "dashboard_title": "Patients",
            "dashboard_intro": "Search returning patients, review prior visits, and start a new visit when they arrive.",
            "hospital": hospital,
            "patient_rows": patient_rows,
            "query": query,
        },
    )


@reception_role_required
def patient_create(request):
    hospital = get_active_hospital(request)
    if hospital is None:
        messages.error(request, "A hospital context is required before you can register patients.")
        return redirect("reception_dashboard")

    if request.method == "POST":
        form = PatientForm(request.POST)
        if form.is_valid():
            patient = form.save(commit=False)
            patient.hospital = hospital
            patient.save()
            messages.success(request, f"{patient.name} registered successfully.")
            return redirect("visit_create", patient_id=patient.pk)
        messages.error(request, "Please fix the patient details below.")
    else:
        form = PatientForm()

    return render(
        request,
        "reception/patient_form.html",
        {
            "active_nav": "reception_patients",
            "dashboard_title": "Register Patient",
            "dashboard_intro": "Create a patient record and continue into visit creation.",
            "hospital": hospital,
            "form": form,
        },
    )


@reception_role_required
@transaction.atomic
def patient_delete(request, patient_id):
    hospital = get_active_hospital(request)
    patient = get_object_or_404(
        Patient.objects.filter(hospital=hospital).prefetch_related("visits"),
        pk=patient_id,
    )
    require_admin_override(request.user)

    cancel_url = resolve_next_url(request, reverse("patient_visits", args=[patient.pk]))
    linked_visits = Visit.objects.filter(patient=patient)
    linked_reports = LabReport.objects.filter(visit__patient=patient)
    linked_consultations = Consultation.objects.filter(visit__patient=patient)

    if request.method == "POST":
        reason = admin_override_reason(request)
        if not reason:
            messages.error(request, "Enter the reason for deleting this patient record.")
        else:
            details = {
                "patient_name": patient.name,
                "reason": reason,
                "visit_count": linked_visits.count(),
                "lab_report_count": linked_reports.count(),
                "consultation_count": linked_consultations.count(),
            }
            linked_reports.delete()
            patient_id_value = patient.pk
            patient_name = patient.name
            patient.delete()
            record_admin_override(
                actor=request.user,
                hospital=hospital,
                action="delete_patient",
                model_name="Patient",
                object_id=patient_id_value,
                details=details,
            )
            messages.success(request, f"{patient_name} and the linked visit records were removed.")
            return redirect(resolve_next_url(request, reverse("patient_list")))

    return render(
        request,
        "admin_override_confirm.html",
        {
            "dashboard_title": "Delete Patient",
            "dashboard_intro": "Remove this patient and the linked visit history from the hospital database.",
            "object_label": patient.name,
            "object_type": "patient",
            "danger_note": (
                f"This will remove {linked_visits.count()} visit(s), {linked_consultations.count()} consultation(s), "
                f"and {linked_reports.count()} linked lab report(s)."
            ),
            "confirm_label": "Delete Patient",
            "cancel_href": cancel_url,
            "next_url": resolve_next_url(request, reverse("patient_list")),
        },
    )


@reception_role_required
@transaction.atomic
def visit_create(request, patient_id):
    hospital = get_active_hospital(request)
    patient = get_object_or_404(Patient, pk=patient_id, hospital=hospital)

    if request.method == "POST":
        form = VisitCreateForm(request.POST, hospital=hospital, patient=patient)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.patient = patient
            visit.hospital = hospital
            visit.created_by = request.user
            visit.total_amount = form.calculate_total()
            if visit.visit_type == Visit.TYPE_FOLLOW_UP:
                visit.parent_visit = form.cleaned_data["follow_up_parent_visit"]
                visit.adjustment_origin_prescription = None
                visit.adjustment_days_used = 0
                visit.adjustment_remaining_days = 0
                visit.adjustment_reason = ""
            elif visit.visit_type == Visit.TYPE_ADJUSTMENT:
                origin = form.cleaned_data["adjustment_origin_prescription"]
                visit.parent_visit = origin.visit
                visit.adjustment_origin_prescription = origin
                visit.adjustment_days_used = form.cleaned_data.get("adjustment_days_used") or 0
                visit.adjustment_remaining_days = form.cleaned_data.get("adjustment_remaining_days") or 0
                visit.adjustment_reason = form.cleaned_data.get("adjustment_reason") or ""
            else:
                visit.parent_visit = None
                visit.adjustment_origin_prescription = None
                visit.adjustment_days_used = 0
                visit.adjustment_remaining_days = 0
                visit.adjustment_reason = ""
            visit.save()

            services = list(form.cleaned_data["services"])
            if visit.visit_type == Visit.TYPE_ADJUSTMENT:
                ensure_pending_queue_entry(
                    visit=visit,
                    hospital=hospital,
                    queue_type=QueueEntry.TYPE_DOCTOR,
                    reason=f"Medication adjustment review for {visit.adjustment_origin_prescription.drug.name}.",
                    requested_by=request.user,
                    notes=(
                        f"Adjustment visit linked to prescription {visit.adjustment_origin_prescription_id}. "
                        f"Days already used: {visit.adjustment_days_used}. Remaining days: {visit.adjustment_remaining_days}."
                    ),
                )
            else:
                for service in services:
                    VisitService.objects.create(
                        visit=visit,
                        service=service,
                        price_at_time=service.price,
                    )
                    for queue_type in queue_types_for_service(service):
                        QueueEntry.objects.create(
                            hospital=hospital,
                            visit=visit,
                            queue_type=queue_type,
                            reason=queue_reason_for_service(service),
                            requested_by=request.user,
                        )

            sync_visit_status(visit)
            
            # SECURITY: Validate billing structure to prevent loopholes
            try:
                visit.validate_billing_structure()
            except ValidationError as e:
                # This should not happen if forms are working correctly, but catch it anyway
                messages.warning(
                    request,
                    f"Visit was created but has a billing issue: {e.message}. "
                    f"Please review and edit the visit if needed."
                )
            
            if visit.visit_type == Visit.TYPE_ADJUSTMENT:
                messages.success(
                    request,
                    (
                        f"Adjustment visit created for {patient.name}. "
                        f"Doctor can now replace {visit.adjustment_origin_prescription.drug.name} "
                        f"for the remaining {visit.adjustment_remaining_days} day(s) without re-billing."
                    ),
                )
                return redirect("patient_visits", patient_id=patient.pk)
            messages.success(
                request,
                f"Visit created. Total bill: {visit.total_amount}. No payment collected yet.",
            )
            return redirect("reception_dashboard")
        messages.error(request, "Please fix the visit details below.")
    else:
        form = VisitCreateForm(hospital=hospital, patient=patient)

    return render(
        request,
        "reception/visit_form.html",
        {
            "active_nav": "reception_patients",
            "dashboard_title": "Create Visit",
            "dashboard_intro": "Select services, build the visit bill, and route work into the live queue.",
            "hospital": hospital,
            "patient": patient,
            "form": form,
            "edit_mode": False,
        },
    )


@reception_role_required
@transaction.atomic
def visit_edit(request, visit_id):
    hospital = get_active_hospital(request)
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "hospital").prefetch_related("visit_services__service", "queue_entries"),
        pk=visit_id,
        hospital=hospital,
    )

    if visit.status == Visit.STATUS_COMPLETED:
        messages.error(request, "Completed visits cannot be edited from reception.")
        return redirect("reception_dashboard")
    if visit.is_adjustment_visit:
        messages.error(request, "Adjustment visits are doctor-led and cannot be edited from reception.")
        return redirect("patient_visits", patient_id=visit.patient_id)
    if visit.queue_entries.filter(processed=True).exists():
        messages.error(request, "This visit already has processed workflow activity and can no longer be edited safely.")
        return redirect("patient_list")

    if request.method == "POST":
        form = VisitCreateForm(request.POST, instance=visit, hospital=hospital, patient=visit.patient)
        if form.is_valid():
            visit = form.save(commit=False)
            visit.total_amount = form.calculate_total()
            visit.save()

            visit.visit_services.all().delete()
            visit.queue_entries.filter(processed=False).delete()

            services = list(form.cleaned_data["services"])
            for service in services:
                VisitService.objects.create(
                    visit=visit,
                    service=service,
                    price_at_time=service.price,
                )
                for queue_type in queue_types_for_service(service):
                    QueueEntry.objects.create(
                        hospital=hospital,
                        visit=visit,
                        queue_type=queue_type,
                        reason=queue_reason_for_service(service),
                        requested_by=request.user,
                    )

            sync_visit_status(visit)
            
            # SECURITY: Validate billing structure to prevent loopholes
            try:
                visit.validate_billing_structure()
            except ValidationError as e:
                messages.warning(
                    request,
                    f"Visit was updated but has a billing issue: {e.message}. "
                    f"Please review the visit services."
                )
            
            messages.success(request, f"Visit for {visit.patient.name} updated successfully.")
            return redirect("patient_list")
        messages.error(request, "Please fix the visit details below.")
    else:
        form = VisitCreateForm(instance=visit, hospital=hospital, patient=visit.patient)

    return render(
        request,
        "reception/visit_form.html",
        {
            "active_nav": "reception_patients",
            "dashboard_title": "Edit Visit",
            "dashboard_intro": "Adjust services and notes before the care workflow has been processed.",
            "hospital": hospital,
            "patient": visit.patient,
            "visit": visit,
            "form": form,
            "edit_mode": True,
        },
    )


@reception_role_required
@transaction.atomic
def visit_delete(request, visit_id):
    hospital = get_active_hospital(request)
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "hospital").prefetch_related("visit_services__service", "queue_entries"),
        pk=visit_id,
        hospital=hospital,
    )

    if visit.status == Visit.STATUS_COMPLETED:
        messages.error(request, "Completed visits cannot be deleted from reception.")
        return redirect("reception_dashboard")
    if visit.is_adjustment_visit:
        messages.error(request, "Adjustment visits are linked to prior treatment and cannot be deleted from reception.")
        return redirect("patient_visits", patient_id=visit.patient_id)
    if visit.queue_entries.filter(processed=True).exists():
        messages.error(request, "This visit already has processed workflow activity and can no longer be deleted safely.")
        return redirect("patient_list")

    if request.method == "POST":
        patient_name = visit.patient.name
        visit.delete()
        messages.success(request, f"Visit for {patient_name} deleted.")
        return redirect("patient_list")

    return render(
        request,
        "reception/visit_confirm_delete.html",
        {
            "active_nav": "reception_patients",
            "dashboard_title": "Delete Visit",
            "dashboard_intro": "Confirm whether this unprocessed visit should be removed.",
            "hospital": hospital,
            "visit": visit,
        },
    )


@reception_role_required
@transaction.atomic
def visit_terminate(request, visit_id):
    hospital = get_active_hospital(request)
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "hospital"),
        pk=visit_id,
        hospital=hospital,
    )
    require_admin_override(request.user)

    fallback_url = reverse("patient_visits", args=[visit.patient_id])
    cancel_url = resolve_next_url(request, fallback_url)
    open_queue_count = visit.queue_entries.filter(processed=False).count()

    if request.method == "POST":
        reason = admin_override_reason(request)
        if not reason:
            messages.error(request, "Enter the reason for terminating this visit.")
        elif visit.status == Visit.STATUS_COMPLETED:
            messages.error(request, "Completed visits cannot be terminated.")
            return redirect(cancel_url)
        elif visit.status == Visit.STATUS_CANCELLED:
            messages.info(request, "This visit has already been terminated.")
            return redirect(cancel_url)
        else:
            closed_queue_count = terminate_visit_workflow(visit=visit, actor=request.user, reason=reason)
            messages.success(
                request,
                f"Visit #{visit.pk} terminated. {closed_queue_count} open queue item(s) were closed.",
            )
            return redirect(resolve_next_url(request, fallback_url))

    return render(
        request,
        "admin_override_confirm.html",
        {
            "dashboard_title": "Terminate Visit",
            "dashboard_intro": "Stop this visit immediately and clear it out of every active queue.",
            "object_label": f"{visit.patient.name} - Visit #{visit.pk}",
            "object_type": "visit",
            "danger_note": (
                f"This visit is currently {visit.get_status_display().lower()} with {open_queue_count} open queue item(s). "
                "Termination keeps the record for audit but prevents any further processing."
            ),
            "confirm_label": "Terminate Visit",
            "cancel_href": cancel_url,
            "next_url": resolve_next_url(request, fallback_url),
        },
    )


@reception_role_required
@transaction.atomic
def complete_visit(request, visit_id):
    hospital = get_active_hospital(request)
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "hospital").prefetch_related("visit_services__service"),
        pk=visit_id,
        hospital=hospital,
    )
    if visit.status == Visit.STATUS_CANCELLED:
        messages.error(request, "This visit has been terminated and can no longer be billed or dispensed.")
        return redirect("patient_visits", patient_id=visit.patient_id)
    
    # SECURITY: Validate billing structure to prevent receptionist loopholes
    try:
        visit.validate_billing_structure()
    except ValidationError as e:
        messages.error(
            request, 
            f"Cannot proceed with billing: {e.message}. This visit may have been improperly created or modified."
        )
        return redirect("patient_visits", patient_id=visit.patient_id)
    
    # With partial payments, a visit is only "completed" when fully settled.
    if visit.status == Visit.STATUS_COMPLETED and visit.is_fully_paid:
        messages.error(request, "This visit has already been fully paid and completed.")
        latest_payment = visit.payments.order_by("-paid_at", "-id").first()
        if latest_payment:
            return redirect("print_payment_receipt", payment_id=latest_payment.pk)
        return redirect("print_receipt", visit_id=visit.pk)

    if request.method == "POST" and request.POST.get("finish_adjustment_visit"):
        if not visit.is_adjustment_visit:
            raise PermissionDenied("Only adjustment visits can be closed without payment from this action.")
        if visit.balance_due > 0:
            messages.error(request, "This adjustment visit still has a balance and cannot be closed without payment.")
            return redirect("complete_visit", visit_id=visit.pk)
        if visit.prescriptions.filter(dispensed=False).exists():
            messages.error(request, "Dispense the replacement medicine before finishing this adjustment visit.")
            return redirect("complete_visit", visit_id=visit.pk)
        visit.status = Visit.STATUS_COMPLETED
        visit.save(update_fields=["status"])
        messages.success(request, "Adjustment visit finished. No extra billing was applied because the replacement is covered by the previous payment.")
        return redirect("patient_visits", patient_id=visit.patient_id)

    if request.method == "POST":
        form = CompleteVisitForm(request.POST, remaining_balance=visit.balance_due, hospital=hospital)
        if form.is_valid():
            amount_paid = form.cleaned_data["amount_paid"]
            payment_mode = form.cleaned_data["payment_mode"]

            # Cash receipts are mirrored to the daily cash statement automatically in Payment.save().

            payment = Payment(
                visit=visit,
                amount=visit.total_amount,
                amount_paid=amount_paid,
                mode=payment_mode,
                bank_account=form.cleaned_data["bank_account"],
                mobile_account=form.cleaned_data["mobile_account"],
                recorded_by=request.user,
                notes=form.cleaned_data["payment_notes"] or "",
            )
            payment.save()

            # Update visit status based on remaining balance.
            visit.refresh_from_db()
            if visit.is_fully_paid:
                visit.status = Visit.STATUS_COMPLETED
                visit.save(update_fields=["status"])
                messages.success(request, "Payment recorded. Visit is now fully paid and completed.")
            else:
                visit.status = Visit.STATUS_READY_FOR_BILLING
                visit.save(update_fields=["status"])
                messages.success(request, f"Partial payment recorded. Balance due: {visit.balance_due}.")

            return redirect("print_payment_receipt", payment_id=payment.pk)
        messages.error(request, "Please correct the billing details below.")
    else:
        remaining = visit.balance_due
        form = CompleteVisitForm(
            remaining_balance=remaining,
            hospital=hospital,
            initial={
                "amount_paid": remaining,
                "payment_mode": Payment.MODE_CASH,
                "bank_account": None,
                "mobile_account": None,
                "payment_notes": "",
            },
        )

    bank_qs = form.fields["bank_account"].queryset
    mobile_qs = form.fields["mobile_account"].queryset
    bank_count = bank_qs.count() if bank_qs is not None else 0
    mobile_count = mobile_qs.count() if mobile_qs is not None else 0
    prescriptions = list(
        visit.prescriptions.select_related("drug", "dispensed_by", "parent_prescription__drug").order_by("-dispensed", "-prescribed_at", "-id")
    )
    visit_services = visit.visit_services.exclude(service__category=Service.CATEGORY_PHARMACY).select_related("service").order_by("created_at")
    available_drugs = list(
        InventoryItem.objects.filter(
            hospital=hospital,
            category__in=[
                InventoryItem.CATEGORY_DRUG,
                InventoryItem.CATEGORY_SYRUP,
                InventoryItem.CATEGORY_IV,
                InventoryItem.CATEGORY_IM,
                InventoryItem.CATEGORY_TUBE,
                InventoryItem.CATEGORY_REAGENT,
            ],
            is_active=True,
        ).order_by("name")
    )

    return render(
        request,
        "reception/complete_visit.html",
        {
            "active_nav": "reception",
            "dashboard_title": "Record Payment",
            "dashboard_intro": "Record a payment for this visit. Partial payments are supported until the bill is fully settled.",
            "hospital": hospital,
            "visit": visit,
            "form": form,
            "total_paid": visit.total_paid,
            "balance_due": visit.balance_due,
            "bank_accounts_count": bank_count,
            "mobile_accounts_count": mobile_count,
            "single_bank_account": bank_qs.first() if bank_count == 1 else None,
            "single_mobile_account": mobile_qs.first() if mobile_count == 1 else None,
            "prescriptions": prescriptions,
            "visit_services": visit_services,
            "available_drugs": available_drugs,
            "allow_reception_prescribing": not visit.is_adjustment_visit,
            "adjustment_origin_prescription": visit.adjustment_origin_prescription,
        },
    )


@reception_role_required
@transaction.atomic
def dispense_prescription(request, visit_id, prescription_id):
    hospital = get_active_hospital(request)
    visit = get_object_or_404(
        Visit.objects.select_related("hospital", "patient"),
        pk=visit_id,
        hospital=hospital,
    )
    prescription = get_object_or_404(
        Prescription.objects.select_related("drug", "billing_visit_service", "visit"),
        pk=prescription_id,
        visit=visit,
    )

    if request.method != "POST":
        raise PermissionDenied("Dispensing requires a POST request.")
    if visit.status == Visit.STATUS_CANCELLED:
        raise PermissionDenied("Cancelled visits cannot dispense medication.")

    if prescription.dispensed:
        messages.info(request, f"{prescription.drug.name} was already dispensed for this visit.")
        return redirect("complete_visit", visit_id=visit.pk)

    drug = prescription.drug
    quantity_to_deduct = prescription.total_quantity
    stock_quantity_to_deduct = drug.to_stock_quantity(quantity_to_deduct)
    available_dispense_quantity = drug.available_dispense_quantity
    if available_dispense_quantity < quantity_to_deduct:
        messages.error(
            request,
            f"Cannot dispense {drug.name} - insufficient stock available. Current stock: {drug.quantity_label}, but prescription requires: {prescription.quantity_display}. Please restock the inventory or adjust the prescription.",
        )
        return redirect("complete_visit", visit_id=visit.pk)

    drug.consume_stock(stock_quantity_to_deduct)

    InventoryTransaction.objects.create(
        hospital=visit.hospital,
        item=drug,
        transaction_type=InventoryTransaction.TYPE_CONSUME,
        quantity=quantity_to_deduct,
        unit_cost=drug.unit_cost,
        visit=visit,
        prescription=prescription,
        performed_by=request.user,
        notes=f"Dispensed via reception workflow for prescription {prescription.pk}",
    )

    prescription.dispensed = True
    prescription.dispensed_at = timezone.now()
    prescription.dispensed_by = request.user
    prescription.save(update_fields=["dispensed", "dispensed_at", "dispensed_by"])

    if prescription.billing_visit_service_id:
        prescription.billing_visit_service.performed = True
        prescription.billing_visit_service.performed_at = timezone.now()
        prescription.billing_visit_service.save(update_fields=["performed", "performed_at"])

    messages.success(request, f"Dispensed {prescription.quantity_display} of {drug.name}.")
    return redirect("complete_visit", visit_id=visit.pk)


@reception_role_required
def print_receipt(request, visit_id):
    hospital = get_active_hospital(request)
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "hospital").prefetch_related("visit_services__service"),
        pk=visit_id,
        hospital=hospital,
    )
    payments = visit.payments.select_related("bank_account", "mobile_account", "recorded_by").order_by("-paid_at", "-id")
    latest_payment = payments.first()
    return render(
        request,
        "reception/receipt.html",
        {
            "visit": visit,
            "payment": latest_payment,
            "payments": payments,
            "hospital": visit.hospital,
            "total_paid": visit.total_paid,
            "balance_due": visit.balance_due,
        },
    )


@reception_role_required
def print_payment_receipt(request, payment_id):
    """Print a receipt for a specific payment (supports partial payments)."""
    hospital = get_active_hospital(request)
    payment = get_object_or_404(
        Payment.objects.select_related("visit__patient", "visit__hospital", "bank_account", "mobile_account", "recorded_by"),
        pk=payment_id,
        visit__hospital=hospital,
    )
    visit = payment.visit
    payments = visit.payments.select_related("bank_account", "mobile_account", "recorded_by").order_by("-paid_at", "-id")
    return render(
        request,
        "reception/payment_receipt.html",
        {
            "hospital": visit.hospital,
            "visit": visit,
            "payment": payment,
            "payments": payments,
            "total_paid": visit.total_paid,
            "balance_due": visit.balance_due,
        },
    )


@reception_role_required
def patient_visits(request, patient_id):
    """Display all visits for a specific patient with edit/delete/view options."""
    hospital = get_active_hospital(request)
    patient = get_object_or_404(Patient, pk=patient_id, hospital=hospital)
    
    # Get all visits for the patient with related data
    visits = (
        Visit.objects.filter(patient=patient)
        .select_related("hospital")
        .prefetch_related("visit_services__service", "queue_entries", "payments")
        .order_by("-visit_date")
    )
    
    # Prepare visit rows with editability and deletability flags
    visit_rows = []
    for visit in visits:
        can_edit = (
            visit.status != Visit.STATUS_COMPLETED
            and visit.status != Visit.STATUS_CANCELLED
            and not visit.is_adjustment_visit
            and not visit.queue_entries.filter(processed=True).exists()
        )
        can_delete = can_edit
        
        payments = list(visit.payments.all())
        # Prefer the most recent receipt for labels/badges.
        latest_payment = max(
            payments,
            key=lambda p: ((p.paid_at.timestamp() if p.paid_at else 0), p.pk or 0),
            default=None,
        )

        total_paid = sum((p.amount_paid for p in payments if p.status != Payment.STATUS_WAIVED), Decimal("0"))
        balance_due = max((visit.total_amount or Decimal("0")) - total_paid, Decimal("0"))
        
        visit_rows.append({
            "visit": visit,
            "can_edit": can_edit,
            "can_delete": can_delete,
            "payments": payments,
            "latest_payment": latest_payment,
            "total_paid": total_paid,
            "balance_due": balance_due,
            "service_count": visit.visit_services.count(),
        })
    
    return render(
        request,
        "reception/patient_visits.html",
        {
            "active_nav": "reception_patients",
            "dashboard_title": f"{patient.name} - Visit History",
            "dashboard_intro": "View, edit, or delete all visits from this patient's medical history.",
            "hospital": hospital,
            "patient": patient,
            "visit_rows": visit_rows,
        },
    )


def requested_by_label(user, fallback="System"):
    if not user:
        return fallback
    return user.get_full_name() or user.username


@reception_role_required
def view_visit_report(request, visit_id):
    """View complete visit report with doctor,nurse, and lab sections for printing"""
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("patient", "hospital")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    
    visit = get_object_or_404(visits, pk=visit_id)
    
    # Import here to avoid circular imports
    from doctor.models import Consultation
    from nurse.models import NurseNote
    from lab.models import LabReport
    
    # Get doctor consultation
    consultation = getattr(visit, "consultation", None)
    
    # Get nurse notes
    nurse_notes = NurseNote.objects.filter(visit=visit).select_related("created_by").order_by("-created_at")
    
    # Get lab reports
    lab_reports = LabReport.objects.filter(visit=visit).prefetch_related("results__test").order_by("-created_at")
    
    context = {
        "active_nav": "reception_patients",
        "dashboard_title": f"Visit Report - {visit.patient.name}",
        "dashboard_intro": "Complete visit documentation with all sections. Print this for patient records.",
        "hospital": hospital,
        "visit": visit,
        "consultation": consultation,
        "nurse_notes": nurse_notes,
        "lab_reports": lab_reports,
        "payments": visit.payments.select_related("bank_account", "mobile_account", "recorded_by").order_by("-paid_at", "-id"),
    }
    
    return render(request, "reception/visit_report.html", context)
