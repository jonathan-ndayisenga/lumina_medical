from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from lab.models import LabReport
from nurse.models import NurseNote
from reception.models import QueueEntry, Service, Triage, Visit, VisitService
from reception.workflow import ensure_pending_queue_entry, sync_visit_status

from .forms import ConsultationForm
from .models import Consultation, LabRequest


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


def get_active_hospital(request):
    return getattr(request, "hospital", None) or getattr(request.user, "hospital", None)


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
            price = float(price_str)
            if price < 0:
                raise ValueError("Price cannot be negative")
        except ValueError:
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
            "price": float(service.price),
            "message": f"Service '{name}' created successfully"
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


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
    available_lab_services = list(
        Service.objects.filter(
            hospital=visit.hospital,
            category=Service.CATEGORY_LAB,
            is_active=True,
        )
        .order_by("name")
        .values("id", "name", "price")
    )
    selected_service_ids = [int(service_id) for service_id in (consultation_instance.lab_requests or [])] if consultation_instance else []
    selected_lab_services = list(
        Service.objects.filter(id__in=selected_service_ids, hospital=visit.hospital).order_by("name")
    )

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
             
            # Handle selected lab services from hidden field (CSV: "1,2,3")
            lab_services_str = form.cleaned_data.get("lab_services", "").strip()
            service_ids = []
            
            if lab_services_str:
                try:
                    service_ids = [int(sid.strip()) for sid in lab_services_str.split(",") if sid.strip()]
                except ValueError:
                    service_ids = []
            
            if service_ids:
                # Get the Service objects for the selected IDs
                selected_services = Service.objects.filter(
                    id__in=service_ids,
                    hospital=visit.hospital,
                    category=Service.CATEGORY_LAB,
                    is_active=True
                ).order_by("name")
                
                service_names = []
                stored_service_ids = []
                
                for service in selected_services:
                    # Check if this service is already in the visit (avoid duplicates)
                    existing = VisitService.objects.filter(
                        visit=visit,
                        service=service
                    ).exists()
                    
                    if not existing:
                        # Create VisitService record
                        visit_service = VisitService.objects.create(
                            visit=visit,
                            service=service,
                            price_at_time=service.price,
                            notes=f"Requested during consultation by {request.user.get_full_name() or request.user.username}"
                        )
                        # Update visit total amount
                        visit.total_amount += service.price
                        service_names.append(service.name)
                    
                    stored_service_ids.append(service.id)
                
                # Save the updated visit total
                visit.save(update_fields=["total_amount"])
                
                # Store service IDs in consultation for reference
                consultation.lab_requests = stored_service_ids
                consultation.save(update_fields=["lab_requests"])
                
                # Create a single lab queue entry with all requested tests
                if service_names:
                    ensure_pending_queue_entry(
                        visit=visit,
                        hospital=visit.hospital,
                        queue_type=QueueEntry.TYPE_LAB_DOCTOR,
                        reason=f"Doctor requested: {', '.join(service_names)}",
                        requested_by=request.user,
                        notes=f"Multiple lab services requested during consultation",
                    )
                    feedback.append(f"Lab services requested: {', '.join(service_names)}")

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
                visit.status = Visit.STATUS_READY_FOR_BILLING
                visit.save(update_fields=["status"])
                close_doctor_queue = True
                feedback.append("Patient marked ready for reception billing.")

            if close_doctor_queue:
                QueueEntry.objects.filter(
                    visit=visit,
                    queue_type=QueueEntry.TYPE_DOCTOR,
                    processed=False,
                ).update(processed=True, processed_at=timezone.now())

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
            "selected_lab_services": selected_lab_services,
            "selected_lab_service_ids": selected_service_ids,
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
