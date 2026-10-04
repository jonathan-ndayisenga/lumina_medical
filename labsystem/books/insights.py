"""Numbers for the Books dashboard, plus the WhatsApp payment reminder."""
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.db.models import Sum
from django.db.models.functions import TruncMonth

from .document_models import CompanySettings, Expense, Invoice, Payment
from .ledger_models import Account
from .planning_models import ExpenseBudget
from .quotation_models import Quotation

ZERO = Decimal("0")


def _month_start(day: date, back: int = 0) -> date:
    month_index = day.year * 12 + day.month - 1 - back
    return date(month_index // 12, month_index % 12 + 1, 1)


def _by_month(queryset, date_field, amount_expr):
    rows = queryset.annotate(m=TruncMonth(date_field)).values("m").annotate(total=Sum(amount_expr))
    return {(row["m"].date() if hasattr(row["m"], "date") else row["m"]): row["total"] or ZERO for row in rows}


def monthly_trend(today: date, months: int = 12) -> dict:
    """Invoiced, collected and spent per month for the last `months` months."""
    start = _month_start(today, months - 1)
    invoiced = _by_month(
        Invoice.objects.filter(issue_date__gte=start).exclude(status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_VOID]),
        "issue_date", "total",
    )
    collected = _by_month(Payment.objects.filter(date__gte=start, voided_at__isnull=True), "date", "amount")
    expenses = Expense.objects.filter(date__gte=start, voided_at__isnull=True)
    spent = _by_month(expenses, "date", "amount_ugx")
    charges = _by_month(expenses, "date", "transaction_charge")
    keys = [_month_start(today, back) for back in range(months - 1, -1, -1)]
    return {
        "labels": [key.strftime("%b %Y") for key in keys],
        "invoiced": [float(invoiced.get(key, ZERO)) for key in keys],
        "collected": [float(collected.get(key, ZERO)) for key in keys],
        "spent": [float(spent.get(key, ZERO) + charges.get(key, ZERO)) for key in keys],
    }


def cash_position(today: date) -> dict:
    """Money in every payment account now, and how many months of average
    spending (last 3 full months) it covers."""
    accounts = [
        {"name": account.name, "balance": account.balance(end=today)}
        for account in Account.objects.filter(active=True, is_payment_account=True).order_by("code")
    ]
    total = sum((a["balance"] for a in accounts), ZERO)
    start, end = _month_start(today, 3), _month_start(today) - timedelta(days=1)
    expenses = Expense.objects.filter(date__range=(start, end), voided_at__isnull=True).aggregate(
        a=Sum("amount_ugx"), c=Sum("transaction_charge"),
    )
    monthly_spend = ((expenses["a"] or ZERO) + (expenses["c"] or ZERO)) / 3
    runway = round(float(total / monthly_spend), 1) if monthly_spend > 0 and total > 0 else None
    return {"accounts": accounts, "total": total, "monthly_spend": monthly_spend, "runway_months": runway}


def quotation_pipeline(today: date) -> dict:
    """Quotations waiting on the client, and the win rate over 90 days."""
    waiting = Quotation.objects.filter(status=Quotation.STATUS_SENT)
    recent = Quotation.objects.filter(issue_date__gte=today - timedelta(days=90))
    won = recent.filter(status__in=[Quotation.STATUS_ACCEPTED, Quotation.STATUS_CONVERTED]).count()
    decided = won + recent.filter(status=Quotation.STATUS_DECLINED).count()
    return {
        "waiting_count": waiting.count(),
        "waiting_value": waiting.aggregate(t=Sum("total"))["t"] or ZERO,
        "won": won,
        "decided": decided,
        "win_rate": round(100 * won / decided) if decided else None,
    }


def top_clients(start: date, limit: int = 5) -> list:
    invoices = Invoice.objects.exclude(status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_VOID])
    if start:
        invoices = invoices.filter(issue_date__gte=start)
    rows = list(invoices.values("client__name").annotate(total=Sum("total")).order_by("-total"))
    grand_total = sum((row["total"] for row in rows), ZERO)
    return [
        {"name": row["client__name"], "total": row["total"],
         "share": round(100 * row["total"] / grand_total) if grand_total else 0}
        for row in rows[:limit]
    ]


def budget_status(today: date) -> list:
    """This month's spending per budgeted account against its budget."""
    month_start = _month_start(today)
    rows = []
    for budget in ExpenseBudget.objects.filter(monthly_amount__gt=0).select_related("account").order_by("account__name"):
        agg = Expense.objects.filter(account=budget.account, date__range=(month_start, today), voided_at__isnull=True).aggregate(
            a=Sum("amount_ugx"), c=Sum("transaction_charge"),
        )
        spent = (agg["a"] or ZERO) + (agg["c"] or ZERO)
        rows.append({
            "account": budget.account.name, "budget": budget.monthly_amount, "spent": spent,
            "percent": round(100 * spent / budget.monthly_amount), "over": spent > budget.monthly_amount,
        })
    return rows


def whatsapp_phone(raw: str) -> str:
    """0772 123456 / +256 772 123456 -> 256772123456 (wa.me format)."""
    digits = "".join(ch for ch in raw or "" if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        return "256" + digits[1:]
    return digits


def payment_reminder_url(invoice) -> str:
    """wa.me link with a ready-to-send reminder for an unpaid invoice.
    Opens WhatsApp with the message filled in; nothing is sent until the user taps send."""
    company = CompanySettings.load()
    pay_ways = []
    if company.bank_name and company.bank_account_number:
        pay_ways.append(f"Bank: {company.bank_name}, A/C {company.bank_account_number} ({company.bank_account_name or company.legal_name})")
    if company.momo_mtn_number:
        pay_ways.append(f"MTN MoMo: {company.momo_mtn_number}" + (f" ({company.momo_mtn_name})" if company.momo_mtn_name else ""))
    if company.momo_airtel_number:
        pay_ways.append(f"Airtel Money: {company.momo_airtel_number}" + (f" ({company.momo_airtel_name})" if company.momo_airtel_name else ""))
    due = f" was due on {invoice.due_date:%d %b %Y}" if invoice.due_date else " is outstanding"
    lines = [
        f"Hello {invoice.client.contact_person or invoice.client.name},",
        f"This is a friendly reminder from {company.trading_name or company.legal_name} that invoice {invoice.number} "
        f"(balance UGX {invoice.balance:,.0f}){due}.",
    ]
    if pay_ways:
        lines.append("You can pay via " + "; ".join(pay_ways) + ".")
    lines.append("Thank you.")
    return f"https://wa.me/{whatsapp_phone(invoice.client.phone)}?text={quote(chr(10).join(lines))}"
