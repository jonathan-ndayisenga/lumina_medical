"""
Double-entry posting engine.

Every financial event (visit charge, payment receipt, expense) routes through
post_visit_service(), post_payment(), or post_expense(). Each function:
  1. Resolves the hospital's Chart of Accounts to find the right accounts.
  2. Creates a JournalEntry with the two (or more) legs.
  3. Is idempotent — if a journal entry already exists for the source record it
     reverses the old one and re-posts, keeping the ledger clean on edits.
"""

from decimal import Decimal

from django.utils import timezone

from .models import Account, JournalEntry, JournalLine


# ---------------------------------------------------------------------------
# Account resolution helpers
# ---------------------------------------------------------------------------

def _get_account(hospital, sub_type, account_type=None):
    """Return the first active account matching sub_type (and optionally account_type)."""
    qs = Account.objects.filter(hospital=hospital, sub_type=sub_type, is_active=True)
    if account_type:
        qs = qs.filter(account_type=account_type)
    return qs.first()


def _revenue_account(hospital, service_category):
    """Map a Service category string to a Revenue account."""
    from reception.models import Service

    sub_map = {
        Service.CATEGORY_CONSULTATION: "revenue_consultation",
        Service.CATEGORY_LAB:          "revenue_lab",
        Service.CATEGORY_PHARMACY:     "revenue_pharmacy",
        Service.CATEGORY_PROCEDURE:    "revenue_procedure",
        Service.CATEGORY_SCAN:         "revenue_scan",
        Service.CATEGORY_TRIAGE:       "revenue_triage",
        Service.CATEGORY_OTHER:        "revenue_other",
    }
    # Try category-specific first, fall back to generic revenue
    sub = sub_map.get(service_category, Account.SUB_REVENUE)
    acc = Account.objects.filter(
        hospital=hospital, sub_type=sub, account_type=Account.TYPE_REVENUE, is_active=True
    ).first()
    if not acc:
        acc = _get_account(hospital, Account.SUB_REVENUE, Account.TYPE_REVENUE)
    return acc


def _cash_account_for_payment(hospital, payment):
    """Return the asset account to debit when a payment is received."""
    from reception.models import Payment as Pmt

    if payment.mode == Pmt.MODE_CASH:
        return _get_account(hospital, Account.SUB_CASH, Account.TYPE_ASSET)
    if payment.mode == Pmt.MODE_MOBILE_MONEY:
        return _get_account(hospital, Account.SUB_MOBILE, Account.TYPE_ASSET)
    # Card and Insurance → bank
    return _get_account(hospital, Account.SUB_BANK, Account.TYPE_ASSET)


def _expense_account(hospital, expense):
    """Map Expense.category to an expense Account."""
    from admin_dashboard.models import Expense

    cat_map = {
        Expense.CATEGORY_SALARY:       "expense_salary",
        Expense.CATEGORY_UTILITIES:    "expense_utilities",
        Expense.CATEGORY_RENT:         "expense_rent",
        Expense.CATEGORY_CONSUMABLES:  "expense_consumables",
        Expense.CATEGORY_MEDICINE:     "expense_medicine",
        Expense.CATEGORY_MAINTENANCE:  "expense_maintenance",
        Expense.CATEGORY_LOGISTICS:    "expense_logistics",
        Expense.CATEGORY_OTHER:        Account.SUB_EXPENSE,
    }
    sub = cat_map.get(expense.category, Account.SUB_EXPENSE)
    acc = Account.objects.filter(
        hospital=hospital, sub_type=sub, account_type=Account.TYPE_EXPENSE, is_active=True
    ).first()
    if not acc:
        acc = _get_account(hospital, Account.SUB_EXPENSE, Account.TYPE_EXPENSE)
    return acc


def _cash_account_for_expense(hospital, expense):
    """Return the asset account to credit when an expense is paid out."""
    from admin_dashboard.models import Expense

    if expense.source == Expense.SOURCE_CASH_DRAWER:
        return _get_account(hospital, Account.SUB_CASH, Account.TYPE_ASSET)
    if expense.source == Expense.SOURCE_MOBILE_MONEY:
        return _get_account(hospital, Account.SUB_MOBILE, Account.TYPE_ASSET)
    return _get_account(hospital, Account.SUB_BANK, Account.TYPE_ASSET)


# ---------------------------------------------------------------------------
# Reversal helper
# ---------------------------------------------------------------------------

def _reverse_existing(hospital, source_visit_service=None, source_payment=None, source_expense=None):
    """
    If a prior journal entry exists for this source, post a reversal and mark it.
    Only touches entries not yet reversed (reversal_of__isnull=True) to prevent
    double-reversal accumulation on repeated saves of the same source record.
    Returns True if a reversal was posted.
    """
    qs = JournalEntry.objects.filter(
        hospital=hospital,
        is_reversal=False,
        reversal_of__isnull=True,  # skip entries already reversed by a later posting
    )
    if source_visit_service:
        qs = qs.filter(source_visit_service=source_visit_service)
    elif source_payment:
        qs = qs.filter(source_payment=source_payment)
    elif source_expense:
        qs = qs.filter(source_expense=source_expense)
    else:
        return False

    reversed_any = False
    for old_entry in qs:
        reversed_any = True
        reversal = JournalEntry.objects.create(
            hospital=hospital,
            date=timezone.localdate(),
            description=f"Reversal of {old_entry.reference}",
            source_type=JournalEntry.SOURCE_REVERSAL,
            is_reversal=True,
        )
        for line in old_entry.lines.all():
            JournalLine.objects.create(
                entry=reversal,
                account=line.account,
                debit=line.credit,   # swap
                credit=line.debit,
            )
        reversal.reversed_entry = old_entry
        reversal.save(update_fields=["reversed_entry"])
    return reversed_any


# ---------------------------------------------------------------------------
# Public posting functions
# ---------------------------------------------------------------------------

def post_visit_service(visit_service):
    """
    Post revenue accrual when a service is added to a visit.

    DR  Accounts Receivable         price_at_time
        CR  [Category] Revenue          price_at_time
    """
    amount = visit_service.price_at_time
    if not amount or amount <= 0:
        return

    hospital = visit_service.visit.hospital
    receivable = _get_account(hospital, Account.SUB_RECEIVABLE, Account.TYPE_ASSET)
    revenue = _revenue_account(hospital, visit_service.service.category)

    if not receivable or not revenue:
        return  # Chart of Accounts not set up yet for this hospital

    _reverse_existing(hospital, source_visit_service=visit_service)

    entry = JournalEntry.objects.create(
        hospital=hospital,
        date=(visit_service.created_at.date() if visit_service.created_at else timezone.localdate()),
        description=f"Charge: {visit_service.service.name} — {visit_service.visit.patient.name}",
        source_type=JournalEntry.SOURCE_VISIT_CHARGE,
        source_visit_service=visit_service,
    )
    JournalLine.objects.create(entry=entry, account=receivable, debit=amount, description="Accounts Receivable")
    JournalLine.objects.create(entry=entry, account=revenue, credit=amount, description=visit_service.service.name)


def post_payment(payment):
    """
    Post cash collection against Accounts Receivable.

    DR  Cash / Bank / Mobile Money   amount_paid
        CR  Accounts Receivable          amount_paid

    For waived payments, post a write-off:
    DR  Bad Debt / Waivers           amount_paid
        CR  Accounts Receivable          amount_paid
    """
    from reception.models import Payment as Pmt

    hospital = payment.visit.hospital
    amount = payment.amount_paid

    if payment.status == Pmt.STATUS_WAIVED or amount <= 0:
        _reverse_existing(hospital, source_payment=payment)
        return

    cash_acc = _cash_account_for_payment(hospital, payment)
    receivable = _get_account(hospital, Account.SUB_RECEIVABLE, Account.TYPE_ASSET)

    if not cash_acc or not receivable:
        return

    _reverse_existing(hospital, source_payment=payment)

    entry = JournalEntry.objects.create(
        hospital=hospital,
        date=(payment.paid_at.date() if payment.paid_at else timezone.localdate()),
        description=f"Receipt {payment.receipt_number} — {payment.visit.patient.name}",
        source_type=JournalEntry.SOURCE_PAYMENT,
        source_payment=payment,
    )
    JournalLine.objects.create(entry=entry, account=cash_acc, debit=amount, description=payment.get_mode_display())
    JournalLine.objects.create(entry=entry, account=receivable, credit=amount, description="Accounts Receivable")


def post_expense(expense):
    """
    Post an expense payout.

    DR  [Expense Category Account]   amount
        CR  Cash / Bank / Mobile         amount
    """
    amount = expense.amount
    if not amount or amount <= 0:
        return

    hospital = expense.hospital
    expense_acc = _expense_account(hospital, expense)
    cash_acc = _cash_account_for_expense(hospital, expense)

    if not expense_acc or not cash_acc:
        return

    _reverse_existing(hospital, source_expense=expense)

    entry = JournalEntry.objects.create(
        hospital=hospital,
        date=expense.date if hasattr(expense, "date") else timezone.localdate(),
        description=f"Expense: {expense.description}",
        source_type=JournalEntry.SOURCE_EXPENSE,
        source_expense=expense,
    )
    JournalLine.objects.create(entry=entry, account=expense_acc, debit=amount, description=expense.description)
    JournalLine.objects.create(entry=entry, account=cash_acc, credit=amount, description=expense.get_source_display())


def post_salary(salary):
    """
    Post salary payment when a Salary record is marked paid.

    DR  Staff Salaries (5001)     amount
        CR  Cash / Bank               amount

    If not yet paid, reverse any existing entry (unpaid salaries aren't in the books yet).
    """
    hospital = salary.hospital

    if not salary.paid or not salary.amount or salary.amount <= 0:
        _reverse_salary(salary)
        return

    salary_acc = Account.objects.filter(
        hospital=hospital, sub_type="expense_salary", account_type=Account.TYPE_EXPENSE, is_active=True
    ).first()
    if not salary_acc:
        salary_acc = _get_account(hospital, Account.SUB_EXPENSE, Account.TYPE_EXPENSE)

    # Salaries paid from bank by default; fall back to cash
    cash_acc = _get_account(hospital, Account.SUB_BANK, Account.TYPE_ASSET)
    if not cash_acc:
        cash_acc = _get_account(hospital, Account.SUB_CASH, Account.TYPE_ASSET)

    if not salary_acc or not cash_acc:
        return

    _reverse_salary(salary)

    employee_name = salary.employee.get_full_name() or salary.employee.username
    entry = JournalEntry.objects.create(
        hospital=hospital,
        date=salary.paid_at or timezone.localdate(),
        description=f"Salary: {employee_name} — {salary.month.strftime('%B %Y')}",
        source_type=JournalEntry.SOURCE_EXPENSE,
    )
    JournalLine.objects.create(entry=entry, account=salary_acc, debit=salary.amount, description=f"Salary — {employee_name}")
    JournalLine.objects.create(entry=entry, account=cash_acc, credit=salary.amount, description="Salary payment")


def _reverse_salary(salary):
    """Reverse all non-reversal journal entries whose description matches this salary record."""
    employee_name = salary.employee.get_full_name() or salary.employee.username
    month_str = salary.month.strftime("%B %Y")
    description = f"Salary: {employee_name} — {month_str}"
    old_entries = JournalEntry.objects.filter(
        hospital=salary.hospital,
        description=description,
        is_reversal=False,
        source_type=JournalEntry.SOURCE_EXPENSE,
    )
    for old_entry in old_entries:
        reversal = JournalEntry.objects.create(
            hospital=salary.hospital,
            date=timezone.localdate(),
            description=f"Reversal of {old_entry.reference}",
            source_type=JournalEntry.SOURCE_REVERSAL,
            is_reversal=True,
        )
        for line in old_entry.lines.all():
            JournalLine.objects.create(
                entry=reversal,
                account=line.account,
                debit=line.credit,
                credit=line.debit,
            )
        reversal.reversed_entry = old_entry
        reversal.save(update_fields=["reversed_entry"])
