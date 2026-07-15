from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from decimal import Decimal

from accounts.models import User
from admin_dashboard.models import InventoryTransaction
from doctor.models import Consultation, Prescription
from lab.models import LabReport
from reception.models import QueueEntry, Triage, Visit
from reception.workflow import close_competing_queue_entries, ensure_pending_queue_entry, send_to_reception_queue, sync_visit_status

from .forms import NurseNoteForm, TriageForm
from .models import NurseNote, NursingAdmission, NursingCareItem, NursingDose, ScanReport


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
    if visit.status == Visit.STATUS_CANCELLED:
        messages.error(request, "This visit was terminated by an administrator and can no longer continue in nursing.")
        queue_entry.processed = True
        queue_entry.processed_at = timezone.now()
        queue_entry.save(update_fields=["processed", "processed_at"])
        return redirect("nurse_queue")
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
                close_competing_queue_entries(visit, QueueEntry.TYPE_DOCTOR)
                ensure_pending_queue_entry(
                    visit=visit,
                    hospital=visit.hospital,
                    queue_type=QueueEntry.TYPE_DOCTOR,
                    reason="Triage completed: Ready for consultation",
                    requested_by=request.user,
                    notes="Patient routed to doctor after triage.",
                )
            elif action == "triage_to_reception":
                close_competing_queue_entries(visit, QueueEntry.TYPE_RECEPTION)
                send_to_reception_queue(
                    visit=visit,
                    hospital=visit.hospital,
                    source="Nurse",
                    detail="Triage completed and returned to reception.",
                    notes="Reception should decide the next action after triage.",
                    requested_by=request.user,
                )
                messages.info(request, "Triage saved. Patient handed back to receptionist queue.")
            elif action == "back_to_doctor":
                close_competing_queue_entries(visit, QueueEntry.TYPE_DOCTOR)
                ensure_pending_queue_entry(
                    visit=visit,
                    hospital=visit.hospital,
                    queue_type=QueueEntry.TYPE_DOCTOR,
                    reason=f"Nurse completed: {(nurse_note.notes if nurse_note else 'Nursing care updated')[:120]}",
                    requested_by=request.user,
                    notes="Patient sent back to doctor after nursing care.",
                )
            elif action == "to_billing":
                close_competing_queue_entries(visit, QueueEntry.TYPE_RECEPTION)
                send_to_reception_queue(
                    visit=visit,
                    hospital=visit.hospital,
                    source="Nurse",
                    detail="Nursing care complete and ready for billing review.",
                    notes="Reception should confirm billing and any final dispense needs.",
                    requested_by=request.user,
                )
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
def remove_prescription_from_nurse(request, queue_entry_id, prescription_id):
    from django.http import JsonResponse
    from doctor.views import remove_prescription_workflow

    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.select_related("visit__hospital").filter(queue_type=QueueEntry.TYPE_NURSE)
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)
    queue_entry = get_object_or_404(queue_entries, pk=queue_entry_id)

    prescription_qs = Prescription.objects.select_related("drug", "visit", "billing_visit_service").filter(
        visit=queue_entry.visit
    )
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        prescription_qs = prescription_qs.filter(visit__hospital=hospital)
    prescription = get_object_or_404(prescription_qs, pk=prescription_id)

    if request.method != "POST":
        raise PermissionDenied("Removing a prescription requires a POST request.")
    if prescription.dispensed:
        messages.error(request, f"{prescription.drug.name} has already been dispensed and cannot be removed here.")
        return redirect("perform_nursing", queue_entry_id=queue_entry.pk)

    payload = remove_prescription_workflow(prescription=prescription, actor=request.user)
    messages.success(request, payload["message"])
    return redirect("perform_nursing", queue_entry_id=queue_entry.pk)


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
    if queue_entry.visit.status == Visit.STATUS_CANCELLED:
        raise PermissionDenied("Cancelled visits cannot dispense medication.")

    if prescription.dispensed:
        messages.info(request, f"{prescription.drug.name} was already dispensed for this visit.")
        return redirect("perform_nursing", queue_entry_id=queue_entry.pk)

    drug = prescription.drug
    quantity_to_deduct = prescription.total_quantity
    stock_quantity_to_deduct = drug.to_stock_quantity(quantity_to_deduct)
    available_dispense_quantity = drug.available_dispense_quantity
    if available_dispense_quantity < quantity_to_deduct:
        messages.error(
            request,
            f"Cannot dispense {drug.name} - insufficient stock available. Current stock: {drug.quantity_label}, but prescription requires: {prescription.quantity_display}. Please restock the inventory or adjust the prescription.",
        )
        return redirect("perform_nursing", queue_entry_id=queue_entry.pk)

    drug.consume_stock(stock_quantity_to_deduct)

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


# ── Nursing Care (IV Admissions) ──────────────────────────────────────────────

@nurse_role_required
def nursing_admissions(request):
    """List all active and recent nursing care admissions."""
    hospital = get_active_hospital(request)
    active = NursingAdmission.objects.filter(
        hospital=hospital, status=NursingAdmission.STATUS_ACTIVE
    ).select_related("visit__patient", "admitted_by").prefetch_related("care_items__doses")

    discharged = NursingAdmission.objects.filter(
        hospital=hospital, status=NursingAdmission.STATUS_DISCHARGED
    ).select_related("visit__patient", "admitted_by").order_by("-discharged_at")[:20]

    return render(request, "nurse/nursing_admissions.html", {
        "active_nav": "nurse",
        "active_admissions": active,
        "discharged_admissions": discharged,
    })


@nurse_role_required
def start_nursing_admission(request, visit_id):
    """Admit a patient for IV nursing care — select which prescriptions to manage."""
    hospital = get_active_hospital(request)
    visit = get_object_or_404(Visit, pk=visit_id, hospital=hospital)

    # Can't admit twice
    if hasattr(visit, "nursing_admission"):
        messages.info(request, f"{visit.patient.name} is already under nursing care.")
        return redirect("nursing_admission_detail", admission_id=visit.nursing_admission.pk)

    # Only IV/infusion prescriptions make sense for nursing care
    iv_prescriptions = visit.prescriptions.filter(
        dispensed=False,
        nursing_managed=False,
    ).select_related("drug").exclude(drug__category__in=["tablet", "capsule"])

    if request.method == "POST":
        selected_ids = request.POST.getlist("prescription_ids")
        if not selected_ids:
            messages.error(request, "Select at least one prescription to manage.")
        else:
            admission = NursingAdmission.objects.create(
                visit=visit,
                hospital=hospital,
                admitted_by=request.user,
            )
            for rx_id in selected_ids:
                try:
                    rx = iv_prescriptions.get(pk=rx_id)
                except Prescription.DoesNotExist:
                    continue
                doses_planned = max(1, (rx.frequency_per_day or 1) * (rx.duration_days or 1))
                per_dose_qty = (Decimal(rx.total_quantity or 0) / doses_planned).quantize(Decimal("0.0001"))
                NursingCareItem.objects.create(
                    admission=admission,
                    prescription=rx,
                    doses_planned=doses_planned,
                    per_dose_quantity=per_dose_qty,
                )
                rx.nursing_managed = True
                rx.save(update_fields=["nursing_managed"])

            messages.success(request, f"{visit.patient.name} admitted for nursing care.")
            return redirect("nursing_admission_detail", admission_id=admission.pk)

    return render(request, "nurse/start_nursing_admission.html", {
        "active_nav": "nurse",
        "visit": visit,
        "iv_prescriptions": iv_prescriptions,
    })


@nurse_role_required
def nursing_admission_detail(request, admission_id):
    """Main working view — give doses, view history, discharge patient."""
    hospital = get_active_hospital(request)
    admission = get_object_or_404(
        NursingAdmission.objects.select_related("visit__patient", "admitted_by")
        .prefetch_related(
            "care_items__prescription__drug",
            "care_items__doses__administered_by",
            "care_items__stopped_by",
        ),
        pk=admission_id,
        hospital=hospital,
    )
    active_items = [ci for ci in admission.care_items.all() if ci.is_active and not ci.is_complete]
    completed_items = [ci for ci in admission.care_items.all() if ci.is_complete]
    stopped_items = [ci for ci in admission.care_items.all() if not ci.is_active and not ci.is_complete]

    return render(request, "nurse/nursing_admission_detail.html", {
        "active_nav": "nurse",
        "admission": admission,
        "active_items": active_items,
        "completed_items": completed_items,
        "stopped_items": stopped_items,
    })


@nurse_role_required
@transaction.atomic
def administer_dose(request, care_item_id):
    """Record one dose administration and deduct from inventory."""
    if request.method != "POST":
        return redirect("nursing_admissions")

    hospital = get_active_hospital(request)
    care_item = get_object_or_404(
        NursingCareItem.objects.select_related(
            "admission__visit__hospital", "prescription__drug"
        ),
        pk=care_item_id,
        admission__hospital=hospital,
    )

    if not care_item.is_active:
        messages.error(request, "This medication has been stopped.")
        return redirect("nursing_admission_detail", admission_id=care_item.admission_id)

    if care_item.is_complete:
        messages.info(request, "All doses for this medication have already been given.")
        return redirect("nursing_admission_detail", admission_id=care_item.admission_id)

    drug = care_item.prescription.drug
    per_dose_qty = care_item.per_dose_quantity
    stock_qty = drug.to_stock_quantity(per_dose_qty)

    if drug.available_dispense_quantity < per_dose_qty:
        messages.error(
            request,
            f"Insufficient stock for {drug.name}. Available: {drug.quantity_label}. "
            f"Required per dose: {per_dose_qty} {drug.base_unit}(s). Please restock."
        )
        return redirect("nursing_admission_detail", admission_id=care_item.admission_id)

    notes = (request.POST.get("notes") or "").strip()

    # Deduct inventory
    drug.consume_stock(stock_qty)
    InventoryTransaction.objects.create(
        hospital=hospital,
        item=drug,
        transaction_type=InventoryTransaction.TYPE_CONSUME,
        quantity=per_dose_qty,
        unit_cost=drug.unit_cost,
        visit=care_item.admission.visit,
        prescription=care_item.prescription,
        performed_by=request.user,
        notes=f"Nursing dose {care_item.doses_given + 1}/{care_item.doses_planned} — {drug.name}",
    )

    # Record the dose
    NursingDose.objects.create(
        care_item=care_item,
        administered_by=request.user,
        quantity_given=per_dose_qty,
        notes=notes,
    )

    # If all doses complete, mark prescription as dispensed
    care_item.refresh_from_db()
    if care_item.is_complete:
        rx = care_item.prescription
        rx.dispensed = True
        rx.dispensed_at = timezone.now()
        rx.dispensed_by = request.user
        rx.save(update_fields=["dispensed", "dispensed_at", "dispensed_by"])
        if rx.billing_visit_service_id:
            rx.billing_visit_service.performed = True
            rx.billing_visit_service.performed_at = timezone.now()
            rx.billing_visit_service.save(update_fields=["performed", "performed_at"])
        messages.success(request, f"Dose given. All {care_item.doses_planned} doses of {drug.name} complete.")
    else:
        messages.success(
            request,
            f"Dose {care_item.doses_given}/{care_item.doses_planned} recorded for {drug.name}."
        )

    return redirect("nursing_admission_detail", admission_id=care_item.admission_id)


@nurse_role_required
@transaction.atomic
def stop_care_item(request, care_item_id):
    """Stop a medication mid-treatment (e.g. doctor changed the prescription)."""
    if request.method != "POST":
        return redirect("nursing_admissions")

    hospital = get_active_hospital(request)
    care_item = get_object_or_404(
        NursingCareItem.objects.select_related("admission__visit__hospital", "prescription__drug"),
        pk=care_item_id,
        admission__hospital=hospital,
    )

    reason = (request.POST.get("stop_reason") or "").strip()
    care_item.is_active = False
    care_item.stopped_at = timezone.now()
    care_item.stopped_by = request.user
    care_item.stop_reason = reason or "Stopped by nurse"
    care_item.save(update_fields=["is_active", "stopped_at", "stopped_by", "stop_reason"])

    messages.success(request, f"{care_item.prescription.drug.name} stopped. {care_item.doses_given} of {care_item.doses_planned} doses were given.")
    return redirect("nursing_admission_detail", admission_id=care_item.admission_id)


@nurse_role_required
@transaction.atomic
def discharge_nursing(request, admission_id):
    """Discharge a patient from nursing care."""
    if request.method != "POST":
        return redirect("nursing_admissions")

    hospital = get_active_hospital(request)
    admission = get_object_or_404(NursingAdmission, pk=admission_id, hospital=hospital)

    notes = (request.POST.get("discharge_notes") or "").strip()
    admission.status = NursingAdmission.STATUS_DISCHARGED
    admission.discharged_at = timezone.now()
    admission.discharged_by = request.user
    admission.discharge_notes = notes
    admission.save(update_fields=["status", "discharged_at", "discharged_by", "discharge_notes"])

    # Stop any still-active care items
    admission.care_items.filter(is_active=True, doses__isnull=False).update(
        is_active=False, stopped_at=timezone.now(), stop_reason="Patient discharged"
    )

    messages.success(request, f"{admission.visit.patient.name} discharged from nursing care.")
    return redirect("nursing_admissions")


# ── Sonographer Queue & Scan Reports ─────────────────────────────────────────

def sonographer_role_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not getattr(request.user, "can_access_sonographer", False):
            return redirect("app_home")
        return view_func(request, *args, **kwargs)
    return wrapped


@sonographer_role_required
def scan_queue(request):
    from django.core.paginator import Paginator

    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.filter(
        queue_type=QueueEntry.TYPE_SONOGRAPHER,
        processed=False,
    ).select_related("visit__patient", "hospital", "requested_by").order_by("created_at")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)

    recent_reports_qs = ScanReport.objects.select_related(
        "visit__patient", "visit__hospital", "sonographer"
    ).order_by("-created_at")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        recent_reports_qs = recent_reports_qs.filter(visit__hospital=hospital)

    paginator = Paginator(recent_reports_qs, 10)
    page_number = request.GET.get("page")
    recent_reports = paginator.get_page(page_number)

    return render(request, "nurse/scan_queue.html", {
        "active_nav": "scan_queue",
        "queue_entries": queue_entries,
        "recent_reports": recent_reports,
    })


@sonographer_role_required
@transaction.atomic
def scan_report(request, queue_entry_id):
    hospital = get_active_hospital(request)
    queue_entries = QueueEntry.objects.select_related("visit__patient", "visit__hospital").filter(
        queue_type=QueueEntry.TYPE_SONOGRAPHER,
    )
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        queue_entries = queue_entries.filter(hospital=hospital)
    queue_entry = get_object_or_404(queue_entries, pk=queue_entry_id)
    visit = queue_entry.visit

    existing_report = visit.scan_reports.first()

    if request.method == "POST":
        scan_type = request.POST.get("scan_type", ScanReport.SCAN_OTHER)
        clinical_indication = (request.POST.get("clinical_indication") or "").strip()
        findings = (request.POST.get("findings") or "").strip()
        impression = (request.POST.get("impression") or "").strip()
        action = request.POST.get("action", "draft")

        if not findings or not impression:
            messages.error(request, "Findings and impression are required.")
        else:
            status = ScanReport.STATUS_FINAL if action == "finalize" else ScanReport.STATUS_DRAFT
            if existing_report:
                existing_report.scan_type = scan_type
                existing_report.clinical_indication = clinical_indication
                existing_report.findings = findings
                existing_report.impression = impression
                existing_report.status = status
                existing_report.sonographer = request.user
                existing_report.save()
                report = existing_report
            else:
                report = ScanReport.objects.create(
                    visit=visit,
                    sonographer=request.user,
                    scan_type=scan_type,
                    clinical_indication=clinical_indication,
                    findings=findings,
                    impression=impression,
                    status=status,
                )

            if action == "finalize":
                from reception.workflow import mark_queue_entries_processed, send_to_reception_queue
                handoff = request.POST.get("handoff", "billing")
                mark_queue_entries_processed(visit=visit, queue_type=QueueEntry.TYPE_SONOGRAPHER)

                if handoff == "nurse":
                    ensure_pending_queue_entry(
                        visit=visit,
                        hospital=visit.hospital,
                        queue_type=QueueEntry.TYPE_NURSE,
                        reason="Scan completed — sent to nurse by sonographer.",
                        requested_by=request.user,
                    )
                    msg = f"Scan report finalized for {visit.patient.name}. Patient sent to nurse queue."
                elif handoff == "doctor":
                    ensure_pending_queue_entry(
                        visit=visit,
                        hospital=visit.hospital,
                        queue_type=QueueEntry.TYPE_DOCTOR,
                        reason="Scan completed — results sent back to doctor.",
                        requested_by=request.user,
                    )
                    msg = f"Scan report finalized for {visit.patient.name}. Results sent back to doctor."
                else:
                    send_to_reception_queue(visit=visit, requested_by=request.user, reason="Scan report finalized — patient ready for billing.")
                    msg = f"Scan report finalized for {visit.patient.name}. Patient sent to reception for billing."

                from reception.workflow import sync_visit_status
                sync_visit_status(visit)
                messages.success(request, msg)
                return redirect("scan_queue")
            else:
                messages.success(request, "Report saved as draft.")
                return redirect("scan_report", queue_entry_id=queue_entry.pk)

    return render(request, "nurse/scan_report_form.html", {
        "active_nav": "nurse",
        "queue_entry": queue_entry,
        "visit": visit,
        "report": existing_report,
        "scan_type_choices": ScanReport.SCAN_TYPE_CHOICES,
    })


@sonographer_role_required
def scan_report_print(request, report_id):
    hospital = get_active_hospital(request)
    qs = ScanReport.objects.select_related("visit__patient", "visit__hospital", "sonographer")
    if hospital and getattr(request.user, "role", "") != User.ROLE_SUPERADMIN:
        qs = qs.filter(visit__hospital=hospital)
    report = get_object_or_404(qs, pk=report_id)
    return render(request, "nurse/scan_report_print.html", {
        "report": report,
        "visit": report.visit,
        "patient": report.visit.patient,
        "hospital": report.visit.hospital,
    })
