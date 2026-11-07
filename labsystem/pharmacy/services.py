"""Giving out medicine, in one place. Nurses, reception and the pharmacy all
dispense through `dispense()`, so the stock check, the stock movement and the
record of what was paid at that moment are the same wherever it happens."""
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Min, Q
from django.utils import timezone

from accounts.models import Hospital

QUEUE_LOOKBACK_DAYS = 30
STANDARD_MODE_LOOKBACK_DAYS = 2


def uses_pharmacy(hospital) -> bool:
    return getattr(hospital, "dispensing_mode", "") == Hospital.DISPENSING_PHARMACY


def dispensed_at_pharmacy(prescription) -> bool:
    """In pharmacy mode the pharmacy gives out everything except the IV and
    injection doses nurses give on the ward."""
    return uses_pharmacy(prescription.visit.hospital) and not prescription.nursing_managed


@transaction.atomic
def dispense(prescription, actor, batch_id=None):
    """Hand over one prescription. Returns (prescription, dispensed_now).
    The row is locked first, so two people pressing Dispense at the same
    moment take the stock off once."""
    from admin_dashboard.models import InventoryItem, InventoryTransaction
    from doctor.models import Prescription
    from reception.models import Visit

    # of=("self",): lock only the prescription row. The billing line is a
    # nullable (outer) join, and PostgreSQL refuses FOR UPDATE on that side.
    rx = Prescription.objects.select_for_update(of=("self",)).select_related("visit", "billing_visit_service").get(pk=prescription.pk)
    if rx.dispensed:
        return rx, False
    visit = rx.visit
    if visit.status == Visit.STATUS_CANCELLED:
        raise ValidationError("Cancelled visits cannot dispense medication.")
    drug = InventoryItem.objects.select_for_update().get(pk=rx.drug_id)
    quantity = rx.total_quantity
    if drug.available_dispense_quantity < quantity:
        raise ValidationError(
            f"Not enough {drug.name} in stock: {drug.quantity_label} on hand, but this needs {rx.quantity_display}. "
            "Restock it or change the prescription."
        )
    try:
        consumption = drug.consume_stock(drug.to_stock_quantity(quantity), preferred_batch_id=batch_id or None)
    except ValueError as exc:
        raise ValidationError(str(exc))

    InventoryTransaction.objects.create(
        hospital=visit.hospital,
        item=drug,
        transaction_type=InventoryTransaction.TYPE_CONSUME,
        quantity=quantity,
        unit_cost=drug.unit_cost,
        visit=visit,
        prescription=rx,
        performed_by=actor,
        notes=f"Dispensed for prescription {rx.pk}. Batches: " + (", ".join(f"{row['batch'].batch_number} ({row['quantity']})" for row in consumption) or "none"),
        # The exact batches used, so an undo can put the stock back where it came from.
        batch_breakdown=[
            {"batch_id": row["batch"].pk, "batch_number": row["batch"].batch_number, "quantity": str(row["quantity"])}
            for row in consumption
        ],
    )

    now = timezone.now()
    rx.dispensed = True
    rx.dispensed_at = now
    rx.dispensed_by = actor
    # Medicine can go out before the bill is settled; this records how much had been paid.
    rx.paid_at_dispense = visit.total_paid
    rx.bill_at_dispense = visit.total_amount
    rx.save(update_fields=["dispensed", "dispensed_at", "dispensed_by", "paid_at_dispense", "bill_at_dispense"])
    if rx.billing_visit_service_id:
        line = rx.billing_visit_service
        line.performed = True
        line.performed_at = now
        line.save(update_fields=["performed", "performed_at"])
    return rx, True


def sold_quantity_for(drug, quantity, counted_in) -> Decimal:
    """What a straight sale takes from stock: individual units (tablets, ml)
    for tablets and capsules, whole packs for liquids and tubes."""
    quantity = Decimal(quantity)
    per_pack = Decimal(drug.units_per_pack or 0)
    by_pack = drug.category in {"syrup", "iv_fluid", "im", "reagent", "tube"}
    if by_pack:
        if counted_in == "unit" and per_pack > 0:
            quantity = quantity / per_pack
        return quantity.quantize(Decimal("1"), rounding=ROUND_CEILING)
    if counted_in == "pack" and per_pack > 0:
        quantity = quantity * per_pack
    return quantity.quantize(Decimal("1"), rounding=ROUND_CEILING)


@transaction.atomic
def add_by_quantity(visit, drug, quantity, counted_in, actor, notes=""):
    """Sell a drug by quantity ("2 packs"), not by dose. Adds it to the bill
    the same way a prescription is billed."""
    from doctor.models import Prescription
    from reception.models import Service, Visit, VisitService

    if visit.status == Visit.STATUS_CANCELLED:
        raise ValidationError("This visit was cancelled.")
    if drug.hospital_id != visit.hospital_id or not drug.is_active:
        raise ValidationError("Choose an active drug from this facility's stock.")
    if Decimal(quantity or 0) <= 0:
        raise ValidationError("Enter a quantity greater than zero.")
    if not drug.selling_price:
        raise ValidationError(f"{drug.name} has no selling price yet, so it would be given away free. Ask an admin to set its price.")
    sold = sold_quantity_for(drug, quantity, counted_in)
    rx = Prescription.objects.create(
        visit=visit, drug=drug, dosage_mg=sold, frequency_per_day=1, duration_days=1, sold_quantity=sold,
        added_from=Prescription.ADDED_FROM_PHARMACY, prescribed_by=actor, notes=notes,
    )
    service, _ = Service.objects.get_or_create(
        hospital=visit.hospital,
        name=f"Pharmacy Item: {drug.name}",
        defaults={"category": Service.CATEGORY_PHARMACY, "price": drug.selling_price or Decimal("0"), "is_active": True},
    )
    line = VisitService.objects.create(visit=visit, service=service, price_at_time=rx.total_price, notes=f"Pharmacy sale: {rx.quantity_display}")
    rx.billing_visit_service = line
    rx.save(update_fields=["billing_visit_service"])
    visit.total_amount = Decimal(visit.total_amount or 0) + rx.total_price
    update_fields = ["total_amount"]
    # A finished visit now owes more, so it goes back to reception for the new charge.
    if visit.status == Visit.STATUS_COMPLETED:
        visit.status = Visit.STATUS_READY_FOR_BILLING
        update_fields.append("status")
    visit.save(update_fields=update_fields)
    return rx


def waiting_prescriptions(hospital):
    """Medicine waiting to be handed over at the pharmacy. Only recent orders,
    so years of old undispensed history don't fill the queue."""
    from doctor.models import Prescription
    from reception.models import Visit

    if uses_pharmacy(hospital):
        since = timezone.now() - timedelta(days=QUEUE_LOOKBACK_DAYS)
        if hospital.pharmacy_mode_since and hospital.pharmacy_mode_since > since:
            since = hospital.pharmacy_mode_since
    else:
        since = timezone.now() - timedelta(days=STANDARD_MODE_LOOKBACK_DAYS)
    return Prescription.objects.filter(
        visit__hospital=hospital, dispensed=False, nursing_managed=False, prescribed_at__gte=since,
    ).exclude(visit__status=Visit.STATUS_CANCELLED)


def pharmacy_queue(hospital):
    from reception.models import Visit

    waiting = waiting_prescriptions(hospital)
    return (
        Visit.objects.filter(pk__in=waiting.values("visit_id"))
        .select_related("patient")
        .annotate(
            waiting_count=Count("prescriptions", filter=Q(prescriptions__in=waiting)),
            first_prescribed=Min("prescriptions__prescribed_at", filter=Q(prescriptions__in=waiting)),
        )
        .order_by("first_prescribed")
    )
