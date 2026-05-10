from django.core.exceptions import PermissionDenied
from django.utils import timezone

from accounts.models import AuditLog

from reception.models import QueueEntry, Visit


RECEPTION_SOURCE_PREFIX = "Source: "


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
) -> QueueEntry:
    """Create a fresh queue entry only when no open entry of the same type exists."""
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
