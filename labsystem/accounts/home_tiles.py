"""Home screen status board: which tiles exist, what each one links to,
and the one live number each shows. Counts are cached per hospital for
30 s so every user of a hospital shares one query per tile."""
from decimal import Decimal

from django.core.cache import cache
from django.db.models import F, Min, Sum
from django.utils import timezone

CACHE_SECONDS = 30

# Order is the grid order. `check` is the User access property; each
# action is (label, url name, optional extra user check).
TILES = {
    "reception": {
        "label": "Reception", "check": "can_access_reception", "wide": True, "live": True,
        "icon": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8m10-3v6m3-3h-6",
        "actions": [("Register patient", "patient_create"), ("Open queue", "reception_queue"), ("Find patient", "patient_list")],
    },
    "doctor": {
        "label": "Doctor", "check": "can_access_doctor", "wide": True, "live": True,
        "icon": "M5 3v6a5 5 0 0 0 10 0V3M10 14v2a5 5 0 0 0 10 0v-3m0 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4",
        "actions": [("Open queue", "doctor_queue"), ("Today's consultations", "report_consultations", "is_hospital_admin")],
    },
    "lab": {
        "label": "Laboratory", "check": "can_access_lab", "live": True,
        "icon": "M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3M7 15h10",
        "actions": [("Enter results", "queue"), ("Reports", "report_list")],
    },
    "nurse": {
        "label": "Nursing", "check": "can_access_nurse", "live": True,
        "icon": "M3 12h4l3-8 4 16 3-8h4",
        "actions": [("Triage queue", "nurse_queue"), ("IV care", "nursing_admissions")],
    },
    "sonographer": {
        "label": "Sonography", "check": "can_access_sonographer", "live": True,
        "icon": "M4 8V6a2 2 0 0 1 2-2h2m8 0h2a2 2 0 0 1 2 2v2m0 8v2a2 2 0 0 1-2 2h-2m-8 0H6a2 2 0 0 1-2-2v-2m8-1a3 3 0 1 0 0-6 3 3 0 0 0 0 6",
        "actions": [("Scan queue", "scan_queue")],
    },
    "inventory": {
        "label": "Inventory", "check": "can_access_inventory", "live": True,
        "icon": "M21 8 12 3 3 8m18 0v8l-9 5-9-5V8m18 0-9 5m-9-5 9 5m0 0v8",
        "actions": [("Restock list", "inventory_insights"), ("Stock levels", "manage_inventory")],
    },
    "finance": {
        "label": "Finance", "check": "can_access_finance", "wide": True, "live": True,
        "icon": "M3 6h18v12H3zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6M6 9v.01M18 15v.01",
        "actions": [("Receipts", "receipts_list"), ("Expenses", "manage_expenses"), ("Ledger", "finance_journal")],
    },
    "hospital_admin": {
        "label": "Hospital admin", "check": "can_access_hospital_admin", "wide": True, "live": False,
        "icon": "M4 21V8l8-5 8 5v13M9 21v-6h6v6M12 10v.01",
        "actions": [("Staff", "manage_users"), ("Services", "manage_services"), ("Reports", "hospital_reports")],
    },
    "home_care": {
        "label": "Home Care", "check": "can_access_home_care", "live": False,
        "icon": "M3 12l9-9 9 9M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10",
        "actions": [("Open Home Care", "homecare_dashboard")],
    },
}


def tile_numbers(key, hospital):
    return cache.get_or_set(
        f"home_tile:{hospital.pk}:{key}", lambda: COMPUTE[key](hospital), CACHE_SECONDS,
    )


def _queue(hospital, queue_type):
    from reception.models import QueueEntry
    return QueueEntry.objects.filter(hospital=hospital, queue_type=queue_type, processed=False)


def _minutes_since(moment):
    return int((timezone.now() - moment).total_seconds() // 60) if moment else 0


def _duration(minutes):
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _compact_ugx(amount):
    a = float(amount or 0)
    if a >= 999_500:
        text = f"{a / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    elif a >= 1_000:
        text = f"{round(a / 1000):,}K"
    else:
        text = f"{a:,.0f}"
    return f"UGX {text}"


def _simple(value, label, warn=False):
    aria = f"{value} {label}" + (", needs attention" if warn else "")
    return {"value": value, "label": label, "warn": warn, "aria": aria}


def _reception(hospital):
    from reception.models import QueueEntry
    return _simple(_queue(hospital, QueueEntry.TYPE_RECEPTION).count(), "waiting at the desk")


def _doctor(hospital):
    from reception.models import QueueEntry
    qs = _queue(hospital, QueueEntry.TYPE_DOCTOR)
    count = qs.count()
    result = _simple(count, "patients waiting")
    if count:
        waited = _minutes_since(qs.aggregate(oldest=Min("created_at"))["oldest"])
        warn = waited > hospital.doctor_wait_warn_minutes
        result["secondary"] = {"value": _duration(waited), "label": "longest wait", "warn": warn}
        result["aria"] += f", longest wait {_duration(waited)}" + (", needs attention" if warn else "")
    return result


def _lab(hospital):
    from lab.models import LabOrder, OrderStage
    qs = LabOrder.objects.filter(
        hospital=hospital,
        stage__in=[OrderStage.PENDING, OrderStage.SAMPLE_COLLECTED, OrderStage.IN_PROGRESS],
    )
    count = qs.count()
    oldest = qs.aggregate(oldest=Min("created_at"))["oldest"]
    return _simple(count, "results pending", warn=bool(count) and _minutes_since(oldest) > hospital.lab_pending_warn_minutes)


def _nurse(hospital):
    from reception.models import QueueEntry
    return _simple(_queue(hospital, QueueEntry.TYPE_NURSE).count(), "awaiting triage")


def _sonographer(hospital):
    from reception.models import QueueEntry
    return _simple(_queue(hospital, QueueEntry.TYPE_SONOGRAPHER).count(), "scans queued")


def _inventory(hospital):
    from admin_dashboard.models import InventoryItem
    count = InventoryItem.objects.filter(
        hospital=hospital, is_active=True, current_quantity__lte=F("reorder_level"),
    ).count()
    return _simple(count, "items below reorder level", warn=count > 0)


def _finance(hospital):
    from reception.models import Payment
    total = (
        Payment.objects.filter(visit__hospital=hospital, paid_at__date=timezone.localdate())
        .exclude(status=Payment.STATUS_WAIVED)
        .aggregate(total=Sum("amount_paid"))["total"]
    ) or Decimal("0")
    return _simple(_compact_ugx(total), "collected today")


COMPUTE = {
    "reception": _reception,
    "doctor": _doctor,
    "lab": _lab,
    "nurse": _nurse,
    "sonographer": _sonographer,
    "inventory": _inventory,
    "finance": _finance,
}
