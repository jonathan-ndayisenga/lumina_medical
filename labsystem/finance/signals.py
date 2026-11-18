"""
Auto-posting signals. Connected in FinanceConfig.ready().

Each signal fires after a source record is saved/deleted and delegates to the
posting engine. Posting runs in its own savepoint so a ledger error never
breaks the clinical workflow: without the savepoint, a failed statement would
poison the caller's transaction (on Postgres every later query then fails with
TransactionManagementError).

A failure is no longer just logged: it is stored as a PostingFailure, shown on
the finance dashboard and retried later (see posting.retry_posting_failures).
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


def _hospital_of(instance):
    hospital_id = getattr(instance, "hospital_id", None)
    if hospital_id is None and getattr(instance, "visit_id", None):
        hospital_id = instance.visit.hospital_id
    return hospital_id


def _record_failure(instance, action, exc):
    from .models import PostingFailure

    label = instance._meta.label_lower
    try:
        with transaction.atomic():
            existing = PostingFailure.objects.filter(
                source_model=label, source_id=instance.pk, action=action, resolved_at__isnull=True,
            ).first()
            if existing:
                existing.error = f"{type(exc).__name__}: {exc}"
                existing.attempts += 1
                existing.save(update_fields=["error", "attempts", "last_attempt_at"])
            else:
                PostingFailure.objects.create(
                    hospital_id=_hospital_of(instance), source_model=label, source_id=instance.pk,
                    action=action, error=f"{type(exc).__name__}: {exc}",
                )
    except Exception:
        logger.exception("Could not record ledger posting failure for %s#%s", label, instance.pk)


def _mark_resolved(instance):
    from .models import PostingFailure

    try:
        with transaction.atomic():
            PostingFailure.objects.filter(
                source_model=instance._meta.label_lower, source_id=instance.pk, resolved_at__isnull=True,
            ).update(resolved_at=timezone.now())
    except Exception:
        logger.exception("Could not clear ledger posting failures for %s#%s", instance._meta.label_lower, instance.pk)


def _safe_post(fn, instance):
    try:
        with transaction.atomic():
            fn(instance)
    except Exception as exc:
        logger.exception("Finance auto-posting error in %s: %s", fn.__name__, exc)
        _record_failure(instance, "post", exc)
    else:
        _mark_resolved(instance)


def _safe_reverse(fn, instance, **kwargs):
    try:
        with transaction.atomic():
            fn(**kwargs)
    except Exception as exc:
        logger.exception("Finance reversal error on %s delete: %s", instance._meta.label_lower, exc)
        _record_failure(instance, "reverse", exc)


# ── VisitService ────────────────────────────────────────────────────────────

@receiver(post_save, sender="reception.VisitService")
def on_visit_service_save(sender, instance, **kwargs):
    from .posting import post_visit_service
    _safe_post(post_visit_service, instance)


@receiver(pre_delete, sender="reception.VisitService")
def on_visit_service_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    _safe_reverse(_reverse_existing, instance, hospital=instance.visit.hospital, source_visit_service=instance)


# ── Payment ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender="reception.Payment")
def on_payment_save(sender, instance, **kwargs):
    from .posting import post_payment
    _safe_post(post_payment, instance)


@receiver(pre_delete, sender="reception.Payment")
def on_payment_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    _safe_reverse(_reverse_existing, instance, hospital=instance.visit.hospital, source_payment=instance)


# ── Expense ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender="admin_dashboard.Expense")
def on_expense_save(sender, instance, **kwargs):
    from .posting import post_expense
    _safe_post(post_expense, instance)


@receiver(pre_delete, sender="admin_dashboard.Expense")
def on_expense_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    _safe_reverse(_reverse_existing, instance, hospital=instance.hospital, source_expense=instance)


# ── Salary ───────────────────────────────────────────────────────────────────

@receiver(post_save, sender="admin_dashboard.Salary")
def on_salary_save(sender, instance, **kwargs):
    from .posting import post_salary
    _safe_post(post_salary, instance)


@receiver(pre_delete, sender="admin_dashboard.Salary")
def on_salary_delete(sender, instance, **kwargs):
    from .posting import _reverse_salary
    _safe_reverse(_reverse_salary, instance, salary=instance)
