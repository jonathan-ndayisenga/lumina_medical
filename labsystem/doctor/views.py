from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from admin_dashboard.models import InventoryItem, InventoryTransaction
from lab.models import LabReport
from nurse.models import NurseNote
from reception.models import QueueEntry, Service, Triage, Visit, VisitService
from reception.workflow import ensure_pending_queue_entry, mark_queue_entries_processed, send_to_reception_queue, sync_visit_status

from .forms import ConsultationForm
from .models import Consultation, LabRequest, Prescription


def doctor_role_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        user = request.user
        allowed = getattr(user, "role", "") in {
            User.ROLE_SUPERADMIN,
            User.ROLE_HOSPITAL_ADMIN,
            User.ROLE_DOCTOR,
        } or user.groups.filter(name="Doctor").exists()
        if not allowed:
            return redirect("app_home")
        return view_func(request, *args, **kwargs)

    return wrapped


def prescribing_role_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        user = request.user
        allowed = getattr(user, "role", "") in {
            User.ROLE_SUPERADMIN,
            User.ROLE_HOSPITAL_ADMIN,
            User.ROLE_DOCTOR,
            User.ROLE_RECEPTIONIST,
        } or user.groups.filter(name__in=["Doctor", "Reception"]).exists()
        if not allowed:
            return redirect("app_home")
        return view_func(request, *args, **kwargs)

    return wrapped


def get_active_hospital(request):
    return getattr(request, "hospital", None) or getattr(request.user, "hospital", None)


def lab_visit_services(visit, *, performed=None):
    services = (
        VisitService.objects.filter(
            visit=visit,
            service__category=Service.CATEGORY_LAB,
        )
        .select_related("service__test_profile")
        .order_by("created_at", "id")
    )
    if performed is True:
        services = services.filter(performed=True)
    elif performed is False:
        services = services.filter(performed=False)
    return services


def requested_lab_service_payload(visit_service):
    test_profile = getattr(visit_service.service, "test_profile", None)
    return {
        "visit_service_id": visit_service.pk,
        "service_id": visit_service.service_id,
        "service_name": visit_service.service.name,
        "price": str(visit_service.price_at_time),
        "performed": visit_service.performed,
        "test_profile_id": test_profile.pk if test_profile else None,
        "test_profile_name": test_profile.name if test_profile else "",
    }


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


def prescription_payload(prescription):
    return {
        "id": prescription.pk,
        "drug_name": prescription.drug.name,
        "regimen": prescription.regimen_display,
        "quantity_display": prescription.quantity_display,
        "total_price": str(prescription.total_price),
        "dispensed": prescription.dispensed,
        "dispensed_at": prescription.dispensed_at.strftime("%Y-%m-%d %H:%M") if prescription.dispensed_at else "",
    }


def reverse_dispensed_stock(prescription, *, actor):
    drug = prescription.drug
    stock_quantity_to_restore = drug.to_stock_quantity(prescription.total_quantity)
    if stock_quantity_to_restore <= 0:
        return

    if drug.has_batch_tracking:
        reversal_batch_number = f"REVERSAL-RX-{prescription.pk}"
        drug.add_or_update_batch(
            reversal_batch_number,
            stock_quantity_to_restore,
            unit_cost=drug.unit_cost,
        )
    else:
        drug.current_quantity = Decimal(drug.current_quantity or 0) + stock_quantity_to_restore
        drug.quantity = int(Decimal(drug.current_quantity or 0))
        drug.save(update_fields=["current_quantity", "quantity"])

    InventoryTransaction.objects.create(
        hospital=prescription.visit.hospital,
        item=drug,
        transaction_type=InventoryTransaction.TYPE_ADJUST,
        quantity=prescription.total_quantity,
        unit_cost=drug.unit_cost,
        visit=prescription.visit,
        performed_by=actor,
        notes=f"Prescription {prescription.pk} removed after dispense; stock restored.",
    )


@transaction.atomic
def remove_prescription_workflow(*, prescription, actor):
    visit = prescription.visit
    total_price = Decimal(prescription.total_price or 0)
    removed_drug_name = prescription.drug.name
    dispensed = prescription.dispensed

    if dispensed:
        reverse_dispensed_stock(prescription, actor=actor)

    billing_line = prescription.billing_visit_service
    prescription.delete()

    if billing_line:
        billing_line.delete()

    visit.total_amount = max(Decimal(visit.total_amount or 0) - total_price, Decimal("0"))
    update_fields = ["total_amount"]
    if visit.status == Visit.STATUS_COMPLETED and not visit.is_fully_paid:
        visit.status = Visit.STATUS_READY_FOR_BILLING
        update_fields.append("status")
    visit.save(update_fields=update_fields)

    message = f"{removed_drug_name} removed from the prescription list."
    if dispensed:
        message = f"{removed_drug_name} removed and dispensed stock was restored."

    return {
        "message": message,
        "visit_total_amount": str(visit.total_amount),
        "removed_prescription_id": prescription.pk,
        "prescriptions_remaining": visit.prescriptions.count(),
    }


def pending_lab_queue_reason(visit):
    pending_names = list(
        lab_visit_services(visit, performed=False).values_list("service__name", flat=True)
    )
    if pending_names:
        return f"Doctor requested: {', '.join(pending_names)}"
    return "Doctor requested laboratory follow-up."


def ensure_doctor_lab_queue_entry(*, visit, requested_by):
    return ensure_pending_queue_entry(
        visit=visit,
        hospital=visit.hospital,
        queue_type=QueueEntry.TYPE_LAB_DOCTOR,
        reason=pending_lab_queue_reason(visit),
        requested_by=requested_by,
        notes="Pending lab services requested during consultation.",
    )


@doctor_role_required
@require_http_methods(["GET"])
def lab_services_api(request):
    """AJAX endpoint to get list of lab services for a hospital"""
    hospital = get_active_hospital(request)
    if not hospital:
        return JsonResponse({"error": "No hospital associated with your account"}, status=400)
    
    services = Service.objects.filter(
        hospital=hospital,
        category=Service.CATEGORY_LAB,
        is_active=True
    ).values('id', 'name', 'price').order_by('name')
    
    return JsonResponse(list(services), safe=False)


@doctor_role_required
@require_http_methods(["POST"])
def add_lab_service_api(request):
    """AJAX endpoint for doctors to create new lab services on the fly"""
    hospital = get_active_hospital(request)
    if not hospital:
        return JsonResponse({"error": "No hospital associated with your account"}, status=400)
    
    # Validate authorization
    if getattr(request.user, "role", "") not in {
        User.ROLE_SUPERADMIN,
        User.ROLE_HOSPITAL_ADMIN,
        User.ROLE_DOCTOR,
    }:
        return JsonResponse({"error": "Unauthorized"}, status=403)
    
    try:
        name = request.POST.get("name", "").strip()
        price_str = request.POST.get("price", "").strip()
        
        if not name:
            return JsonResponse({"error": "Service name is required"}, status=400)
        if not price_str:
            return JsonResponse({"error": "Service price is required"}, status=400)
        
        try:
            price = Decimal(price_str)
            if price < 0:
                raise ValueError("Price cannot be negative")
        except (InvalidOperation, ValueError):
            return JsonResponse({"error": "Invalid price format"}, status=400)
        
        # Check if service already exists
        existing_service = Service.objects.filter(
            hospital=hospital,
            name=name,
            category=Service.CATEGORY_LAB
        ).first()
        
        if existing_service:
            return JsonResponse({
                "id": existing_service.id,
                "name": existing_service.name,
                "price": float(existing_service.price),
                "message": "Service already exists"
            })
        
        # Create new service
        service = Service.objects.create(
            hospital=hospital,
            name=name,
            category=Service.CATEGORY_LAB,
            price=price,
            is_active=True
        )
        
        return JsonResponse({
            "id": service.id,
            "name": service.name,
            "price": str(service.price),
            "message": f"Service '{name}' created successfully"
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@doctor_role_required
@require_http_methods(["POST"])
@transaction.atomic
def send_lab_request_api(request, visit_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("hospital").all()
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)

    consultation = getattr(visit, "consultation", None)
    raw_service_ids = request.POST.getlist("service_ids[]") or request.POST.getlist("service_ids")
    single_service_id = request.POST.get("service_id", "").strip()
    if not raw_service_ids and single_service_id:
        raw_service_ids = [single_service_id]

    service_ids = []
    seen_ids = set()
    for raw_service_id in raw_service_ids:
        value = str(raw_service_id).strip()
        if not value or value in seen_ids:
            continue
        seen_ids.add(value)
        service_ids.append(value)

    if not service_ids:
        return JsonResponse({"error": "Select one or more lab services first."}, status=400)

    services = {
        str(service.pk): service
        for service in Service.objects.filter(
            pk__in=service_ids,
            hospital=visit.hospital,
            category=Service.CATEGORY_LAB,
            is_active=True,
        )
    }
    missing_ids = [service_id for service_id in service_ids if service_id not in services]
    if missing_ids:
        return JsonResponse({"error": "One or more selected lab services could not be found."}, status=400)

    consultation_request_ids = []
    if consultation:
        consultation_request_ids = [int(item) for item in (consultation.lab_requests or [])]

    created_visit_services = []
    skipped_pending = []
    skipped_completed = []
    added_total = Decimal("0")

    for service_id in service_ids:
        service = services[service_id]
        existing_visit_service = VisitService.objects.filter(
            visit=visit,
            service=service,
        ).first()
        if existing_visit_service:
            if existing_visit_service.performed:
                skipped_completed.append(service.name)
            else:
                skipped_pending.append(service.name)
            continue

        visit_service = VisitService.objects.create(
            visit=visit,
            service=service,
            price_at_time=service.price,
            notes=f"Requested during consultation by {request.user.get_full_name() or request.user.username}",
        )
        created_visit_services.append(visit_service)
        added_total += service.price
        if consultation and service.pk not in consultation_request_ids:
            consultation_request_ids.append(service.pk)

    if not created_visit_services:
        if skipped_completed:
            completed_message = (
                f"{skipped_completed[0]} already has recorded results on this visit and now lives under Lab Results."
                if len(skipped_completed) == 1
                else f"{', '.join(skipped_completed)} already have recorded results on this visit and now live under Lab Results."
            )
            return JsonResponse(
                {"error": completed_message},
                status=400,
            )
        return JsonResponse(
            {"error": f"{', '.join(skipped_pending)} already {'is' if len(skipped_pending) == 1 else 'are'} pending in the lab workflow for this visit."},
            status=400,
        )

    if added_total:
        visit.total_amount += added_total
        visit.save(update_fields=["total_amount"])

    if consultation and consultation_request_ids != (consultation.lab_requests or []):
        consultation.lab_requests = consultation_request_ids
        consultation.save(update_fields=["lab_requests"])

    ensure_doctor_lab_queue_entry(visit=visit, requested_by=request.user)
    sync_visit_status(visit)

    pending_services = [
        requested_lab_service_payload(item)
        for item in lab_visit_services(visit, performed=False)
    ]

    added_names = [visit_service.service.name for visit_service in created_visit_services]
    if len(added_names) == 1:
        message = f"{added_names[0]} sent to lab."
    else:
        message = f"{len(added_names)} lab requests sent to lab: {', '.join(added_names)}."

    skipped_notes = []
    if skipped_pending:
        skipped_notes.append(f"Already pending: {', '.join(skipped_pending)}.")
    if skipped_completed:
        skipped_notes.append(f"Already completed: {', '.join(skipped_completed)}.")
    if skipped_notes:
        message = f"{message} {' '.join(skipped_notes)}"

    return JsonResponse(
        {
            "message": message,
            "service": requested_lab_service_payload(created_visit_services[0]),
            "services": [requested_lab_service_payload(item) for item in created_visit_services],
            "visit_total_amount": str(visit.total_amount),
            "pending_services": pending_services,
        }
    )


@doctor_role_required
@require_http_methods(["POST"])
@transaction.atomic
def add_billable_service_api(request, visit_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("hospital").all()
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)

    service_id = request.POST.get("service_id", "").strip()
    if not service_id:
        return JsonResponse({"error": "Select a service first."}, status=400)

    service = get_object_or_404(
        Service.objects.exclude(category=Service.CATEGORY_LAB),
        pk=service_id,
        hospital=visit.hospital,
        is_active=True,
    )

    visit_service, created = VisitService.objects.get_or_create(
        visit=visit,
        service=service,
        defaults={
            "price_at_time": service.price,
            "notes": f"Added during consultation by {request.user.get_full_name() or request.user.username}",
        },
    )
    if not created:
        return JsonResponse({"error": f"{service.name} is already on this visit."}, status=400)

    visit.total_amount += service.price
    visit.save(update_fields=["total_amount"])

    return JsonResponse(
        {
            "message": f"{service.name} added to the visit bill.",
            "service": {
                "visit_service_id": visit_service.pk,
                "service_id": service.pk,
                "service_name": service.name,
                "price": str(visit_service.price_at_time),
                "category": service.get_category_display(),
            },
            "visit_total_amount": str(visit.total_amount),
        }
    )


@prescribing_role_required
@require_http_methods(["POST"])
@transaction.atomic
def add_prescription_api(request, visit_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("hospital").all()
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)

    drug_id = request.POST.get("drug_id", "").strip()
    dosage_value = request.POST.get("dosage_mg", "").strip()
    frequency_value = request.POST.get("frequency_per_day", "").strip()
    duration_value = request.POST.get("duration_days", "").strip()
    notes = (request.POST.get("notes") or "").strip()

    if not drug_id:
        return JsonResponse({"error": "Select a medication first."}, status=400)

    try:
        dosage = Decimal(dosage_value)
        frequency = int(frequency_value)
        duration = int(duration_value)
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "Enter a valid dosage, frequency, and duration."}, status=400)

    if dosage <= 0 or frequency <= 0 or duration <= 0:
        return JsonResponse({"error": "Dosage, frequency, and duration must all be greater than zero."}, status=400)

    drug = get_object_or_404(
        InventoryItem,
        pk=drug_id,
        hospital=visit.hospital,
        category__in=[
            InventoryItem.CATEGORY_DRUG,
            InventoryItem.CATEGORY_SYRUP,
            InventoryItem.CATEGORY_IV,
            InventoryItem.CATEGORY_IM,
            InventoryItem.CATEGORY_TUBE,
        ],
        is_active=True,
    )

    prescription = Prescription.objects.create(
        visit=visit,
        drug=drug,
        dosage_mg=dosage,
        frequency_per_day=frequency,
        duration_days=duration,
        notes=notes,
        prescribed_by=request.user,
    )

    service, _ = Service.objects.get_or_create(
        hospital=visit.hospital,
        name=f"Pharmacy Item: {drug.name}",
        defaults={
            "category": Service.CATEGORY_PHARMACY,
            "price": drug.selling_price or Decimal("0"),
            "is_active": True,
        },
    )
    billing_line = VisitService.objects.create(
        visit=visit,
        service=service,
        price_at_time=prescription.total_price,
        notes=f"Prescription: {prescription.regimen_display}",
    )
    prescription.billing_visit_service = billing_line
    prescription.save(update_fields=["billing_visit_service"])

    visit.total_amount += prescription.total_price
    visit.save(update_fields=["total_amount"])

    return JsonResponse(
        {
            "message": f"{drug.name} added to the prescription list.",
            "prescription": prescription_payload(prescription),
            "visit_total_amount": str(visit.total_amount),
        }
    )


@prescribing_role_required
@require_http_methods(["POST"])
@transaction.atomic
def remove_prescription_api(request, visit_id, prescription_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("hospital").all()
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)
    prescription = get_object_or_404(
        Prescription.objects.select_related("drug", "visit", "billing_visit_service"),
        pk=prescription_id,
        visit=visit,
    )

    payload = remove_prescription_workflow(prescription=prescription, actor=request.user)
    return JsonResponse(payload)


@doctor_role_required
def doctor_queue(request):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.filter(
        queue_type=QueueEntry.TYPE_DOCTOR,
        processed=False,
    ).select_related("visit__patient", "hospital", "requested_by")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)
    queue_entries = queue_entries.order_by("created_at")
    queued_items = [
        {"entry": entry, "is_results_ready": queue_reason_is_results_ready(entry.reason)}
        for entry in queue_entries
    ]

    completed_consultations = Consultation.objects.select_related(
        "visit__patient",
        "visit__hospital",
        "created_by",
    )
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        completed_consultations = completed_consultations.filter(visit__hospital=hospital)

    recent_consultations = []
    for consultation in completed_consultations.order_by("-created_at")[:12]:
        nurse_entries = consultation.visit.queue_entries.filter(
            queue_type=QueueEntry.TYPE_NURSE
        ).order_by("-created_at")
        latest_nurse_entry = nurse_entries.first()
        recent_consultations.append(
            {
                "consultation": consultation,
                "latest_nurse_entry": latest_nurse_entry,
                "nurse_notes_count": consultation.visit.nurse_notes.count(),
            }
        )

    return render(
        request,
        "doctor/doctor_queue.html",
        {
            "active_nav": "doctor",
            "queue_entries": queued_items,
            "recent_consultations": recent_consultations,
        },
    )


def queue_reason_is_results_ready(reason: str) -> bool:
    return "lab results ready" in (reason or "").lower()


@doctor_role_required
@transaction.atomic
def consultation(request, visit_id):
    hospital = get_active_hospital(request)
    visits = Visit.objects.select_related("patient", "hospital", "triage").all()
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        visits = visits.filter(hospital=hospital)
    visit = get_object_or_404(visits, pk=visit_id)
    consultation_instance = getattr(visit, "consultation", None)
    try:
        triage_instance = visit.triage
    except Triage.DoesNotExist:
        triage_instance = None
    lab_reports = LabReport.objects.filter(visit=visit).prefetch_related("results__test")
    nurse_queue_entries = visit.queue_entries.filter(queue_type=QueueEntry.TYPE_NURSE).order_by("-created_at")
    nurse_notes = NurseNote.objects.filter(visit=visit).select_related("created_by")
    prescriptions = list(
        visit.prescriptions.select_related("drug", "dispensed_by").order_by("-prescribed_at", "-id")
    )
    existing_lab_service_ids = list(
        VisitService.objects.filter(
            visit=visit,
            service__category=Service.CATEGORY_LAB,
        ).values_list("service_id", flat=True)
    )
    available_lab_services = list(
        Service.objects.filter(
            hospital=visit.hospital,
            category=Service.CATEGORY_LAB,
            is_active=True,
        )
        .exclude(pk__in=existing_lab_service_ids)
        .order_by("name")
        .values("id", "name", "price")
    )
    added_non_lab_visit_services = list(
        VisitService.objects.filter(visit=visit)
        .exclude(service__category=Service.CATEGORY_LAB)
        .select_related("service")
        .order_by("created_at", "id")
    )
    existing_billable_service_ids = [item.service_id for item in added_non_lab_visit_services]
    available_billable_services = list(
        Service.objects.filter(
            hospital=visit.hospital,
            is_active=True,
        )
        .exclude(category__in=[Service.CATEGORY_LAB, Service.CATEGORY_PHARMACY])
        .exclude(pk__in=existing_billable_service_ids)
        .order_by("category", "name")
        .values("id", "name", "price", "category")
    )
    available_drugs = list(
        InventoryItem.objects.filter(
            hospital=visit.hospital,
            category__in=[
                InventoryItem.CATEGORY_DRUG,
                InventoryItem.CATEGORY_SYRUP,
                InventoryItem.CATEGORY_IV,
                InventoryItem.CATEGORY_IM,
                InventoryItem.CATEGORY_TUBE,
            ],
            is_active=True,
        )
        .order_by("name")
    )
    pending_lab_services = list(lab_visit_services(visit, performed=False))
    completed_lab_services = list(lab_visit_services(visit, performed=True))

    if request.method == "POST":
        form = ConsultationForm(
            request.POST,
            instance=consultation_instance,
            hospital=visit.hospital,
            triage=triage_instance,
        )
        if form.is_valid():
            consultation = form.save(commit=False)
            consultation.visit = visit
            consultation.created_by = request.user
            consultation.save()

            feedback = [f"Consultation saved for {visit.patient.name}."]

            # Update shared triage (unless doctor explicitly sent patient to nurse for triage).
            send_to_nurse = form.cleaned_data.get("send_to_nurse", False)
            triage_data = form.cleaned_triage_data()
            has_any_triage_value = any(value is not None for value in triage_data.values())
            if not send_to_nurse and has_any_triage_value:
                triage_obj, created = Triage.objects.get_or_create(
                    visit=visit,
                    defaults={
                        "recorded_by": request.user,
                        "updated_by": request.user,
                    },
                )
                for key, value in triage_data.items():
                    setattr(triage_obj, key, value)
                if created and not triage_obj.recorded_by_id:
                    triage_obj.recorded_by = request.user
                triage_obj.updated_by = request.user
                triage_obj.save()

            if form.cleaned_data.get("send_to_nurse"):
                ensure_pending_queue_entry(
                    visit=visit,
                    hospital=visit.hospital,
                    queue_type=QueueEntry.TYPE_NURSE,
                    reason="Doctor requested nursing follow-up.",
                    requested_by=request.user,
                    notes="Consultation completed and handed off to nurse.",
                )
                close_doctor_queue = True
                feedback.append("Patient sent to nurse queue.")
            else:
                close_doctor_queue = False

            if form.cleaned_data.get("send_to_reception"):
                send_to_reception_queue(
                    visit=visit,
                    hospital=visit.hospital,
                    source="Doctor",
                    detail="Consultation completed and returned to reception.",
                    notes="Doctor has completed consultation. Reception should finalize the next step.",
                    requested_by=request.user,
                )
                close_doctor_queue = True
                feedback.append("Patient returned to receptionist queue.")

            if close_doctor_queue:
                mark_queue_entries_processed(visit=visit, queue_type=QueueEntry.TYPE_DOCTOR)

            sync_visit_status(visit)
            messages.success(request, " ".join(feedback))
            return redirect("consultation_detail", visit_id=visit.pk)
        messages.error(request, "Please fix the consultation details below.")
    else:
        form = ConsultationForm(instance=consultation_instance, hospital=visit.hospital, triage=triage_instance)

    return render(
        request,
        "doctor/consultation_form.html",
        {
            "active_nav": "doctor",
            "visit": visit,
            "lab_reports": lab_reports,
            "form": form,
            "consultation_instance": consultation_instance,
            "nurse_queue_entries": nurse_queue_entries,
            "nurse_notes": nurse_notes,
            "available_lab_services": available_lab_services,
            "available_billable_services": available_billable_services,
            "available_drugs": available_drugs,
            "pending_lab_services": pending_lab_services,
            "completed_lab_services": completed_lab_services,
            "added_non_lab_visit_services": added_non_lab_visit_services,
            "prescriptions": prescriptions,
        },
    )


@doctor_role_required
def consultation_detail(request, visit_id):
    hospital = get_active_hospital(request)
    consultations = Consultation.objects.select_related(
        "visit__patient",
        "visit__hospital",
        "created_by",
    )
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        consultations = consultations.filter(visit__hospital=hospital)

    consultation_instance = get_object_or_404(consultations, visit_id=visit_id)
    visit = consultation_instance.visit
    triage = Triage.objects.filter(visit=visit).first()
    lab_reports = LabReport.objects.filter(visit=visit).prefetch_related("results__test")
    nurse_queue_entries = visit.queue_entries.filter(queue_type=QueueEntry.TYPE_NURSE).order_by("-created_at")
    nurse_notes = NurseNote.objects.filter(visit=visit).select_related("created_by")

    return render(
        request,
        "doctor/consultation_detail.html",
        {
            "active_nav": "doctor",
            "visit": visit,
            "triage": triage,
            "consultation_instance": consultation_instance,
            "lab_reports": lab_reports,
            "nurse_queue_entries": nurse_queue_entries,
            "nurse_notes": nurse_notes,
        },
    )


@doctor_role_required
def doctor_lab_requests(request):
    messages.info(
        request,
        "Lab requests now happen directly inside the consultation form. Open a visit from the doctor queue to request tests.",
    )
    return redirect("doctor_queue")


@doctor_role_required
@transaction.atomic
def create_lab_request(request):
    messages.info(
        request,
        "Use the consultation page to request lab services. The older standalone lab-request screen has been retired.",
    )
    visit_id = request.GET.get("visit_id") or request.POST.get("visit_id")
    if visit_id:
        return redirect("consultation", visit_id=visit_id)
    return redirect("doctor_queue")


@doctor_role_required
def view_lab_request(request, lab_request_id):
    lab_request = get_object_or_404(LabRequest.objects.select_related("visit"), pk=lab_request_id)
    messages.info(
        request,
        "This older lab-request page is now read-only. Continue the workflow from the consultation page.",
    )
    return redirect("consultation", visit_id=lab_request.visit_id)
