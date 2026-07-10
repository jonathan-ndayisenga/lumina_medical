"""
Auto-posting signals. Connected in FinanceConfig.ready().

Each signal fires after a source record is saved/deleted and delegates
to the posting engine. Wrapped in try/except so a ledger error never
breaks the clinical workflow.
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _safe_post(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        logger.exception("Finance auto-posting error in %s: %s", fn.__name__, exc)


# ── VisitService ────────────────────────────────────────────────────────────

@receiver(post_save, sender="reception.VisitService")
def on_visit_service_save(sender, instance, **kwargs):
    from .posting import post_visit_service
    _safe_post(post_visit_service, instance)


@receiver(post_delete, sender="reception.VisitService")
def on_visit_service_delete(sender, instance, **kwargs):
    from .models import JournalEntry
    from .posting import _reverse_existing
    try:
        _reverse_existing(instance.visit.hospital, source_visit_service=instance)
    except Exception as exc:
        logger.exception("Finance reversal error on VisitService delete: %s", exc)


# ── Payment ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender="reception.Payment")
def on_payment_save(sender, instance, **kwargs):
    from .posting import post_payment
    _safe_post(post_payment, instance)


@receiver(post_delete, sender="reception.Payment")
def on_payment_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    try:
        _reverse_existing(instance.visit.hospital, source_payment=instance)
    except Exception as exc:
        logger.exception("Finance reversal error on Payment delete: %s", exc)


# ── Expense ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender="admin_dashboard.Expense")
def on_expense_save(sender, instance, **kwargs):
    from .posting import post_expense
    _safe_post(post_expense, instance)


@receiver(post_delete, sender="admin_dashboard.Expense")
def on_expense_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    try:
        _reverse_existing(instance.hospital, source_expense=instance)
    except Exception as exc:
        logger.exception("Finance reversal error on Expense delete: %s", exc)


# ── Salary ───────────────────────────────────────────────────────────────────

@receiver(post_save, sender="admin_dashboard.Salary")
def on_salary_save(sender, instance, **kwargs):
    from .posting import post_salary
    _safe_post(post_salary, instance)


@receiver(post_delete, sender="admin_dashboard.Salary")
def on_salary_delete(sender, instance, **kwargs):
    from .posting import _reverse_salary
    try:
        _reverse_salary(instance)
    except Exception as exc:
        logger.exception("Finance reversal error on Salary delete: %s", exc)
