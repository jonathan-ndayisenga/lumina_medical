"""
The one place the newer lab engine reaches back OUT into live reception/doctor
call sites, rather than just being reached into via LabOrder's FK. Companion
piece to LabOrder.visit_service using on_delete=PROTECT: a VisitService can
legitimately be deleted today (doctor removes a just-added lab service, an
admin voids a visit) — PROTECT means that now fails outright if a LabOrder is
attached, unless callers check first.
"""

from django.core.exceptions import ValidationError

from .models import OrderStage


def _blocking_message(visit_service, order) -> str:
    return (
        f"{visit_service.service.name} ({order.test.name}) already has lab work in progress "
        f"({order.get_stage_display()}) and cannot be removed. "
        "Contact the lab to void the order first."
    )


def release_visit_service_for_lab(visit_service):
    """
    Call immediately before deleting a single VisitService. A bundled
    service can carry more than one LabOrder (one per linked LabTest) — any
    order that never progressed past PENDING is safe to retract silently
    (nothing has happened yet) so PROTECT doesn't block a legitimate
    removal. If ANY of them has moved on (sample collected, results
    entered, ...), raise instead of quietly discarding real lab work —
    checked before deleting anything, so a block never leaves a partial
    delete behind.
    """
    orders = list(visit_service.lab_orders_next.all())
    for order in orders:
        if order.stage != OrderStage.PENDING:
            raise ValidationError(_blocking_message(visit_service, order))
    for order in orders:
        order.delete()


def release_visit_services_for_lab(visit_services):
    """
    Same contract, for a bulk removal (rebuilding a visit's service list,
    voiding a visit). Two passes on purpose: check every service's every
    order first and raise before touching anything if any one of them
    blocks, only then delete the PENDING orders that are actually safe to
    retract. Doing the check-and-delete in one pass would let earlier
    deletions happen even though a later item aborts the whole operation.
    """
    orders_to_delete = []
    for visit_service in visit_services:
        for order in visit_service.lab_orders_next.all():
            if order.stage != OrderStage.PENDING:
                raise ValidationError(_blocking_message(visit_service, order))
            orders_to_delete.append(order)
    for order in orders_to_delete:
        order.delete()
