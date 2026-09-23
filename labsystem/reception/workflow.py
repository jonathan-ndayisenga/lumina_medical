import calendar

from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.utils import timezone

from accounts.models import AuditLog

from reception.models import QueueEntry, Service, Visit


def add_months(base_date, months):
    """base_date + N calendar months, clamped to the shorter target month's
    last day (e.g. 31 Jan + 1 -> 28/29 Feb)."""
    month_index = base_date.month - 1 + months
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return base_date.replace(year=year, month=month, day=day)


def package_expiry_for(service, purchase_date):
    """purchase_date + service.validity_months, or None if the package (set
    by the admin on its catalog entry) has no configured expiry."""
    if not service.validity_months:
        return None
    return add_months(purchase_date, service.validity_months)


def queue_counts_for_hospital(hospital) -> dict:
    """Pending (unprocessed) entry counts per operational queue, keyed to
    match the NAV_SECTIONS tile keys in accounts/views.py — used for the
    red count badges on the home tiles and sidebar queue links."""
    empty = {"reception": 0, "doctor": 0, "nurse": 0, "sonographer": 0, "lab": 0, "phlebotomy": 0}
    if not hospital:
        return empty
    rows = (
        QueueEntry.objects.filter(hospital=hospital, processed=False)
        .values("queue_type")
        .annotate(n=Count("id"))
    )
    by_type = {row["queue_type"]: row["n"] for row in rows}
    return {
        "reception": by_type.get(QueueEntry.TYPE_RECEPTION, 0),
        "doctor": by_type.get(QueueEntry.TYPE_DOCTOR, 0),
        "nurse": by_type.get(QueueEntry.TYPE_NURSE, 0),
        "sonographer": by_type.get(QueueEntry.TYPE_SONOGRAPHER, 0),
        "lab": by_type.get(QueueEntry.TYPE_LAB_RECEPTION, 0) + by_type.get(QueueEntry.TYPE_LAB_DOCTOR, 0),
        "phlebotomy": by_type.get(QueueEntry.TYPE_PHLEBOTOMY, 0),
    }


RECEPTION_SOURCE_PREFIX = "Source: "

# Which platform Module must be enabled for a hospital to receive this queue type.
# Reception is core and always enabled, so it's intentionally left unmapped (no gate needed).
QUEUE_TYPE_TO_MODULE_CODE = {
    QueueEntry.TYPE_DOCTOR: "doctor",
    QueueEntry.TYPE_NURSE: "nurse",
    QueueEntry.TYPE_LAB_RECEPTION: "lab",
    QueueEntry.TYPE_LAB_DOCTOR: "lab",
    QueueEntry.TYPE_SONOGRAPHER: "sonographer",
    QueueEntry.TYPE_PHLEBOTOMY: "lab",
}


def require_module_for_queue_type(*, hospital, queue_type: str) -> None:
    """Block routing a patient to a module the hospital hasn't subscribed to."""
    module_code = QUEUE_TYPE_TO_MODULE_CODE.get(queue_type)
    if not module_code:
        return
    if not hospital or not hospital.has_module(module_code):
        raise PermissionDenied(
            f"This hospital does not have the '{module_code}' module enabled, "
            "so this patient cannot be routed there."
        )


def active_package_visit_service(visit, service):
    """The visit's own active Package-category VisitService, if any, whose
    package_services includes `service` -- or None. Falls back to
    visit.package_source (the reused purchase line on an earlier visit, for
    a Visit.TYPE_PACKAGE visit) when nothing on this visit itself covers it.
    Callers use this to decide whether a new service line being added should
    be billed normally or marked covered_by_package (see
    VisitService.billing_label). Checked against the exact service, not its
    category -- a package only ever covers the specific services it was
    built with (e.g. Antenatal -> Consultation, CBC, Urinalysis), not "any
    lab test the patient wants"."""
    for package_line in visit.visit_services.filter(service__category=Service.CATEGORY_PACKAGE).select_related("service"):
        if package_line.service.package_services.filter(pk=service.pk).exists():
            return package_line
    source = visit.package_source
    if source and source.service.package_services.filter(pk=service.pk).exists():
        return source
    return None


def package_services_available_on_visit(visit):
    """Every specific service included in any active package on this visit
    that hasn't already been added as a line on it -- grouped by nothing in
    particular, callers group by category/queue as needed. Also includes the
    reused purchase's services (visit.package_source) for a Visit.TYPE_PACKAGE
    visit. Used to build the "send to..." action list on the visit detail
    page."""
    already_added_ids = set(visit.visit_services.values_list("service_id", flat=True))
    available = []
    seen_ids = set()
    package_lines = list(visit.visit_services.filter(service__category=Service.CATEGORY_PACKAGE).select_related("service"))
    if visit.package_source_id and visit.package_source not in package_lines:
        package_lines.append(visit.package_source)
    for package_line in package_lines:
        for included_service in package_line.service.package_services.all():
            if included_service.pk in already_added_ids or included_service.pk in seen_ids:
                continue
            seen_ids.add(included_service.pk)
            available.append((included_service, package_line))
    return available


def user_can_admin_override(user) -> bool:
    return bool(getattr(user, "can_access_hospital_admin", False))


def require_admin_override(user) -> None:
    if not user_can_admin_override(user):
        raise PermissionDenied("Only a hospital administrator can perform this action.")


def record_admin_override(*, actor, hospital, action: str, model_name: str, object_id, details=None) -> None:
    AuditLog.objects.create(
        user=actor,
        hospital=hospital,
        action=action,
        model_name=model_name,
        object_id=str(object_id),
        details=details or {},
    )


def terminate_visit_workflow(*, visit: Visit, actor, reason: str) -> int:
    if visit.status == Visit.STATUS_COMPLETED:
        raise PermissionDenied("Completed visits cannot be terminated.")

    previous_status = visit.status
    processed_at = timezone.now()
    open_queue_entries = visit.queue_entries.filter(processed=False)
    closed_queue_count = open_queue_entries.count()
    open_queue_entries.update(processed=True, processed_at=processed_at)

    note_line = f"[Admin terminated {processed_at:%Y-%m-%d %H:%M}] {reason.strip()}"
    visit.status = Visit.STATUS_CANCELLED
    visit.notes = f"{visit.notes}\n{note_line}".strip() if visit.notes else note_line
    visit.save(update_fields=["status", "notes"])

    record_admin_override(
        actor=actor,
        hospital=visit.hospital,
        action="terminate_visit",
        model_name="Visit",
        object_id=visit.pk,
        details={
            "patient_id": visit.patient_id,
            "patient_name": visit.patient.name,
            "reason": reason,
            "closed_queue_count": closed_queue_count,
            "previous_status": previous_status,
        },
    )
    return closed_queue_count


def sync_visit_status(visit: Visit) -> Visit:
    """Keep visit status aligned with queue progress without overriding cancellations."""
    if visit.status in {Visit.STATUS_CANCELLED, Visit.STATUS_COMPLETED}:
        return visit

    has_open_queue = visit.queue_entries.filter(processed=False).exists()
    next_status = Visit.STATUS_IN_PROGRESS if has_open_queue else Visit.STATUS_READY_FOR_BILLING

    if visit.status != next_status:
        visit.status = next_status
        visit.save(update_fields=["status"])

    return visit


def ensure_pending_queue_entry(
    *,
    visit: Visit,
    hospital,
    queue_type: str,
    notes: str = "",
    reason: str = "",
    requested_by=None,
):
    """Create a fresh queue entry only when no open entry of the same type exists."""
    if visit.status in {Visit.STATUS_COMPLETED, Visit.STATUS_CANCELLED}:
        return None
    require_module_for_queue_type(hospital=hospital, queue_type=queue_type)
    existing_pending = visit.queue_entries.filter(queue_type=queue_type, processed=False).order_by("-created_at").first()
    if existing_pending:
        changed_fields = []
        if reason and existing_pending.reason != reason:
            existing_pending.reason = reason
            changed_fields.append("reason")
        if notes and existing_pending.notes != notes:
            existing_pending.notes = notes
            changed_fields.append("notes")
        if requested_by and existing_pending.requested_by_id != getattr(requested_by, "pk", None):
            existing_pending.requested_by = requested_by
            changed_fields.append("requested_by")
        if changed_fields:
            existing_pending.save(update_fields=changed_fields)
        return existing_pending

    return QueueEntry.objects.create(
        hospital=hospital,
        visit=visit,
        queue_type=queue_type,
        notes=notes,
        reason=reason,
        requested_by=requested_by,
    )


def build_reception_queue_notes(source: str, notes: str = "") -> str:
    prefix = f"{RECEPTION_SOURCE_PREFIX}{source}"
    return f"{prefix}\n{notes}".strip() if notes else prefix


def build_reception_queue_reason(source: str, detail: str = "") -> str:
    source_label = (source or "Care").strip()
    detail = (detail or "").strip()
    return f"Returned from {source_label}: {detail}" if detail else f"Returned from {source_label}"


def reception_source_from_entry(entry: QueueEntry) -> str:
    lines = (entry.notes or "").splitlines()
    if lines:
        first_line = lines[0].strip()
        if first_line.startswith(RECEPTION_SOURCE_PREFIX):
            return first_line.replace(RECEPTION_SOURCE_PREFIX, "", 1).strip() or "Reception"
    
    reason = (entry.reason or "").strip()
    if reason.lower().startswith("returned from "):
        source = reason[14:].split(":", 1)[0].strip()
        return source or "Reception"
    return "Reception"


def send_to_reception_queue(
    *,
    visit: Visit,
    hospital,
    source: str,
    detail: str = "",
    notes: str = "",
    requested_by=None,
) -> QueueEntry:
    return ensure_pending_queue_entry(
        visit=visit,
        hospital=hospital,
        queue_type=QueueEntry.TYPE_RECEPTION,
        reason=build_reception_queue_reason(source, detail),
        notes=build_reception_queue_notes(source, notes),
        requested_by=requested_by,
    )


def mark_queue_entries_processed(*, visit: Visit, queue_type: str) -> int:
    return visit.queue_entries.filter(queue_type=queue_type, processed=False).update(
        processed=True,
        processed_at=timezone.now(),
    )


# Queues that must be closed before the key queue type may open.
_COMPETING_TYPES: dict = {
    QueueEntry.TYPE_NURSE: [QueueEntry.TYPE_DOCTOR],
    QueueEntry.TYPE_DOCTOR: [QueueEntry.TYPE_NURSE],
    QueueEntry.TYPE_RECEPTION: [QueueEntry.TYPE_NURSE, QueueEntry.TYPE_DOCTOR],
}


def close_competing_queue_entries(visit: Visit, new_type: str) -> int:
    """Close any open queue entries that conflict with the queue type about to open.

    E.g. opening TYPE_NURSE closes TYPE_DOCTOR; opening TYPE_RECEPTION closes both.
    Returns the number of entries closed.
    """
    types_to_close = _COMPETING_TYPES.get(new_type, [])
    if not types_to_close:
        return 0
    return visit.queue_entries.filter(
        queue_type__in=types_to_close, processed=False
    ).update(processed=True, processed_at=timezone.now())
