"""Revenue by department, shared by the Finance, Doctor and Patient reports so
their numbers agree for the same dates.

Billed: charges added to bills in the period, by the department of each charge.
Collected: receipts taken in the period. A payment is made against a whole
visit, so each one is split across that visit's charges in proportion to their
value. A patient who pays half a bill pays half of each department's share."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

ZERO = Decimal("0")
CENT = Decimal("0.01")
UNASSIGNED = "unassigned"


def receipts(hospital):
    """The one definition of money received: paid, not waived. The Finance
    report's totals are built on this."""
    from reception.models import Payment
    return Payment.objects.filter(visit__hospital=hospital, paid_at__isnull=False, amount_paid__gt=0).exclude(status=Payment.STATUS_WAIVED)


def _charged_lines(visit):
    return [line for line in visit.visit_services.all() if line.is_charged and line.price_at_time]


def split_payment(amount, lines) -> dict:
    """Shares of one payment by department, in proportion to each charge.
    The last department takes the rounding, so the shares always add up exactly."""
    amount = Decimal(amount or 0)
    weights = defaultdict(lambda: ZERO)
    for line in lines:
        weights[line.service.category] += Decimal(line.price_at_time)
    total = sum(weights.values(), ZERO)
    if total <= 0:
        return {UNASSIGNED: amount}
    shares, given = {}, ZERO
    departments = sorted(weights)
    for index, department in enumerate(departments):
        if index == len(departments) - 1:
            share = amount - given
        else:
            share = (amount * weights[department] / total).quantize(CENT, rounding=ROUND_HALF_UP)
        shares[department] = share
        given += share
    return shares


def by_department(hospital, start, end) -> dict:
    from reception.models import Service, VisitService

    labels = dict(Service.CATEGORY_CHOICES)
    labels[UNASSIGNED] = "Not linked to a charge"
    collected = defaultdict(lambda: ZERO)
    period_receipts = receipts(hospital).filter(paid_at__date__range=(start, end)).select_related("visit").prefetch_related("visit__visit_services__service")
    for payment in period_receipts:
        for department, share in split_payment(payment.amount_paid, _charged_lines(payment.visit)).items():
            collected[department] += share

    billed = defaultdict(lambda: ZERO)
    lines = VisitService.objects.filter(visit__hospital=hospital, created_at__date__range=(start, end), price_at_time__gt=0).select_related("service")
    for line in lines:
        if line.is_charged:
            billed[line.service.category] += Decimal(line.price_at_time)

    order = [key for key, _ in Service.CATEGORY_CHOICES] + [UNASSIGNED]
    rows = [
        {"key": key, "label": labels.get(key, key.title()), "billed": billed.get(key, ZERO), "collected": collected.get(key, ZERO)}
        for key in order if billed.get(key) or collected.get(key)
    ]
    total_collected = period_receipts.aggregate(t=Sum("amount_paid"))["t"] or ZERO
    return {
        "start": start, "end": end, "rows": rows,
        "total_billed": sum(billed.values(), ZERO),
        "total_collected": total_collected,
        "pharmacy_collected": collected.get(Service.CATEGORY_PHARMACY, ZERO),
        "pharmacy_billed": billed.get(Service.CATEGORY_PHARMACY, ZERO),
    }
