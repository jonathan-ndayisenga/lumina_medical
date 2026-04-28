from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import User
from admin_dashboard.models import InventoryTransaction
from doctor.models import Consultation, Prescription
from lab.models import LabReport
from reception.models import QueueEntry, Triage, Visit
from reception.workflow import ensure_pending_queue_entry, sync_visit_status

from .forms import NurseNoteForm, TriageForm
from .models import NurseNote


def nurse_role_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        user = request.user
        allowed = getattr(user, "role", "") in {
            User.ROLE_SUPERADMIN,
            User.ROLE_HOSPITAL_ADMIN,
            User.ROLE_NURSE,
        } or user.groups.filter(name="Nurse").exists()
        if not allowed:
            return redirect("app_home")
        return view_func(request, *args, **kwargs)

    return wrapped


def get_active_hospital(request):
    return getattr(request, "hospital", None) or getattr(request.user, "hospital", None)


@nurse_role_required
def nurse_queue(request):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.filter(
        queue_type=QueueEntry.TYPE_NURSE,
        processed=False,
    ).select_related("visit__patient", "hospital", "requested_by", "visit__triage")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)
    return render(
        request,
        "nurse/nurse_queue.html",
        {
            "active_nav": "nurse",
            "queue_entries": queue_entries.order_by("created_at"),
        },
    )


@nurse_role_required
@transaction.atomic
def perform_nursing(request, queue_entry_id):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.select_related("visit__patient", "visit__hospital").filter(
        queue_type=QueueEntry.TYPE_NURSE
    )
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)
    queue_entry = get_object_or_404(queue_entries, pk=queue_entry_id)
    visit = queue_entry.visit
    prescriptions = list(
        visit.prescriptions.select_related("drug", "dispensed_by").order_by("-dispensed", "-prescribed_at", "-id")
    )
    triage_obj, triage_created = Triage.objects.get_or_create(
        visit=visit,
        defaults={
            "recorded_by": request.user,
            "updated_by": request.user,
        },
    )

    if request.method == "POST":
        triage_form = TriageForm(request.POST, instance=triage_obj)
        note_form = NurseNoteForm(request.POST)
        action = request.POST.get("action") or ""
        note_text = (request.POST.get("notes") or "").strip()

        # Triage-only actions should not require a nursing note.
        triage_only_action = action in {"triage_to_doctor", "triage_to_reception"}

        if triage_form.is_valid() and (triage_only_action or note_form.is_valid()):
            triage_saved = triage_form.save(commit=False)
            if triage_created and not triage_saved.recorded_by_id:
                triage_saved.recorded_by = request.user
            triage_saved.updated_by = request.user
            triage_saved.save()

            nurse_note = None
            if note_text:
                nurse_note = note_form.save(commit=False)
                nurse_note.visit = visit
                nurse_note.created_by = request.user
                nurse_note.save()

            if action == "triage_to_doctor":
                ensure_pending_queue_entry(
                    visit=visit,
                    hospital=visit.hospital,
                    queue_type=QueueEntry.TYPE_DOCTOR,
                    reason="Triage completed: Ready for consultation",
                    requested_by=request.user,
                    notes="Patient routed to doctor after triage.",
                )
            elif action == "triage_to_reception":
                messages.info(request, "Triage saved. Patient handed back to reception.")
            elif action == "back_to_doctor":
                ensure_pending_queue_entry(
                    visit=visit,
                    hospital=visit.hospital,
                    queue_type=QueueEntry.TYPE_DOCTOR,
                    reason=f"Nurse completed: {(nurse_note.notes if nurse_note else 'Nursing care updated')[:120]}",
                    requested_by=request.user,
                    notes="Patient sent back to doctor after nursing care.",
                )
            elif action == "to_billing":
                visit.status = Visit.STATUS_READY_FOR_BILLING
                visit.save(update_fields=["status"])
            else:
                messages.error(request, "Choose where the patient should go after nursing care.")
                consultation = Consultation.objects.filter(visit=visit).select_related("created_by").first()
                lab_reports = LabReport.objects.filter(visit=visit).prefetch_related("results__test")
                existing_notes = NurseNote.objects.filter(visit=visit).select_related("created_by")
                return render(
                    request,
                    "nurse/nursing_form.html",
                    {
                        "active_nav": "nurse",
                        "queue_entry": queue_entry,
                        "visit": visit,
                        "consultation": consultation,
                        "lab_reports": lab_reports,
                        "existing_notes": existing_notes,
                        "triage_form": triage_form,
                        "form": note_form,
                        "triage": triage_obj,
                        "prescriptions": prescriptions,
                    },
                )

            queue_entry.processed = True
            queue_entry.processed_at = timezone.now()
            queue_entry.save(update_fields=["processed", "processed_at"])
            sync_visit_status(visit)

            messages.success(request, "Nurse task completed.")
            return redirect("nurse_queue")

        if not triage_form.is_valid():
            messages.error(request, "Please complete the required triage fields (weight and blood pressure) before saving.")
        elif triage_only_action:
            messages.error(request, "Please complete the triage fields before saving.")
        else:
            messages.error(request, "Please complete the nursing note before saving.")
    else:
        triage_form = TriageForm(instance=triage_obj)
        note_form = NurseNoteForm()

    consultation = Consultation.objects.filter(visit=visit).select_related("created_by").first()
    lab_reports = LabReport.objects.filter(visit=visit).prefetch_related("results__test")
    existing_notes = NurseNote.objects.filter(visit=visit).select_related("created_by")

    return render(
        request,
        "nurse/nursing_form.html",
        {
            "active_nav": "nurse",
            "queue_entry": queue_entry,
            "visit": visit,
            "consultation": consultation,
            "lab_reports": lab_reports,
            "existing_notes": existing_notes,
            "triage_form": triage_form,
            "form": note_form,
            "triage": triage_obj,
            "prescriptions": prescriptions,
        },
    )


@nurse_role_required
@transaction.atomic
def dispense_prescription(request, queue_entry_id, prescription_id):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.select_related("visit__hospital").filter(queue_type=QueueEntry.TYPE_NURSE)
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)
    queue_entry = get_object_or_404(queue_entries, pk=queue_entry_id)

    prescription_qs = Prescription.objects.select_related("drug", "visit__hospital", "billing_visit_service").filter(
        visit=queue_entry.visit
    )
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        prescription_qs = prescription_qs.filter(visit__hospital=hospital)
    prescription = get_object_or_404(prescription_qs, pk=prescription_id)

    if request.method != "POST":
        raise PermissionDenied("Dispensing requires a POST request.")

    if prescription.dispensed:
        messages.info(request, f"{prescription.drug.name} was already dispensed for this visit.")
        return redirect("perform_nursing", queue_entry_id=queue_entry.pk)

    drug = prescription.drug
    quantity_to_deduct = prescription.total_quantity
    if drug.current_quantity < quantity_to_deduct:
        messages.error(
            request,
            f"Insufficient stock for {drug.name}. Available: {drug.quantity_label}. Needed: {prescription.quantity_display}.",
        )
        return redirect("perform_nursing", queue_entry_id=queue_entry.pk)

    drug.current_quantity -= quantity_to_deduct
    drug.save(update_fields=["current_quantity", "quantity", "unit_price", "low_stock_threshold"])

    InventoryTransaction.objects.create(
        hospital=queue_entry.visit.hospital,
        item=drug,
        transaction_type=InventoryTransaction.TYPE_CONSUME,
        quantity=quantity_to_deduct,
        unit_cost=drug.unit_cost,
        visit=queue_entry.visit,
        prescription=prescription,
        performed_by=request.user,
        notes=f"Dispensed via nurse workflow for prescription {prescription.pk}",
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
    return redirect("perform_nursing", queue_entry_id=queue_entry.pk)
