from reception.models import QueueEntry, Visit


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
