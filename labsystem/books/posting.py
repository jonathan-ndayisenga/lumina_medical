"""The single place that knows which accounts a document type touches."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .ledger_models import Account, JournalEntry


@transaction.atomic
def post_invoice(invoice, user=None) -> JournalEntry:
    entry = JournalEntry.objects.create(
        date=invoice.issue_date,
        memo=f"Invoice {invoice.number} - {invoice.client.name}",
        reference=invoice.number,
        source_type=JournalEntry.SOURCE_INVOICE,
        source_id=invoice.pk,
    )
    ar = Account.by_role(Account.ROLE_AR)
    entry.add(ar, debit=invoice.total, narration=f"Invoice {invoice.number}")
    for line in invoice.lines.all():
        entry.add(
            line.resolved_revenue_account(),
            credit=line.amount,
            narration=line.description,
        )
    if invoice.tax_amount:
        vat = Account.by_role(Account.ROLE_VAT)
        entry.add(vat, credit=invoice.tax_amount, narration="VAT payable")
    entry.post(user)
    invoice.journal_entry = entry
    invoice.save(update_fields=["journal_entry"])
    return entry


@transaction.atomic
def post_payment(payment, user=None) -> JournalEntry:
    entry = JournalEntry.objects.create(
        date=payment.date,
        memo=f"Payment {payment.receipt_number} - {payment.client.name}",
        reference=payment.receipt_number,
        source_type=JournalEntry.SOURCE_PAYMENT,
        source_id=payment.pk,
    )
    deposit_account = payment.resolved_deposit_account()
    entry.add(deposit_account, debit=payment.amount, narration=f"Payment {payment.receipt_number}")

    allocated = payment.allocated
    if allocated:
        ar = Account.by_role(Account.ROLE_AR)
        entry.add(ar, credit=allocated, narration="Applied to invoices")
    unallocated = payment.amount - allocated
    if unallocated:
        credits_account = Account.by_role(Account.ROLE_CREDITS)
        entry.add(credits_account, credit=unallocated, narration="Unallocated customer credit")

    entry.post(user)
    payment.journal_entry = entry
    payment.save(update_fields=["journal_entry"])
    return entry


@transaction.atomic
def post_wht_credit(credit, user=None) -> JournalEntry:
    entry = JournalEntry.objects.create(
        date=credit.date,
        memo=f"WHT credit - {credit.invoice.number}",
        reference=credit.certificate_number,
        source_type=JournalEntry.SOURCE_WHT,
        source_id=credit.pk,
    )
    wht = Account.by_role(Account.ROLE_WHT)
    ar = Account.by_role(Account.ROLE_AR)
    entry.add(wht, debit=credit.amount, narration="Withholding tax credit")
    entry.add(ar, credit=credit.amount, narration=f"WHT on invoice {credit.invoice.number}")
    entry.post(user)
    credit.journal_entry = entry
    credit.save(update_fields=["journal_entry"])
    return entry


@transaction.atomic
def post_expense(expense, user=None) -> JournalEntry:
    entry = JournalEntry.objects.create(
        date=expense.date,
        memo=f"Expense - {expense.description}",
        reference=expense.reference,
        source_type=JournalEntry.SOURCE_EXPENSE,
        source_id=expense.pk,
    )
    entry.add(expense.account, debit=expense.amount_ugx, narration=expense.description)
    if expense.transaction_charge:
        charges = Account.by_role(Account.ROLE_CHARGES)
        entry.add(charges, debit=expense.transaction_charge, narration="Transaction charge")
    source_account = expense.resolved_source_account()
    entry.add(source_account, credit=expense.total_cost_ugx, narration=f"Paid via {expense.get_method_display()}")
    entry.post(user)
    expense.journal_entry = entry
    expense.save(update_fields=["journal_entry"])
    return entry


@transaction.atomic
def post_manual(date, memo: str, lines, user=None, reference: str = "") -> JournalEntry:
    """`lines` is an iterable of (account, debit, credit, narration) tuples."""
    entry = JournalEntry.objects.create(
        date=date,
        memo=memo,
        reference=reference,
        source_type=JournalEntry.SOURCE_MANUAL,
    )
    for account, debit, credit, narration in lines:
        entry.add(account, debit=Decimal(debit or 0), credit=Decimal(credit or 0), narration=narration)
    entry.post(user)
    return entry
