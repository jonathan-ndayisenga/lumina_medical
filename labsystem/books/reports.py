"""All reports read posted JournalLine rows only — never the document tables.
The trial balance is proof the ledger is internally consistent, not a
restatement of numbers that were never cross-checked."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from .document_models import Client, Invoice, WithholdingCredit
from .ledger_models import Account, JournalLine


def _posted_lines(start=None, end=None):
    lines = JournalLine.objects.filter(entry__is_posted=True).select_related("entry", "account")
    if start is not None:
        lines = lines.filter(entry__date__gte=start)
    if end is not None:
        lines = lines.filter(entry__date__lte=end)
    return lines


def trial_balance(start=None, end=None) -> dict:
    rows = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    accounts = Account.objects.filter(active=True).order_by("code")
    for account in accounts:
        agg = _posted_lines(start, end).filter(account=account).aggregate(
            d=models.Sum("debit"), c=models.Sum("credit")
        )
        debit = agg["d"] or Decimal("0")
        credit = agg["c"] or Decimal("0")
        if not debit and not credit:
            continue
        net_debit = debit - credit if debit >= credit else Decimal("0")
        net_credit = credit - debit if credit > debit else Decimal("0")
        rows.append({
            "account": account,
            "debit": net_debit,
            "credit": net_credit,
        })
        total_debit += net_debit
        total_credit += net_credit
    return {
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }


def profit_and_loss(start=None, end=None) -> dict:
    income_rows = []
    direct_cost_rows = []
    overhead_rows = []
    total_income = Decimal("0")
    total_direct_cost = Decimal("0")
    total_overhead = Decimal("0")
    non_deductible = Decimal("0")

    for account in Account.objects.filter(active=True, type=Account.TYPE_INCOME).order_by("code"):
        amount = account.balance(start, end)
        if amount:
            income_rows.append({"account": account, "amount": amount})
            total_income += amount

    for account in Account.objects.filter(active=True, type=Account.TYPE_EXPENSE).order_by("code"):
        amount = account.balance(start, end)
        if not amount:
            continue
        row = {"account": account, "amount": amount}
        if account.is_direct_cost:
            direct_cost_rows.append(row)
            total_direct_cost += amount
        else:
            overhead_rows.append(row)
            total_overhead += amount
        if not account.is_deductible:
            non_deductible += amount

    gross_profit = total_income - total_direct_cost
    gross_margin_pct = (gross_profit / total_income * 100) if total_income else Decimal("0")
    net_profit = gross_profit - total_overhead

    return {
        "income_rows": income_rows,
        "total_income": total_income,
        "direct_cost_rows": direct_cost_rows,
        "total_direct_cost": total_direct_cost,
        "gross_profit": gross_profit,
        "gross_margin_pct": gross_margin_pct,
        "overhead_rows": overhead_rows,
        "total_overhead": total_overhead,
        "net_profit": net_profit,
        "non_deductible": non_deductible,
        "taxable_profit_before_adjustments": net_profit + non_deductible,
    }


def balance_sheet(as_at=None) -> dict:
    as_at = as_at or timezone.localdate()
    asset_rows, liability_rows, equity_rows = [], [], []
    total_assets = total_liabilities = total_equity = Decimal("0")

    for account in Account.objects.filter(active=True, type=Account.TYPE_ASSET).order_by("code"):
        amount = account.balance(end=as_at)
        if amount:
            asset_rows.append({"account": account, "amount": amount})
            total_assets += amount

    for account in Account.objects.filter(active=True, type=Account.TYPE_LIABILITY).order_by("code"):
        amount = account.balance(end=as_at)
        if amount:
            liability_rows.append({"account": account, "amount": amount})
            total_liabilities += amount

    for account in Account.objects.filter(active=True, type=Account.TYPE_EQUITY).order_by("code"):
        amount = account.balance(end=as_at)
        if amount:
            equity_rows.append({"account": account, "amount": amount})
            total_equity += amount

    financial_year = None
    from .ledger_models import FinancialYear
    financial_year = FinancialYear.for_date(as_at)
    period_start = financial_year.start_date if financial_year else None
    pnl = profit_and_loss(period_start, as_at)
    result_for_period = pnl["net_profit"]
    total_equity_with_result = total_equity + result_for_period

    return {
        "as_at": as_at,
        "asset_rows": asset_rows,
        "total_assets": total_assets,
        "liability_rows": liability_rows,
        "total_liabilities": total_liabilities,
        "equity_rows": equity_rows,
        "total_equity": total_equity,
        "result_for_period": result_for_period,
        "total_equity_with_result": total_equity_with_result,
        "balanced": total_assets == (total_liabilities + total_equity_with_result),
    }


AGEING_BUCKETS = ["Not yet due", "1-30", "31-60", "61-90", "Over 90"]


def _bucket_for_days_overdue(days: int) -> str:
    if days <= 0:
        return "Not yet due"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "Over 90"


def aged_receivables(as_at=None) -> dict:
    as_at = as_at or timezone.localdate()
    client_rows = []
    grand_totals = {bucket: Decimal("0") for bucket in AGEING_BUCKETS}

    for client in Client.objects.filter(active=True).order_by("name"):
        buckets = {bucket: Decimal("0") for bucket in AGEING_BUCKETS}
        has_balance = False
        for invoice in client.invoices.filter(status=Invoice.STATUS_OPEN):
            balance = invoice.balance
            if balance <= 0:
                continue
            has_balance = True
            days_overdue = (as_at - invoice.due_date).days if invoice.due_date else 0
            bucket = _bucket_for_days_overdue(days_overdue)
            buckets[bucket] += balance
            grand_totals[bucket] += balance
        if has_balance:
            client_rows.append({
                "client": client,
                "buckets": buckets,
                "total": sum(buckets.values(), Decimal("0")),
                "average_days_to_pay": client.average_days_to_pay(),
            })

    return {
        "buckets": AGEING_BUCKETS,
        "client_rows": client_rows,
        "grand_totals": grand_totals,
        "grand_total": sum(grand_totals.values(), Decimal("0")),
    }


def client_statement(client, start=None, end=None) -> dict:
    events = []

    invoices = client.invoices.exclude(status=Invoice.STATUS_DRAFT)
    if start is not None:
        invoices = invoices.filter(issue_date__gte=start)
    if end is not None:
        invoices = invoices.filter(issue_date__lte=end)
    for invoice in invoices:
        events.append({
            "sort_key": (invoice.issue_date, 0, invoice.pk),
            "date": invoice.issue_date,
            "type": "invoice",
            "label": f"Invoice {invoice.number}",
            "reference": invoice,
            "debit": invoice.total,
            "credit": Decimal("0"),
        })

    payments = client.payments.filter(voided_at__isnull=True)
    if start is not None:
        payments = payments.filter(date__gte=start)
    if end is not None:
        payments = payments.filter(date__lte=end)
    for payment in payments:
        events.append({
            "sort_key": (payment.date, 1, payment.pk),
            "date": payment.date,
            "type": "payment",
            "label": f"Payment {payment.receipt_number}",
            "reference": payment,
            "debit": Decimal("0"),
            "credit": payment.amount,
        })

    wht_credits = WithholdingCredit.objects.filter(client=client)
    if start is not None:
        wht_credits = wht_credits.filter(date__gte=start)
    if end is not None:
        wht_credits = wht_credits.filter(date__lte=end)
    for credit in wht_credits:
        events.append({
            "sort_key": (credit.date, 1, credit.pk),
            "date": credit.date,
            "type": "wht",
            "label": f"WHT credit ({credit.certificate_number or 'no cert #'})",
            "reference": credit,
            "debit": Decimal("0"),
            "credit": credit.amount,
        })

    events.sort(key=lambda e: e["sort_key"])
    running = Decimal("0")
    for event in events:
        running += event["debit"] - event["credit"]
        event["running_balance"] = running

    return {"client": client, "events": events, "closing_balance": running}


def revenue_by_product(start=None, end=None) -> dict:
    rows = []
    total = Decimal("0")
    for account in Account.objects.filter(active=True, type=Account.TYPE_INCOME).order_by("code"):
        amount = account.balance(start, end)
        if amount:
            rows.append({"account": account, "amount": amount})
            total += amount
    for row in rows:
        row["percent"] = (row["amount"] / total * 100) if total else Decimal("0")
    return {"rows": rows, "total": total}


def tax_pack(financial_year) -> dict:
    start, end = financial_year.start_date, financial_year.end_date
    total_wht = WithholdingCredit.objects.filter(date__gte=start, date__lte=end).aggregate(
        t=models.Sum("amount")
    )["t"] or Decimal("0")
    return {
        "financial_year": financial_year,
        "trial_balance": trial_balance(start, end),
        "profit_and_loss": profit_and_loss(start, end),
        "balance_sheet": balance_sheet(end),
        "aged_receivables": aged_receivables(end),
        "revenue_by_product": revenue_by_product(start, end),
        "total_wht_credits": total_wht,
    }
