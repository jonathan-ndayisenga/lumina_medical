from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Hospital

from . import services


def pharmacy_access_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.can_access_inventory or getattr(request, "hospital", None) is None:
            return HttpResponseForbidden("This facility doesn't have the Pharmacy module, or your account doesn't have access to it.")
        return view_func(request, *args, **kwargs)
    wrapped.__name__ = view_func.__name__
    return wrapped


def _is_admin(user) -> bool:
    return bool(getattr(user, "is_superadmin", False) or getattr(user, "is_hospital_admin", False))


def _visit_or_404(request, visit_id):
    from reception.models import Visit
    return get_object_or_404(Visit.objects.select_related("patient", "hospital"), pk=visit_id, hospital=request.hospital)


@pharmacy_access_required
def queue(request):
    hospital = request.hospital
    return render(request, "pharmacy/queue.html", {
        "active_nav": "pharmacy_queue",
        "visits": services.pharmacy_queue(hospital),
        "pharmacy_mode": services.uses_pharmacy(hospital),
        "can_change_mode": _is_admin(request.user),
        "modes": Hospital.DISPENSING_CHOICES,
    })


@require_POST
@pharmacy_access_required
def set_mode(request):
    if not _is_admin(request.user):
        return HttpResponseForbidden("Only admins can change how this facility gives out medicine.")
    hospital = request.hospital
    mode = request.POST.get("mode")
    if mode not in dict(Hospital.DISPENSING_CHOICES):
        messages.error(request, "Choose one of the options.")
        return redirect("pharmacy_queue")
    if mode != hospital.dispensing_mode:
        hospital.dispensing_mode = mode
        hospital.pharmacy_mode_since = timezone.now() if mode == Hospital.DISPENSING_PHARMACY else None
        hospital.save(update_fields=["dispensing_mode", "pharmacy_mode_since"])
        if mode == Hospital.DISPENSING_PHARMACY:
            messages.success(request, "The pharmacy now gives out medicine. Nurses still give IV and injection doses on the ward.")
        else:
            messages.success(request, "Nurses and reception give out medicine again, as before.")
    return redirect("pharmacy_queue")


@pharmacy_access_required
def visit(request, visit_id):
    from admin_dashboard.models import InventoryItem
    from doctor.models import Prescription

    current = _visit_or_404(request, visit_id)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "dispense":
                rx = get_object_or_404(Prescription, pk=request.POST.get("prescription"), visit=current)
                rx, done = services.dispense(rx, request.user, request.POST.get("batch_id") or None)
                if done:
                    messages.success(request, f"Dispensed {rx.quantity_display} of {rx.drug.name}.")
                else:
                    messages.info(request, f"{rx.drug.name} was already dispensed.")
            elif action == "add":
                drug = get_object_or_404(InventoryItem, pk=request.POST.get("drug"), hospital=current.hospital)
                try:
                    quantity = Decimal(request.POST.get("quantity") or "0")
                except InvalidOperation:
                    raise ValidationError("Enter a number for the quantity.")
                rx = services.add_by_quantity(current, drug, quantity, request.POST.get("counted_in", "unit"), request.user, request.POST.get("notes", "").strip())
                messages.success(request, f"Added {rx.quantity_display} of {drug.name} for UGX {rx.total_price:,.0f}.")
            elif action == "remove":
                from doctor.views import remove_prescription_workflow
                rx = get_object_or_404(Prescription, pk=request.POST.get("prescription"), visit=current)
                if rx.dispensed:
                    raise ValidationError("This was already handed over. Ask an admin to undo it from the bill.")
                name = rx.drug.name
                remove_prescription_workflow(prescription=rx, actor=request.user)
                messages.success(request, f"{name} removed and taken off the bill.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("pharmacy_visit", visit_id=current.pk)

    prescriptions = current.prescriptions.select_related("drug", "prescribed_by", "dispensed_by").order_by("dispensed", "prescribed_at")
    return render(request, "pharmacy/visit.html", {
        "active_nav": "pharmacy_queue",
        "visit": current,
        "prescriptions": prescriptions,
        "drugs": InventoryItem.objects.filter(hospital=current.hospital, is_active=True).order_by("name"),
        "balance": max(Decimal(current.total_amount or 0) - Decimal(current.total_paid or 0), Decimal("0")),
    })


@pharmacy_access_required
def walk_in(request):
    from reception.forms import QuickDispenseStartForm
    from reception.models import Visit
    from reception.views import get_or_create_walk_in_patient

    hospital = request.hospital
    form = QuickDispenseStartForm(request.POST or None, hospital=hospital)
    if request.method == "POST" and form.is_valid():
        patient = (
            form.cleaned_data["patient"]
            if form.cleaned_data["client_type"] == QuickDispenseStartForm.CLIENT_EXISTING
            else get_or_create_walk_in_patient(hospital)
        )
        sale = Visit.objects.create(
            patient=patient, hospital=hospital, status=Visit.STATUS_READY_FOR_BILLING, total_amount=Decimal("0.00"),
            created_by=request.user, notes=form.cleaned_data.get("notes") or "Walk-in sale started at the pharmacy.",
        )
        messages.success(request, f"Sale started for {patient.name}. Add the medicine, hand it over, then reception takes payment.")
        return redirect("pharmacy_visit", visit_id=sale.pk)
    return render(request, "pharmacy/walk_in.html", {"active_nav": "pharmacy_walk_in", "form": form})
