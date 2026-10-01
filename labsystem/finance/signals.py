"""
Auto-posting signals. Connected in FinanceConfig.ready().

Each signal fires after a source record is saved/deleted and delegates
to the posting engine. Each one runs in its own savepoint and is wrapped in try/except, so a ledger
error never breaks the clinical workflow: without the savepoint, a failed
statement would poison the caller's transaction (on Postgres every later query
then fails with TransactionManagementError) even though the error was caught.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _safe_post(fn, *args, **kwargs):
    try:
        with transaction.atomic():
            fn(*args, **kwargs)
    except Exception as exc:
        logger.exception("Finance auto-posting error in %s: %s", fn.__name__, exc)


# ── VisitService ────────────────────────────────────────────────────────────

@receiver(post_save, sender="reception.VisitService")
def on_visit_service_save(sender, instance, **kwargs):
    from .posting import post_visit_service
    _safe_post(post_visit_service, instance)


@receiver(pre_delete, sender="reception.VisitService")
def on_visit_service_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    try:
        with transaction.atomic():
            _reverse_existing(instance.visit.hospital, source_visit_service=instance)
    except Exception as exc:
        logger.exception("Finance reversal error on VisitService delete: %s", exc)


# ── Payment ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender="reception.Payment")
def on_payment_save(sender, instance, **kwargs):
    from .posting import post_payment
    _safe_post(post_payment, instance)


@receiver(pre_delete, sender="reception.Payment")
def on_payment_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    try:
        with transaction.atomic():
            _reverse_existing(instance.visit.hospital, source_payment=instance)
    except Exception as exc:
        logger.exception("Finance reversal error on Payment delete: %s", exc)


# ── Expense ─────────────────────────────────────────────────────────────────

@receiver(post_save, sender="admin_dashboard.Expense")
def on_expense_save(sender, instance, **kwargs):
    from .posting import post_expense
    _safe_post(post_expense, instance)


@receiver(pre_delete, sender="admin_dashboard.Expense")
def on_expense_delete(sender, instance, **kwargs):
    from .posting import _reverse_existing
    try:
        with transaction.atomic():
            _reverse_existing(instance.hospital, source_expense=instance)
    except Exception as exc:
        logger.exception("Finance reversal error on Expense delete: %s", exc)


# ── Salary ───────────────────────────────────────────────────────────────────

@receiver(post_save, sender="admin_dashboard.Salary")
def on_salary_save(sender, instance, **kwargs):
    from .posting import post_salary
    _safe_post(post_salary, instance)


@receiver(pre_delete, sender="admin_dashboard.Salary")
def on_salary_delete(sender, instance, **kwargs):
    from .posting import _reverse_salary
    try:
        with transaction.atomic():
            _reverse_salary(instance)
    except Exception as exc:
        logger.exception("Finance reversal error on Salary delete: %s", exc)
