from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from admin_dashboard.forms import ExpenseForm
from admin_dashboard.models import Expense
from reception.models import Payment, Visit

from .accounts_seed import provision_chart_of_accounts
from .models import Account, JournalEntry, JournalLine


def _hospital(request):
    return request.user.hospital


def _require_finance(request):
    if not request.user.can_access_finance:
        return HttpResponseForbidden("Finance access required.")
    return None


def _ctx(nav_key, extra=None):
    """Build a base context dict with active_nav set."""
    ctx = {"active_nav": nav_key}
    if extra:
        ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    if not hospital:
        return HttpResponseForbidden()

    # Auto-provision accounts if this hospital doesn't have any yet
    if not Account.objects.filter(hospital=hospital).exists():
        provision_chart_of_accounts(hospital)

    today = timezone.localdate()
    month_start = today.replace(day=1)

    # Today's collections (payments received today)
    todays_collections = (
        Payment.objects.filter(
            visit__hospital=hospital,
            paid_at__date=today,
            amount_paid__gt=0,
        ).aggregate(t=Sum("amount_paid"))["t"] or Decimal("0")
    )

    # Month revenue (from journal lines on revenue accounts)
    revenue_accounts = Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_REVENUE
    ).values_list("id", flat=True)
    month_revenue = (
        JournalLine.objects.filter(
            account__in=revenue_accounts,
            entry__date__gte=month_start,
            entry__date__lte=today,
            entry__is_reversal=False,
        ).aggregate(t=Sum("credit"))["t"] or Decimal("0")
    )

    # Month expenses
    expense_accounts = Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_EXPENSE
    ).values_list("id", flat=True)
    month_expenses = (
        JournalLine.objects.filter(
            account__in=expense_accounts,
            entry__date__gte=month_start,
            entry__date__lte=today,
            entry__is_reversal=False,
        ).aggregate(t=Sum("debit"))["t"] or Decimal("0")
    )

    # Outstanding receivables — visits with a balance due
    outstanding_visits = (
        Visit.objects.filter(hospital=hospital)
        .prefetch_related("payments", "visit_services")
        .filter(status__in=["open", "ready_for_billing", "completed"])
    )
    outstanding_visits = [v for v in outstanding_visits if v.balance_due > 0]
    total_outstanding = sum(v.balance_due for v in outstanding_visits)

    # Revenue by category for current month
    revenue_by_category = []
    for acc in Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_REVENUE, is_active=True
    ).order_by("code"):
        total = (
            JournalLine.objects.filter(
                account=acc,
                entry__date__gte=month_start,
                entry__date__lte=today,
                entry__is_reversal=False,
            ).aggregate(t=Sum("credit"))["t"] or Decimal("0")
        )
        if total > 0:
            revenue_by_category.append({"account": acc, "total": total})

    revenue_by_category.sort(key=lambda x: x["total"], reverse=True)

    # Recent journal entries (last 10)
    recent_entries = (
        JournalEntry.objects.filter(hospital=hospital)
        .select_related("source_payment__visit__patient", "source_expense")
        .order_by("-date", "-id")[:10]
    )

    return render(request, "finance/dashboard.html", {
        "active_nav": "finance_dashboard",
        "hospital": hospital,
        "today": today,
        "month_start": month_start,
        "todays_collections": todays_collections,
        "month_revenue": month_revenue,
        "month_expenses": month_expenses,
        "month_net": month_revenue - month_expenses,
        "total_outstanding": total_outstanding,
        "outstanding_visits": outstanding_visits[:5],
        "revenue_by_category": revenue_by_category,
        "recent_entries": recent_entries,
    })


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------

@login_required
def chart_of_accounts(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    accounts = Account.objects.filter(hospital=hospital, is_active=True).order_by("code")
    return render(request, "finance/chart_of_accounts.html", {
        "active_nav": "finance_accounts",
        "hospital": hospital,
        "accounts": accounts,
    })


@login_required
def account_create(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    error = None

    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        name = request.POST.get("name", "").strip()
        account_type = request.POST.get("account_type", "")
        sub_type = request.POST.get("sub_type", Account.SUB_EXPENSE)

        if not code or not name or not account_type:
            error = "Code, name, and type are required."
        elif Account.objects.filter(hospital=hospital, code=code).exists():
            error = f"Account code {code} already exists."
        else:
            Account.objects.create(
                hospital=hospital,
                code=code,
                name=name,
                account_type=account_type,
                sub_type=sub_type,
                is_system=False,
            )
            return redirect("finance_accounts")

    return render(request, "finance/account_form.html", {
        "active_nav": "finance_accounts",
        "hospital": hospital,
        "error": error,
        "account_types": Account.TYPE_CHOICES,
        "sub_types": Account.SUB_TYPE_CHOICES,
    })


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@login_required
def journal_list(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()

    date_from_str = request.GET.get("date_from", "")
    date_to_str = request.GET.get("date_to", "")
    source_type = request.GET.get("source_type", "")

    qs = JournalEntry.objects.filter(hospital=hospital).prefetch_related("lines__account")

    if date_from_str:
        try:
            qs = qs.filter(date__gte=date.fromisoformat(date_from_str))
        except ValueError:
            pass
    if date_to_str:
        try:
            qs = qs.filter(date__lte=date.fromisoformat(date_to_str))
        except ValueError:
            pass
    if source_type:
        qs = qs.filter(source_type=source_type)

    entries = qs.order_by("-date", "-id")[:100]

    return render(request, "finance/journal_list.html", {
        "active_nav": "finance_journal",
        "hospital": hospital,
        "entries": entries,
        "date_from": date_from_str,
        "date_to": date_to_str,
        "source_type": source_type,
        "source_type_choices": JournalEntry.SOURCE_CHOICES,
        "today": today,
    })


@login_required
def journal_entry_create(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    accounts = Account.objects.filter(hospital=hospital, is_active=True).order_by("code")
    error = None

    if request.method == "POST":
        description = request.POST.get("description", "").strip()
        entry_date = request.POST.get("date") or timezone.localdate().isoformat()
        debit_account_id = request.POST.get("debit_account")
        credit_account_id = request.POST.get("credit_account")
        amount_raw = request.POST.get("amount", "0").replace(",", "")

        try:
            amount = Decimal(amount_raw)
        except Exception:
            amount = Decimal("0")

        if not description:
            error = "Description is required."
        elif amount <= 0:
            error = "Amount must be greater than zero."
        elif debit_account_id == credit_account_id:
            error = "Debit and credit accounts must be different."
        else:
            try:
                dr_acc = Account.objects.get(pk=debit_account_id, hospital=hospital)
                cr_acc = Account.objects.get(pk=credit_account_id, hospital=hospital)
                entry = JournalEntry.objects.create(
                    hospital=hospital,
                    date=entry_date,
                    description=description,
                    source_type=JournalEntry.SOURCE_MANUAL,
                    posted_by=request.user,
                )
                JournalLine.objects.create(entry=entry, account=dr_acc, debit=amount)
                JournalLine.objects.create(entry=entry, account=cr_acc, credit=amount)
                return redirect("finance_journal")
            except Account.DoesNotExist:
                error = "Invalid account selection."

    return render(request, "finance/journal_entry_form.html", {
        "active_nav": "finance_journal",
        "hospital": hospital,
        "accounts": accounts,
        "today": timezone.localdate(),
        "error": error,
    })


# ---------------------------------------------------------------------------
# Cashbook
# ---------------------------------------------------------------------------

@login_required
def cashbook(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    date_from = request.GET.get("from", today.replace(day=1).isoformat())
    date_to = request.GET.get("to", today.isoformat())

    cash_accounts = Account.objects.filter(
        hospital=hospital,
        account_type=Account.TYPE_ASSET,
        sub_type__in=[Account.SUB_CASH, Account.SUB_BANK, Account.SUB_MOBILE],
        is_active=True,
    )

    lines = (
        JournalLine.objects.filter(
            account__in=cash_accounts,
            entry__date__gte=date_from,
            entry__date__lte=date_to,
            entry__is_reversal=False,
        )
        .select_related("entry", "account")
        .order_by("entry__date", "entry__id")
    )

    total_in = lines.aggregate(t=Sum("debit"))["t"] or Decimal("0")
    total_out = lines.aggregate(t=Sum("credit"))["t"] or Decimal("0")

    return render(request, "finance/cashbook.html", {
        "active_nav": "finance_cashbook",
        "hospital": hospital,
        "lines": lines,
        "date_from": date_from,
        "date_to": date_to,
        "total_in": total_in,
        "total_out": total_out,
        "net": total_in - total_out,
    })


# ---------------------------------------------------------------------------
# Debtor Ledger
# ---------------------------------------------------------------------------

@login_required
def debtor_ledger(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    visits = (
        Visit.objects.filter(hospital=hospital)
        .select_related("patient")
        .prefetch_related("payments", "visit_services")
        .order_by("patient__name", "-visit_date")
    )
    debtors = [v for v in visits if v.balance_due > 0]
    total_outstanding = sum(v.balance_due for v in debtors)

    return render(request, "finance/debtor_ledger.html", {
        "active_nav": "finance_debtors",
        "hospital": hospital,
        "debtors": debtors,
        "total_outstanding": total_outstanding,
    })


@login_required
def debtor_patient(request, patient_id):
    guard = _require_finance(request)
    if guard:
        return guard

    from reception.models import Patient
    hospital = _hospital(request)
    patient = get_object_or_404(Patient, pk=patient_id, hospital=hospital)
    visits = (
        Visit.objects.filter(patient=patient, hospital=hospital)
        .prefetch_related("payments", "visit_services__service")
        .order_by("-visit_date")
    )
    total_billed = sum(v.total_amount for v in visits)
    total_paid = sum(v.total_paid for v in visits)
    total_balance = sum(v.balance_due for v in visits)

    return render(request, "finance/debtor_patient.html", {
        "active_nav": "finance_debtors",
        "hospital": hospital,
        "patient": patient,
        "visits": visits,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_balance": total_balance,
    })


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@login_required
def revenue_report(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    date_from = request.GET.get("from", today.replace(day=1).isoformat())
    date_to = request.GET.get("to", today.isoformat())

    revenue_accounts = Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_REVENUE, is_active=True
    ).order_by("code")

    rows = []
    grand_total = Decimal("0")
    for acc in revenue_accounts:
        total = (
            JournalLine.objects.filter(
                account=acc,
                entry__date__gte=date_from,
                entry__date__lte=date_to,
                entry__is_reversal=False,
            ).aggregate(t=Sum("credit"))["t"] or Decimal("0")
        )
        rows.append({"account": acc, "total": total})
        grand_total += total

    return render(request, "finance/revenue_report.html", {
        "active_nav": "finance_revenue",
        "hospital": hospital,
        "rows": rows,
        "grand_total": grand_total,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def revenue_report_print(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    date_from = request.GET.get("from", today.replace(day=1).isoformat())
    date_to = request.GET.get("to", today.isoformat())

    revenue_accounts = Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_REVENUE, is_active=True
    ).order_by("code")

    rows = []
    grand_total = Decimal("0")
    for acc in revenue_accounts:
        total = (
            JournalLine.objects.filter(
                account=acc,
                entry__date__gte=date_from,
                entry__date__lte=date_to,
                entry__is_reversal=False,
            ).aggregate(t=Sum("credit"))["t"] or Decimal("0")
        )
        rows.append({"account": acc, "total": total})
        grand_total += total

    return render(request, "finance/revenue_report_print.html", {
        "hospital": hospital,
        "rows": rows,
        "grand_total": grand_total,
        "date_from": date_from,
        "date_to": date_to,
    })


@login_required
def trial_balance(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    as_of = request.GET.get("as_of", today.isoformat())

    accounts = Account.objects.filter(hospital=hospital, is_active=True).order_by("code")
    rows = []
    total_dr = Decimal("0")
    total_cr = Decimal("0")

    for acc in accounts:
        agg = JournalLine.objects.filter(
            account=acc,
            entry__date__lte=as_of,
            entry__is_reversal=False,
        ).aggregate(d=Sum("debit"), c=Sum("credit"))
        dr = agg["d"] or Decimal("0")
        cr = agg["c"] or Decimal("0")
        if dr == 0 and cr == 0:
            continue
        rows.append({"account": acc, "debit": dr, "credit": cr})
        total_dr += dr
        total_cr += cr

    return render(request, "finance/trial_balance.html", {
        "active_nav": "finance_trial_balance",
        "hospital": hospital,
        "rows": rows,
        "total_dr": total_dr,
        "total_cr": total_cr,
        "balanced": total_dr == total_cr,
        "as_of": as_of,
    })


@login_required
def profit_and_loss(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    date_from = request.GET.get("from", today.replace(month=1, day=1).isoformat())
    date_to = request.GET.get("to", today.isoformat())

    def _sum_lines(account_type, side):
        accs = Account.objects.filter(
            hospital=hospital, account_type=account_type, is_active=True
        ).values_list("id", flat=True)
        agg_key = "credit" if side == "credit" else "debit"
        return (
            JournalLine.objects.filter(
                account__in=accs,
                entry__date__gte=date_from,
                entry__date__lte=date_to,
                entry__is_reversal=False,
            ).aggregate(t=Sum(agg_key))["t"] or Decimal("0")
        )

    total_revenue = _sum_lines(Account.TYPE_REVENUE, "credit")
    total_expenses = _sum_lines(Account.TYPE_EXPENSE, "debit")
    net_profit = total_revenue - total_expenses

    revenue_rows = []
    for acc in Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_REVENUE, is_active=True
    ).order_by("code"):
        t = (
            JournalLine.objects.filter(
                account=acc,
                entry__date__gte=date_from,
                entry__date__lte=date_to,
                entry__is_reversal=False,
            ).aggregate(t=Sum("credit"))["t"] or Decimal("0")
        )
        if t:
            revenue_rows.append({"account": acc, "total": t})

    expense_rows = []
    for acc in Account.objects.filter(
        hospital=hospital, account_type=Account.TYPE_EXPENSE, is_active=True
    ).order_by("code"):
        t = (
            JournalLine.objects.filter(
                account=acc,
                entry__date__gte=date_from,
                entry__date__lte=date_to,
                entry__is_reversal=False,
            ).aggregate(t=Sum("debit"))["t"] or Decimal("0")
        )
        if t:
            expense_rows.append({"account": acc, "total": t})

    return render(request, "finance/profit_and_loss.html", {
        "active_nav": "finance_pnl",
        "hospital": hospital,
        "date_from": date_from,
        "date_to": date_to,
        "revenue_rows": revenue_rows,
        "total_revenue": total_revenue,
        "expense_rows": expense_rows,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
    })


# ---------------------------------------------------------------------------
# Expense Journal
# ---------------------------------------------------------------------------

@login_required
def expense_journal(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    month_start = today.replace(day=1)

    # Filter support
    date_from = request.GET.get("from", month_start.isoformat())
    date_to = request.GET.get("to", today.isoformat())
    category_filter = request.GET.get("category", "")

    expenses_qs = (
        Expense.objects.filter(hospital=hospital)
        .select_related("bank_account", "mobile_money_account", "cash_drawer")
        .order_by("-date", "-id")
    )
    if date_from:
        expenses_qs = expenses_qs.filter(date__gte=date_from)
    if date_to:
        expenses_qs = expenses_qs.filter(date__lte=date_to)
    if category_filter:
        expenses_qs = expenses_qs.filter(category=category_filter)

    # Period total
    period_total = expenses_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    # Category breakdown for sidebar summary
    category_totals = (
        Expense.objects.filter(hospital=hospital, date__gte=date_from, date__lte=date_to)
        .values("category")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    cat_label_map = dict(Expense.CATEGORY_CHOICES)
    category_summary = [
        {"label": cat_label_map.get(r["category"], r["category"]), "total": r["total"]}
        for r in category_totals
    ]

    # POST = record new expense
    form = ExpenseForm(hospital=hospital)
    error = None

    if request.method == "POST":
        form = ExpenseForm(request.POST, hospital=hospital)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.hospital = hospital
            expense.save()  # signal auto-posts to journal
            from django.contrib import messages
            messages.success(request, f"Expense recorded: {expense.description} — UGX {expense.amount:,.0f}")
            return redirect("finance_expenses")
        else:
            error = "Please fix the errors below."

    return render(request, "finance/expense_journal.html", {
        "active_nav": "finance_expenses",
        "hospital": hospital,
        "form": form,
        "error": error,
        "expenses": expenses_qs,
        "period_total": period_total,
        "category_summary": category_summary,
        "category_choices": Expense.CATEGORY_CHOICES,
        "date_from": date_from,
        "date_to": date_to,
        "category_filter": category_filter,
        "today": today,
    })


@login_required
def expense_delete(request, expense_id):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    expense = get_object_or_404(Expense, pk=expense_id, hospital=hospital)

    if request.method == "POST":
        desc = expense.description
        expense.delete()  # signal reverses journal entry
        from django.contrib import messages
        messages.success(request, f"Expense '{desc}' deleted and reversed from ledger.")
        return redirect("finance_expenses")

    return render(request, "finance/expense_confirm_delete.html", {
        "active_nav": "finance_expenses",
        "hospital": hospital,
        "expense": expense,
    })


# ---------------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------------

@login_required
def balance_sheet(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    today = timezone.localdate()
    as_of = request.GET.get("as_of", today.isoformat())

    def _account_balance(acc, as_of_date):
        agg = JournalLine.objects.filter(
            account=acc,
            entry__date__lte=as_of_date,
        ).aggregate(d=Sum("debit"), c=Sum("credit"))
        dr = agg["d"] or Decimal("0")
        cr = agg["c"] or Decimal("0")
        if acc.account_type in (Account.TYPE_ASSET, Account.TYPE_EXPENSE):
            return dr - cr
        return cr - dr

    def _section(account_type):
        rows = []
        total = Decimal("0")
        for acc in Account.objects.filter(
            hospital=hospital, account_type=account_type, is_active=True
        ).order_by("code"):
            bal = _account_balance(acc, as_of)
            if bal != 0:
                rows.append({"account": acc, "balance": bal})
                total += bal
        return rows, total

    asset_rows, total_assets = _section(Account.TYPE_ASSET)
    liability_rows, total_liabilities = _section(Account.TYPE_LIABILITY)
    equity_rows, total_equity = _section(Account.TYPE_EQUITY)

    # Retained earnings = cumulative net profit (revenue - expenses) up to as_of
    rev_agg = JournalLine.objects.filter(
        account__hospital=hospital,
        account__account_type=Account.TYPE_REVENUE,
        entry__date__lte=as_of,
    ).aggregate(t=Sum("credit"))
    exp_agg = JournalLine.objects.filter(
        account__hospital=hospital,
        account__account_type=Account.TYPE_EXPENSE,
        entry__date__lte=as_of,
    ).aggregate(t=Sum("debit"))
    retained_earnings = (rev_agg["t"] or Decimal("0")) - (exp_agg["t"] or Decimal("0"))

    total_equity_and_retained = total_equity + retained_earnings
    total_liabilities_and_equity = total_liabilities + total_equity_and_retained

    return render(request, "finance/balance_sheet.html", {
        "active_nav": "finance_balance_sheet",
        "hospital": hospital,
        "as_of": as_of,
        "asset_rows": asset_rows,
        "total_assets": total_assets,
        "liability_rows": liability_rows,
        "total_liabilities": total_liabilities,
        "equity_rows": equity_rows,
        "total_equity": total_equity,
        "retained_earnings": retained_earnings,
        "total_equity_and_retained": total_equity_and_retained,
        "total_liabilities_and_equity": total_liabilities_and_equity,
        "balanced": total_assets == total_liabilities_and_equity,
    })


# ---------------------------------------------------------------------------
# Opening Balances
# ---------------------------------------------------------------------------

@login_required
def opening_balances(request):
    guard = _require_finance(request)
    if guard:
        return guard

    hospital = _hospital(request)
    error = None
    success = None

    # Only show asset and liability accounts — those are the ones with opening balances
    accounts = Account.objects.filter(
        hospital=hospital,
        account_type__in=[Account.TYPE_ASSET, Account.TYPE_LIABILITY, Account.TYPE_EQUITY],
        is_active=True,
    ).order_by("code")

    # Check if opening balances already posted
    existing = JournalEntry.objects.filter(
        hospital=hospital,
        description__startswith="Opening balance",
        source_type=JournalEntry.SOURCE_MANUAL,
    ).order_by("-created_at")

    if request.method == "POST":
        ob_date = request.POST.get("ob_date") or timezone.localdate().isoformat()
        lines_data = []
        total_dr = Decimal("0")
        total_cr = Decimal("0")

        for acc in accounts:
            dr_raw = request.POST.get(f"dr_{acc.pk}", "").strip().replace(",", "")
            cr_raw = request.POST.get(f"cr_{acc.pk}", "").strip().replace(",", "")
            try:
                dr = Decimal(dr_raw) if dr_raw else Decimal("0")
                cr = Decimal(cr_raw) if cr_raw else Decimal("0")
            except Exception:
                dr = cr = Decimal("0")
            if dr > 0 or cr > 0:
                lines_data.append({"account": acc, "debit": dr, "credit": cr})
                total_dr += dr
                total_cr += cr

        if not lines_data:
            error = "Enter at least one opening balance."
        elif total_dr != total_cr:
            error = f"Entry does not balance — debits UGX {total_dr:,.0f}, credits UGX {total_cr:,.0f}. They must be equal."
        else:
            entry = JournalEntry.objects.create(
                hospital=hospital,
                date=ob_date,
                description=f"Opening balances as of {ob_date}",
                source_type=JournalEntry.SOURCE_MANUAL,
                posted_by=request.user,
            )
            for ld in lines_data:
                JournalLine.objects.create(
                    entry=entry,
                    account=ld["account"],
                    debit=ld["debit"],
                    credit=ld["credit"],
                    description="Opening balance",
                )
            success = f"Opening balances posted — {entry.reference}"

    return render(request, "finance/opening_balances.html", {
        "active_nav": "finance_opening_balances",
        "hospital": hospital,
        "accounts": accounts,
        "existing": existing,
        "error": error,
        "success": success,
        "today": timezone.localdate(),
    })
