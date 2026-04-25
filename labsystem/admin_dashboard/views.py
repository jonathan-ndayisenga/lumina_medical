from datetime import timedelta
from decimal import Decimal
import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db.models import Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.forms import HospitalSubscriptionPaymentForm, SubscriptionPlanForm
from accounts.models import Hospital, HospitalSubscriptionPayment, SubscriptionPlan, User
from lab.models import LabReport
from reception.models import Patient, Payment, QueueEntry, Service, Visit
from .forms import (
    BankAccountForm,
    BankReconciliationForm,
    BankTransactionForm,
    CashTransactionForm,
    CloseCashDrawerForm,
    ExpenseForm,
    HospitalForm,
    HospitalServiceForm,
    HospitalStaffUserForm,
    HospitalStaffUserUpdateForm,
    InventoryItemForm,
    MobileMoneyAccountForm,
    MobileMoneyStatementForm,
    MobileMoneyTransactionForm,
    OpenCashDrawerForm,
    SalaryForm,
    ThreeWayReconciliationForm,
)
from .models import (
    BankAccount,
    BankTransaction,
    CashDrawer,
    CashTransaction,
    Expense,
    HospitalAccount,
    InventoryItem,
    MobileMoneyAccount,
    MobileMoneyTransaction,
    ReconciliationStatement,
    Salary,
    sync_hospital_account_balance,
)


def role_required(*allowed_roles):
    def decorator(view_func):
        @login_required
        def wrapped(request, *args, **kwargs):
            user_role = getattr(request.user, "role", "")
            # Allow superadmin to access all hospital admin views
            if request.user.is_superuser or user_role == User.ROLE_SUPERADMIN:
                return view_func(request, *args, **kwargs)
            if user_role not in allowed_roles:
                return HttpResponseForbidden("You do not have access to this page.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def hospital_admin_only(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if getattr(request.user, "role", "") != User.ROLE_HOSPITAL_ADMIN:
            return HttpResponseForbidden("This page is available to hospital admin users only.")
        return view_func(request, *args, **kwargs)

    return wrapped


def active_hospital(request):
    return getattr(request, "hospital", None) or getattr(request.user, "hospital", None)


def parse_date_param(value, fallback):
    raw = (value or "").strip()
    if not raw:
        return fallback
    try:
        return timezone.datetime.fromisoformat(raw).date()
    except ValueError:
        return fallback


def payment_from_receipt_reference(reference, hospital, mode=None):
    """Best-effort match when a statement line includes our receipt number (RCT-YYYYMMDD-000123)."""
    if not reference or not hospital:
        return None
    match = re.search(r"RCT-\d{8}-(\d+)", str(reference).strip(), re.IGNORECASE)
    if not match:
        return None
    try:
        payment_id = int(match.group(1))
    except ValueError:
        return None
    queryset = Payment.objects.filter(pk=payment_id, visit__hospital=hospital)
    if mode:
        queryset = queryset.filter(mode=mode)
    return queryset.select_related("visit__patient").first()


@hospital_admin_only
def financial_metric_detail(request, metric):
    """Explain and break down a financial dashboard figure."""
    hospital = active_hospital(request)
    if not hospital:
        return HttpResponseForbidden("No hospital is linked to this account.")

    today = timezone.localdate()
    month_start = today.replace(day=1)
    period_start = parse_date_param(request.GET.get("start_date"), month_start)
    period_end = parse_date_param(request.GET.get("end_date"), today)

    payments = Payment.objects.filter(visit__hospital=hospital).select_related("visit__patient", "recorded_by")
    expenses = Expense.objects.filter(hospital=hospital).select_related("bank_account", "mobile_money_account", "cash_drawer")
    salaries = Salary.objects.filter(hospital=hospital).select_related("employee")

    payments_period = payments.filter(paid_at__date__gte=period_start, paid_at__date__lte=period_end)
    expenses_period = expenses.filter(date__gte=period_start, date__lte=period_end)
    salaries_period = salaries.filter(paid=True, paid_at__gte=period_start, paid_at__lte=period_end)

    open_drawer = CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=True).order_by("-date", "-id").first()

    # Default context for the template.
    context = finance_context(
        request,
        "hospital_financials",
        "Metric Detail",
        "Understand what this figure means and which records make it up.",
    )
    context.update(
        {
            "metric_key": metric,
            "period_start": period_start,
            "period_end": period_end,
            "value": Decimal("0"),
            "definition": "",
            "formula": "",
            "source_models": "",
            "records_title": "",
            "records": [],
            "records_kind": "",
            "open_drawer": open_drawer,
        }
    )

    if metric == "income_period":
        context.update(
            {
                "dashboard_title": "Total Collected (Period)",
                "definition": "Sum of actual cash received for the selected period (not billed totals).",
                "formula": "SUM(Payment.amount_paid) for payments in period.",
                "source_models": "reception.Payment (amount_paid, paid_at, mode).",
                "records_title": "Receipts in this period",
                "records_kind": "payments",
                "value": payments_period.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0"),
                "records": payments_period.order_by("-paid_at", "-id")[:200],
            }
        )
    elif metric == "expenses_period":
        context.update(
            {
                "dashboard_title": "Expenses (Period)",
                "definition": "Sum of expenses recorded during the selected period.",
                "formula": "SUM(Expense.amount) for expenses in period.",
                "source_models": "admin_dashboard.Expense (amount, category, source, date).",
                "records_title": "Expenses in this period",
                "records_kind": "expenses",
                "value": expenses_period.aggregate(total=Sum("amount"))["total"] or Decimal("0"),
                "records": expenses_period.order_by("-date", "-id")[:200],
            }
        )
    elif metric == "salaries_period":
        context.update(
            {
                "dashboard_title": "Salaries (Period)",
                "definition": "Total of paid salary records during the selected period.",
                "formula": "SUM(Salary.amount) where paid=True and paid_at in period.",
                "source_models": "admin_dashboard.Salary (amount, paid, paid_at, employee).",
                "records_title": "Paid salaries in this period",
                "records_kind": "salaries",
                "value": salaries_period.aggregate(total=Sum("amount"))["total"] or Decimal("0"),
                "records": salaries_period.order_by("-paid_at", "-id")[:200],
            }
        )
    elif metric == "net_profit_period":
        income = payments_period.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")
        exp_total = expenses_period.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        sal_total = salaries_period.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        context.update(
            {
                "dashboard_title": "Net Profit (Period)",
                "definition": "Profit estimate for the period using collected income minus expenses and paid salaries.",
                "formula": "Income - (Expenses + Salaries).",
                "source_models": "reception.Payment + admin_dashboard.Expense + admin_dashboard.Salary.",
                "records_title": "Breakdown",
                "records_kind": "net_profit_breakdown",
                "value": income - (exp_total + sal_total),
                "income_component": income,
                "expense_component": exp_total,
                "salary_component": sal_total,
            }
        )
    elif metric == "outstanding_balance":
        outstanding_total = (
            payments.exclude(status=Payment.STATUS_WAIVED)
            .aggregate(total=Sum(models.F("amount") - models.F("amount_paid")))["total"]
            or Decimal("0")
        )
        outstanding_payments = payments.exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).order_by("-paid_at", "-id")[:200]
        context.update(
            {
                "dashboard_title": "Outstanding Balances",
                "definition": "Total remaining balance due across all billed visits that are not fully paid or waived.",
                "formula": "SUM(Payment.amount - Payment.amount_paid) where not waived.",
                "source_models": "reception.Payment (amount, amount_paid, status).",
                "records_title": "Receipts with balances due",
                "records_kind": "payments",
                "value": outstanding_total,
                "records": outstanding_payments,
            }
        )
    elif metric == "pending_payments":
        pending = payments.exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).order_by("-paid_at", "-id")[:200]
        context.update(
            {
                "dashboard_title": "Pending Payments",
                "definition": "Count of receipts where the billed amount is not fully paid (excluding waived).",
                "formula": "COUNT(Payment) where amount_paid != amount and status != waived.",
                "source_models": "reception.Payment (amount, amount_paid, status).",
                "records_title": "Receipts pending full payment",
                "records_kind": "payments",
                "value": payments.exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).count(),
                "records": pending,
            }
        )
    elif metric == "part_paid":
        part_paid = payments.filter(status=Payment.STATUS_PART_PAID).order_by("-paid_at", "-id")[:200]
        context.update(
            {
                "dashboard_title": "Part Paid Visits",
                "definition": "Visits with partial payment recorded (some money received, but not fully settled).",
                "formula": "Payment.status == part_paid.",
                "source_models": "reception.Payment (status).",
                "records_title": "Part paid receipts",
                "records_kind": "payments",
                "value": payments.filter(status=Payment.STATUS_PART_PAID).count(),
                "records": part_paid,
            }
        )
    elif metric == "account_balance":
        account = sync_hospital_account_balance(hospital)
        context.update(
            {
                "dashboard_title": "Account Balance",
                "definition": "Current computed hospital balance: collected income minus expenses and paid salaries.",
                "formula": "SUM(Payment.amount_paid) - (SUM(Expense.amount) + SUM(paid Salary.amount)).",
                "source_models": "admin_dashboard.HospitalAccount (derived from Payment/Expense/Salary).",
                "records_title": "What affects this balance",
                "records_kind": "account_balance",
                "value": account.balance if account else Decimal("0"),
            }
        )
    elif metric == "reconciliation_variance":
        latest_bank = (
            ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_BANK)
            .select_related("bank_account")
            .first()
        )
        latest_mobile = (
            ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_MOBILE_MONEY)
            .select_related("mobile_money_account")
            .first()
        )
        bank_variance = (latest_bank.total_deposits - latest_bank.reconciled_balance) if latest_bank else Decimal("0")
        mobile_variance = (latest_mobile.total_deposits - latest_mobile.reconciled_balance) if latest_mobile else Decimal("0")
        drawer_discrepancy = open_drawer.discrepancy if open_drawer and open_drawer.discrepancy is not None else Decimal("0")
        context.update(
            {
                "dashboard_title": "Reconciliation Variance",
                "definition": "Total reconciliation variance: bank variance + mobile variance + cash drawer discrepancy.",
                "formula": "|bank credits - internal| + |mobile credits - internal| + |cash discrepancy|.",
                "source_models": "admin_dashboard.ReconciliationStatement + admin_dashboard.CashDrawer.",
                "records_title": "Variance components",
                "records_kind": "variance_breakdown",
                "value": abs(bank_variance) + abs(mobile_variance) + abs(drawer_discrepancy),
                "latest_bank": latest_bank,
                "latest_mobile": latest_mobile,
                "bank_variance": bank_variance,
                "mobile_variance": mobile_variance,
                "drawer_discrepancy": drawer_discrepancy,
            }
        )
    elif metric == "paid_income_all":
        context.update(
            {
                "dashboard_title": "Paid Income (All Time)",
                "definition": "All money collected so far for this hospital.",
                "formula": "SUM(Payment.amount_paid).",
                "source_models": "reception.Payment (amount_paid).",
                "records_title": "Recent receipts (all time)",
                "records_kind": "payments",
                "value": payments.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0"),
                "records": payments.filter(paid_at__isnull=False).order_by("-paid_at", "-id")[:200],
            }
        )
    elif metric == "expenses_all":
        context.update(
            {
                "dashboard_title": "Expenses (All Time)",
                "definition": "All recorded expenses for this hospital.",
                "formula": "SUM(Expense.amount).",
                "source_models": "admin_dashboard.Expense.",
                "records_title": "Recent expenses (all time)",
                "records_kind": "expenses",
                "value": expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0"),
                "records": expenses.order_by("-date", "-id")[:200],
            }
        )
    elif metric == "salaries_all":
        paid_salaries = salaries.filter(paid=True)
        context.update(
            {
                "dashboard_title": "Paid Salaries (All Time)",
                "definition": "Total of salary records marked paid for this hospital.",
                "formula": "SUM(Salary.amount) where paid=True.",
                "source_models": "admin_dashboard.Salary (paid=True).",
                "records_title": "Recent paid salaries (all time)",
                "records_kind": "salaries",
                "value": paid_salaries.aggregate(total=Sum("amount"))["total"] or Decimal("0"),
                "records": paid_salaries.order_by("-paid_at", "-id")[:200],
            }
        )
    elif metric == "net_profit_all":
        income = payments.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")
        exp_total = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        sal_total = salaries.filter(paid=True).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        context.update(
            {
                "dashboard_title": "Net Profit (All Time)",
                "definition": "All-time profit estimate using collected income minus all expenses and paid salaries.",
                "formula": "Income - (Expenses + Salaries).",
                "source_models": "reception.Payment + admin_dashboard.Expense + admin_dashboard.Salary.",
                "records_title": "Breakdown",
                "records_kind": "net_profit_breakdown",
                "value": income - (exp_total + sal_total),
                "income_component": income,
                "expense_component": exp_total,
                "salary_component": sal_total,
            }
        )
    elif metric == "unreconciled_bank":
        items = (
            BankTransaction.objects.filter(
                bank_account__hospital=hospital,
                transaction_type=BankTransaction.TYPE_CREDIT,
                is_reconciled=False,
            )
            .select_related("bank_account")
            .order_by("-transaction_date", "-id")[:200]
        )
        context.update(
            {
                "dashboard_title": "Unreconciled Bank Credits",
                "definition": "Bank credit statement lines that are not yet matched to any receipt.",
                "formula": "COUNT/SUM(BankTransaction) where credit and is_reconciled=False.",
                "source_models": "admin_dashboard.BankTransaction (external statement lines).",
                "records_title": "Unreconciled bank credits",
                "records_kind": "bank_transactions",
                "value": items.count(),
                "records": items,
            }
        )
    elif metric == "unreconciled_mobile":
        items = (
            MobileMoneyTransaction.objects.filter(
                mobile_money_account__hospital=hospital,
                transaction_type=MobileMoneyTransaction.TYPE_CREDIT,
                is_reconciled=False,
            )
            .select_related("mobile_money_account")
            .order_by("-transaction_date", "-id")[:200]
        )
        context.update(
            {
                "dashboard_title": "Unreconciled Mobile Money Credits",
                "definition": "Mobile money credit statement lines that are not yet matched to any receipt.",
                "formula": "COUNT/SUM(MobileMoneyTransaction) where credit and is_reconciled=False.",
                "source_models": "admin_dashboard.MobileMoneyTransaction (external statement lines).",
                "records_title": "Unreconciled mobile money credits",
                "records_kind": "mobile_transactions",
                "value": items.count(),
                "records": items,
            }
        )
    else:
        context.update(
            {
                "dashboard_title": "Unknown Metric",
                "definition": "This metric key is not recognized.",
                "formula": "",
                "source_models": "",
                "value": Decimal("0"),
            }
        )

    return render(request, "admin_dashboard/financial_metric_detail.html", context)


def hospital_admin_context(request, active_nav, dashboard_title, dashboard_intro):
    hospital = active_hospital(request)
    return {
        "base_template": "base.html",
        "active_nav": active_nav,
        "dashboard_title": dashboard_title,
        "dashboard_intro": dashboard_intro,
        "hospital": hospital,
    }


def superadmin_context(request, active_nav, dashboard_title, dashboard_intro):
    return {
        "base_template": "admin_dashboard/developer_base.html",
        "active_nav": active_nav,
        "dashboard_title": dashboard_title,
        "dashboard_intro": dashboard_intro,
    }


def hospital_owned_or_404(model, request, **filters):
    hospital = active_hospital(request)
    queryset = model.objects.filter(hospital=hospital, **filters) if hospital else model.objects.none()
    return get_object_or_404(queryset)


def finance_context(request, active_nav, dashboard_title, dashboard_intro):
    context = hospital_admin_context(request, active_nav, dashboard_title, dashboard_intro)
    context["base_template"] = "base.html"
    return context


@role_required(User.ROLE_SUPERADMIN)
def developer_dashboard(request):
    hospitals = Hospital.objects.select_related("subscription_plan").all()
    total_income = (
        HospitalSubscriptionPayment.objects.aggregate(total=Sum("amount"))["total"] or 0
    )
    expiring_hospitals = hospitals.filter(
        subscription_end_date__isnull=False,
        subscription_end_date__lte=timezone.now().date() + timedelta(days=7),
    )
    context = {
        "active_nav": "superadmin",
        "dashboard_title": "Super Admin Dashboard",
        "dashboard_intro": "Platform-wide hospital oversight and subscription health.",
        "hospitals": hospitals,
        "total_income": total_income,
        "expiring_hospitals": expiring_hospitals,
        "total_hospitals": hospitals.count(),
        "active_hospitals": hospitals.filter(is_active=True).count(),
        "total_users": User.objects.count(),
    }
    return render(request, "admin_dashboard/developer_dashboard.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def hospital_dashboard(request):
    hospital = active_hospital(request)
    reports = LabReport.objects.filter(hospital=hospital) if hospital else LabReport.objects.none()
    visits = Visit.objects.filter(hospital=hospital).select_related("patient") if hospital else Visit.objects.none()
    queue_entries = QueueEntry.objects.filter(hospital=hospital) if hospital else QueueEntry.objects.none()
    users = hospital.users.all() if hospital else User.objects.none()
    payments = Payment.objects.filter(visit__hospital=hospital) if hospital else Payment.objects.none()
    services = Service.objects.filter(hospital=hospital, is_active=True) if hospital else Service.objects.none()
    account = sync_hospital_account_balance(hospital) if hospital else None
    low_stock_items = InventoryItem.objects.filter(hospital=hospital, quantity__lte=models.F("low_stock_threshold")) if hospital else InventoryItem.objects.none()

    context = {
        "active_nav": "hospital_admin",
        "dashboard_title": "Hospital Admin Dashboard",
        "dashboard_intro": "Hospital oversight now spans staffing, patient flow, lab throughput, and the first layer of billing visibility.",
        "hospital": hospital,
        "user_count": users.count() if hospital else 0,
        "patient_count": Patient.objects.filter(hospital=hospital).count() if hospital else 0,
        "visit_count": visits.count(),
        "completed_visit_count": visits.filter(status=Visit.STATUS_COMPLETED).count(),
        "open_queue_count": queue_entries.filter(processed=False).count(),
        "lab_queue_count": queue_entries.filter(
            queue_type__in=[QueueEntry.TYPE_LAB_RECEPTION, QueueEntry.TYPE_LAB_DOCTOR],
            processed=False,
        ).count(),
        "doctor_queue_count": queue_entries.filter(queue_type=QueueEntry.TYPE_DOCTOR, processed=False).count(),
        "nurse_queue_count": queue_entries.filter(queue_type=QueueEntry.TYPE_NURSE, processed=False).count(),
        "report_count": reports.count(),
        "draft_reports": reports.filter(printed=False).count(),
        "service_count": services.count(),
        "total_billed": payments.aggregate(total=Sum("amount"))["total"] or 0,
        "realized_income": payments.aggregate(total=Sum("amount_paid"))["total"] or 0,
        "account_balance": account.balance if account else 0,
        "paid_visits": payments.filter(status=Payment.STATUS_PAID).count(),
        "pending_payments": payments.exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).count(),
        "expense_total": Expense.objects.filter(hospital=hospital).aggregate(total=Sum("amount"))["total"] or 0 if hospital else 0,
        "salary_total": Salary.objects.filter(hospital=hospital, paid=True).aggregate(total=Sum("amount"))["total"] or 0 if hospital else 0,
        "low_stock_count": low_stock_items.count() if hospital else 0,
        "receptionist_count": users.filter(role=User.ROLE_RECEPTIONIST).count() if hospital else 0,
        "lab_attendant_count": users.filter(role=User.ROLE_LAB_ATTENDANT).count() if hospital else 0,
        "doctor_count": users.filter(role=User.ROLE_DOCTOR).count() if hospital else 0,
        "nurse_count": users.filter(role=User.ROLE_NURSE).count() if hospital else 0,
        "recent_visits": visits.order_by("-visit_date")[:6],
        "recent_reports": reports.select_related("visit__patient").order_by("-created_at")[:6],
        "low_stock_items": low_stock_items.order_by("quantity", "name")[:6] if hospital else [],
    }
    return render(request, "admin_dashboard/hospital_dashboard.html", context)


@hospital_admin_only
def financial_report(request):
    hospital = active_hospital(request)
    payments = Payment.objects.filter(visit__hospital=hospital) if hospital else Payment.objects.none()
    paid_income = payments.aggregate(total=Sum("amount_paid"))["total"] or 0
    expense_total = Expense.objects.filter(hospital=hospital).aggregate(total=Sum("amount"))["total"] or 0 if hospital else 0
    salary_total = Salary.objects.filter(hospital=hospital, paid=True).aggregate(total=Sum("amount"))["total"] or 0 if hospital else 0
    account = sync_hospital_account_balance(hospital) if hospital else None
    today = timezone.localdate()
    month_start = today.replace(day=1)

    # Dashboard period (default: current month).
    period_start_raw = request.GET.get("start_date", "").strip() or str(month_start)
    period_end_raw = request.GET.get("end_date", "").strip() or str(today)
    try:
        period_start = timezone.datetime.fromisoformat(period_start_raw).date()
    except ValueError:
        period_start = month_start
    try:
        period_end = timezone.datetime.fromisoformat(period_end_raw).date()
    except ValueError:
        period_end = today

    payments_today = payments.filter(paid_at__date=today)
    payments_month = payments.filter(paid_at__date__gte=month_start, paid_at__date__lte=today)
    payments_period = payments.filter(paid_at__date__gte=period_start, paid_at__date__lte=period_end)
    income_period = payments_period.aggregate(total=Sum("amount_paid"))["total"] or 0
    expenses_period = (
        Expense.objects.filter(hospital=hospital, date__gte=period_start, date__lte=period_end).aggregate(total=Sum("amount"))["total"]
        or 0
        if hospital
        else 0
    )
    salaries_period = (
        Salary.objects.filter(hospital=hospital, paid=True, paid_at__gte=period_start, paid_at__lte=period_end).aggregate(total=Sum("amount"))["total"]
        or 0
        if hospital
        else 0
    )

    open_drawer = CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=True).first() if hospital else None
    open_drawer_cash_in = (
        open_drawer.transactions.filter(transaction_type=CashTransaction.TYPE_CASH_IN).aggregate(total=Sum("amount"))["total"]
        if open_drawer
        else None
    ) or 0
    open_drawer_cash_out = (
        open_drawer.transactions.filter(transaction_type=CashTransaction.TYPE_CASH_OUT).aggregate(total=Sum("amount"))["total"]
        if open_drawer
        else None
    ) or 0
    open_drawer_expected = (open_drawer.opening_balance + open_drawer_cash_in - open_drawer_cash_out) if open_drawer else None
    last_closed_drawer = CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=False).order_by("-date", "-id").first() if hospital else None

    recent_receipts = (
        payments.filter(paid_at__isnull=False)
        .select_related("visit__patient", "recorded_by")
        .order_by("-paid_at", "-id")[:8]
        if hospital
        else Payment.objects.none()
    )
    recent_bank_statement = (
        ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_BANK)
        .select_related("bank_account", "generated_by")
        .first()
        if hospital
        else None
    )
    recent_three_way_statement = (
        ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_THREE_WAY)
        .select_related("generated_by")
        .first()
        if hospital
        else None
    )
    recent_mobile_statement = (
        ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_MOBILE_MONEY)
        .select_related("mobile_money_account", "generated_by")
        .first()
        if hospital
        else None
    )

    bank_variance = (recent_bank_statement.total_deposits - recent_bank_statement.reconciled_balance) if recent_bank_statement else Decimal("0")
    mobile_variance = (recent_mobile_statement.total_deposits - recent_mobile_statement.reconciled_balance) if recent_mobile_statement else Decimal("0")
    cash_discrepancy = open_drawer.discrepancy if open_drawer and open_drawer.discrepancy is not None else 0
    if not cash_discrepancy and last_closed_drawer and last_closed_drawer.discrepancy is not None:
        cash_discrepancy = last_closed_drawer.discrepancy
    reconciliation_discrepancy_total = abs(bank_variance) + abs(mobile_variance) + abs(Decimal(str(cash_discrepancy)))

    unreconciled_bank_count = (
        BankTransaction.objects.filter(
            bank_account__hospital=hospital,
            transaction_type=BankTransaction.TYPE_CREDIT,
            is_reconciled=False,
        ).count()
        if hospital
        else 0
    )
    unreconciled_mobile_count = (
        MobileMoneyTransaction.objects.filter(
            mobile_money_account__hospital=hospital,
            transaction_type=MobileMoneyTransaction.TYPE_CREDIT,
            is_reconciled=False,
        ).count()
        if hospital
        else 0
    )
    unreconciled_bank_total = (
        BankTransaction.objects.filter(
            bank_account__hospital=hospital,
            transaction_type=BankTransaction.TYPE_CREDIT,
            is_reconciled=False,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
        if hospital
        else 0
    )
    unreconciled_mobile_total = (
        MobileMoneyTransaction.objects.filter(
            mobile_money_account__hospital=hospital,
            transaction_type=MobileMoneyTransaction.TYPE_CREDIT,
            is_reconciled=False,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
        if hospital
        else 0
    )

    # --- Modern dashboard: report selector + chart + summary table ---
    report_type = (request.GET.get("report_type") or "income_overview").strip()
    payment_mode = (request.GET.get("payment_mode") or "all").strip()
    bank_account_id = (request.GET.get("bank_account") or "").strip()
    mobile_account_id = (request.GET.get("mobile_account") or "").strip()

    report_type_options = [
        ("income_overview", "Income Overview"),
        ("cash_collection", "Cash Collection"),
        ("card_payments", "Card Payments"),
        ("mobile_money", "Mobile Money Payments"),
        ("expenses", "Expenses"),
        ("salaries", "Salaries"),
        ("net_profit", "Net Profit"),
    ]

    bank_accounts = (
        BankAccount.objects.filter(hospital=hospital, is_active=True).order_by("bank_name", "account_name")
        if hospital
        else BankAccount.objects.none()
    )
    mobile_accounts = (
        MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True).order_by("provider", "number")
        if hospital
        else MobileMoneyAccount.objects.none()
    )

    chart_title = ""
    chart_series_label = ""
    chart_kind = "line"
    chart_labels = []
    chart_values = []
    summary_rows = []

    def append_summary(date_value, amount_value, mode_value, account_value):
        summary_rows.append(
            {
                "date": date_value,
                "amount": amount_value,
                "mode": mode_value,
                "account": account_value,
            }
        )

    if hospital:
        if report_type in {"income_overview", "cash_collection", "card_payments", "mobile_money"}:
            payments_dash = (
                payments.filter(paid_at__date__gte=period_start, paid_at__date__lte=period_end)
                .exclude(status=Payment.STATUS_WAIVED)
                .exclude(paid_at__isnull=True)
            )

            if report_type == "cash_collection":
                payments_dash = payments_dash.filter(mode=Payment.MODE_CASH)
                chart_title = "Cash Collections"
                chart_series_label = "Cash received"
            elif report_type == "card_payments":
                payments_dash = payments_dash.filter(mode=Payment.MODE_CARD)
                chart_title = "Card Payments"
                chart_series_label = "Card received"
                if bank_account_id:
                    payments_dash = payments_dash.filter(bank_account_id=bank_account_id)
            elif report_type == "mobile_money":
                payments_dash = payments_dash.filter(mode=Payment.MODE_MOBILE_MONEY)
                chart_title = "Mobile Money Payments"
                chart_series_label = "Mobile money received"
                if mobile_account_id:
                    payments_dash = payments_dash.filter(mobile_account_id=mobile_account_id)
            else:
                chart_title = "Income Overview"
                chart_series_label = "Collected income"
                if payment_mode and payment_mode != "all":
                    payments_dash = payments_dash.filter(mode=payment_mode)

            daily = (
                payments_dash.annotate(day=TruncDate("paid_at"))
                .values("day")
                .annotate(total=Sum("amount_paid"))
                .order_by("day")
            )
            chart_labels = [row["day"].isoformat() for row in daily if row["day"]]
            chart_values = [str(row["total"] or 0) for row in daily]

            grouped = {}
            payments_for_summary = payments_dash.select_related("bank_account", "mobile_account")
            for payment in payments_for_summary.order_by("-paid_at", "-id")[:800]:
                day = payment.paid_at.date() if payment.paid_at else None
                if not day:
                    continue
                if payment.mode == Payment.MODE_CARD:
                    account_label = str(payment.bank_account) if payment.bank_account_id else "-"
                elif payment.mode == Payment.MODE_MOBILE_MONEY:
                    account_label = str(payment.mobile_account) if payment.mobile_account_id else "-"
                elif payment.mode == Payment.MODE_CASH:
                    account_label = "Cash Drawer"
                else:
                    account_label = "-"
                key = (day, payment.mode, account_label)
                grouped[key] = grouped.get(key, Decimal("0")) + (payment.amount_paid or Decimal("0"))

            for (day, mode_value, account_label), amount_value in sorted(grouped.items(), key=lambda item: item[0][0], reverse=True)[:200]:
                append_summary(day, amount_value, mode_value, account_label)

        elif report_type == "expenses":
            chart_title = "Expenses"
            chart_series_label = "Expenses recorded"
            chart_kind = "bar"
            expenses_dash = Expense.objects.filter(hospital=hospital, date__gte=period_start, date__lte=period_end).select_related(
                "bank_account", "mobile_money_account", "cash_drawer"
            )
            daily = expenses_dash.values("date").annotate(total=Sum("amount")).order_by("date")
            chart_labels = [row["date"].isoformat() for row in daily if row["date"]]
            chart_values = [str(row["total"] or 0) for row in daily]

            grouped = {}
            for expense in expenses_dash.order_by("-date", "-id")[:800]:
                key = (expense.date, "expense", expense.source_account_label)
                grouped[key] = grouped.get(key, Decimal("0")) + (expense.amount or Decimal("0"))
            for (day, mode_value, account_label), amount_value in sorted(grouped.items(), key=lambda item: item[0][0], reverse=True)[:200]:
                append_summary(day, amount_value, mode_value, account_label)

        elif report_type == "salaries":
            chart_title = "Salaries"
            chart_series_label = "Salaries paid"
            chart_kind = "bar"
            salaries_dash = Salary.objects.filter(
                hospital=hospital,
                paid=True,
                paid_at__gte=period_start,
                paid_at__lte=period_end,
            ).select_related("employee")
            daily = salaries_dash.values("paid_at").annotate(total=Sum("amount")).order_by("paid_at")
            chart_labels = [row["paid_at"].isoformat() for row in daily if row["paid_at"]]
            chart_values = [str(row["total"] or 0) for row in daily]

            grouped = {}
            for salary in salaries_dash.order_by("-paid_at", "-id")[:800]:
                if not salary.paid_at:
                    continue
                key = (salary.paid_at, "salary", salary.employee.get_full_name() or salary.employee.username)
                grouped[key] = grouped.get(key, Decimal("0")) + (salary.amount or Decimal("0"))
            for (day, mode_value, account_label), amount_value in sorted(grouped.items(), key=lambda item: item[0][0], reverse=True)[:200]:
                append_summary(day, amount_value, mode_value, account_label)

        elif report_type == "net_profit":
            chart_title = "Net Profit"
            chart_series_label = "Net profit"
            chart_kind = "bar"

            payments_dash = (
                payments.filter(paid_at__date__gte=period_start, paid_at__date__lte=period_end)
                .exclude(status=Payment.STATUS_WAIVED)
                .exclude(paid_at__isnull=True)
            )
            expenses_dash = Expense.objects.filter(hospital=hospital, date__gte=period_start, date__lte=period_end)
            salaries_dash = Salary.objects.filter(hospital=hospital, paid=True, paid_at__gte=period_start, paid_at__lte=period_end)

            income_daily = {
                row["day"]: (row["total"] or Decimal("0"))
                for row in payments_dash.annotate(day=TruncDate("paid_at")).values("day").annotate(total=Sum("amount_paid"))
            }
            exp_daily = {
                row["date"]: (row["total"] or Decimal("0"))
                for row in expenses_dash.values("date").annotate(total=Sum("amount"))
            }
            sal_daily = {
                row["paid_at"]: (row["total"] or Decimal("0"))
                for row in salaries_dash.values("paid_at").annotate(total=Sum("amount"))
            }

            cursor = period_start
            while cursor <= period_end:
                income = income_daily.get(cursor, Decimal("0"))
                expense_amt = exp_daily.get(cursor, Decimal("0"))
                salary_amt = sal_daily.get(cursor, Decimal("0"))
                net = income - (expense_amt + salary_amt)
                chart_labels.append(cursor.isoformat())
                chart_values.append(str(net))
                cursor += timedelta(days=1)

            # Summary: show recent days, newest first.
            for label, value in zip(reversed(chart_labels), reversed(chart_values)):
                append_summary(timezone.datetime.fromisoformat(label).date(), Decimal(value), "net_profit", "Income - Expenses - Salaries")
                if len(summary_rows) >= 60:
                    break

    context = {
        "active_nav": "hospital_financials",
        "dashboard_title": "Financial Report",
        "dashboard_intro": "Track realized income, outgoing costs, and the running hospital balance.",
        "hospital": hospital,
        "today": today,
        "month_start": month_start,
        "paid_income": paid_income,
        "paid_income_today": payments_today.aggregate(total=Sum("amount_paid"))["total"] or 0,
        "paid_income_month": payments_month.aggregate(total=Sum("amount_paid"))["total"] or 0,
        "receipt_count_today": payments_today.count(),
        "receipt_count_month": payments_month.count(),
        "period_start": period_start,
        "period_end": period_end,
        "income_period": income_period,
        "expenses_period": expenses_period,
        "salaries_period": salaries_period,
        "net_profit_period": income_period - (expenses_period + salaries_period),
        "reconciliation_discrepancy_total": reconciliation_discrepancy_total,
        "expense_total": expense_total,
        "salary_total": salary_total,
        "net_profit": paid_income - (expense_total + salary_total),
        "account_balance": account.balance if account else 0,
        "pending_payments": payments.exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).count(),
        "expense_items": (
            Expense.objects.filter(hospital=hospital)
            .select_related("bank_account", "mobile_money_account", "cash_drawer")
            .order_by("-date", "-id")[:10]
            if hospital
            else []
        ),
        "salary_items": Salary.objects.filter(hospital=hospital).select_related("employee").order_by("-month", "-id")[:10] if hospital else [],
        "low_stock_items": InventoryItem.objects.filter(hospital=hospital, quantity__lte=models.F("low_stock_threshold")).order_by("quantity", "name")[:10] if hospital else [],
        "part_paid_count": payments.filter(status=Payment.STATUS_PART_PAID).count(),
        "outstanding_balance": (payments.aggregate(total=Sum(models.F("amount") - models.F("amount_paid")))["total"] or 0),
        "open_drawer": open_drawer,
        "open_drawer_cash_in": open_drawer_cash_in,
        "open_drawer_cash_out": open_drawer_cash_out,
        "open_drawer_expected": open_drawer_expected,
        "last_closed_drawer": last_closed_drawer,
        "recent_receipts": recent_receipts,
        "recent_bank_statement": recent_bank_statement,
        "recent_three_way_statement": recent_three_way_statement,
        "recent_mobile_statement": recent_mobile_statement,
        "recent_bank_variance": bank_variance,
        "recent_mobile_variance": mobile_variance,
        "unreconciled_bank_count": unreconciled_bank_count,
        "unreconciled_mobile_count": unreconciled_mobile_count,
        "unreconciled_bank_total": unreconciled_bank_total,
        "unreconciled_mobile_total": unreconciled_mobile_total,
        "report_type": report_type,
        "report_type_options": report_type_options,
        "payment_mode": payment_mode,
        "bank_account_id": bank_account_id,
        "mobile_account_id": mobile_account_id,
        "bank_accounts": bank_accounts,
        "mobile_accounts": mobile_accounts,
        "chart_title": chart_title,
        "chart_series_label": chart_series_label,
        "chart_kind": chart_kind,
        "chart_labels_json": json.dumps(chart_labels),
        "chart_values_json": json.dumps(chart_values),
        "summary_rows": summary_rows,
    }
    return render(request, "admin_dashboard/financial_report.html", context)


@hospital_admin_only
def financial_statements(request):
    """Landing page for the three core statements: Bank, Mobile Money, and Cash Drawer."""
    hospital = active_hospital(request)
    from reception.models import Payment

    today = timezone.localdate()
    period_start = request.GET.get("start") or today.replace(day=1).isoformat()
    period_end = request.GET.get("end") or today.isoformat()

    cash_date = request.GET.get("cash_date") or today.isoformat()

    bank_account_id = (request.GET.get("bank_account") or "").strip()
    mobile_account_id = (request.GET.get("mobile_account") or "").strip()

    open_drawer = CashDrawer.objects.filter(hospital=hospital, date=cash_date).order_by("-id").first() if hospital else None
    last_closed_drawer = (
        CashDrawer.objects.filter(hospital=hospital, closing_balance__isnull=False).order_by("-date", "-id").first() if hospital else None
    )

    latest_bank = (
        ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_BANK)
        .select_related("bank_account", "generated_by")
        .first()
        if hospital
        else None
    )
    latest_mobile = (
        ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_MOBILE_MONEY)
        .select_related("mobile_money_account", "generated_by")
        .first()
        if hospital
        else None
    )

    unreconciled_bank = (
        BankTransaction.objects.filter(bank_account__hospital=hospital, transaction_type=BankTransaction.TYPE_CREDIT, is_reconciled=False).count()
        if hospital
        else 0
    )
    unreconciled_mobile = (
        MobileMoneyTransaction.objects.filter(
            mobile_money_account__hospital=hospital,
            transaction_type=MobileMoneyTransaction.TYPE_CREDIT,
            is_reconciled=False,
        ).count()
        if hospital
        else 0
    )

    bank_variance = (latest_bank.total_deposits - latest_bank.reconciled_balance) if latest_bank else Decimal("0")
    mobile_variance = (latest_mobile.total_deposits - latest_mobile.reconciled_balance) if latest_mobile else Decimal("0")
    cash_discrepancy = last_closed_drawer.discrepancy if last_closed_drawer and last_closed_drawer.discrepancy is not None else Decimal("0")
    variance_total = abs(bank_variance) + abs(mobile_variance) + abs(cash_discrepancy)

    # Internal receipts (source of truth for "what was collected")
    payments = Payment.objects.filter(visit__hospital=hospital).exclude(status=Payment.STATUS_WAIVED) if hospital else Payment.objects.none()

    cash_receipts = (
        payments.filter(mode=Payment.MODE_CASH, paid_at__date__gte=period_start, paid_at__date__lte=period_end)
        .select_related("visit__patient", "recorded_by")
        .order_by("-paid_at", "-id")
    )
    bank_receipts = (
        payments.filter(mode=Payment.MODE_CARD, paid_at__date__gte=period_start, paid_at__date__lte=period_end)
        .select_related("visit__patient", "bank_account", "recorded_by")
        .order_by("-paid_at", "-id")
    )
    if bank_account_id:
        bank_receipts = bank_receipts.filter(bank_account_id=bank_account_id)

    mobile_receipts = (
        payments.filter(mode=Payment.MODE_MOBILE_MONEY, paid_at__date__gte=period_start, paid_at__date__lte=period_end)
        .select_related("visit__patient", "mobile_account", "recorded_by")
        .order_by("-paid_at", "-id")
    )
    if mobile_account_id:
        mobile_receipts = mobile_receipts.filter(mobile_account_id=mobile_account_id)

    cash_total = cash_receipts.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")
    bank_total = bank_receipts.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")
    mobile_total = mobile_receipts.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")

    # Daily cash statement numbers (expected vs actual)
    cash_day = cash_date
    cash_in_day = (
        payments.filter(mode=Payment.MODE_CASH, paid_at__date=cash_day).aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")
        if hospital
        else Decimal("0")
    )
    cash_out_day = (
        Expense.objects.filter(hospital=hospital, source=Expense.SOURCE_CASH_DRAWER, date=cash_day).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
        if hospital
        else Decimal("0")
    )
    opening_balance = Decimal("0")
    prior = (
        CashDrawer.objects.filter(hospital=hospital, date__lt=cash_day, closing_balance__isnull=False).order_by("-date", "-id").first()
        if hospital
        else None
    )
    if prior and prior.closing_balance is not None:
        opening_balance = prior.closing_balance
    expected_closing = opening_balance + cash_in_day - cash_out_day
    actual_closing = open_drawer.closing_balance if open_drawer else None
    computed_discrepancy = (actual_closing - expected_closing) if (actual_closing is not None) else None

    bank_accounts = BankAccount.objects.filter(hospital=hospital, is_active=True).order_by("bank_name", "account_name") if hospital else BankAccount.objects.none()
    mobile_accounts = MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True).order_by("provider", "number") if hospital else MobileMoneyAccount.objects.none()

    if request.method == "POST" and hospital:
        if request.POST.get("action") == "close_cash_day":
            close_date = request.POST.get("cash_date") or cash_day
            closing_balance = Decimal(request.POST.get("closing_balance") or "0")
            drawer = CashDrawer.objects.filter(hospital=hospital, date=close_date).order_by("-id").first()
            if not drawer:
                drawer = CashDrawer.objects.create(hospital=hospital, date=close_date, opening_balance=opening_balance)
            drawer.opening_balance = opening_balance
            drawer.expected_closing = expected_closing
            drawer.closing_balance = closing_balance
            drawer.discrepancy = closing_balance - expected_closing
            drawer.closed_by = request.user
            drawer.closed_at = timezone.now()
            drawer.save(
                update_fields=[
                    "opening_balance",
                    "expected_closing",
                    "closing_balance",
                    "discrepancy",
                    "closed_by",
                    "closed_at",
                ]
            )
            messages.success(request, "Cash day closing balance recorded.")
            return redirect(f"{reverse('financial_statements')}?start={period_start}&end={period_end}&cash_date={close_date}")

    context = finance_context(
        request,
        "hospital_financial_statements",
        "Financial Statements",
        "Bank, mobile money, and cash drawer statements in one place for quick reconciliation.",
    )
    context.update(
        {
            "open_drawer": open_drawer,
            "last_closed_drawer": last_closed_drawer,
            "latest_bank": latest_bank,
            "latest_mobile": latest_mobile,
            "unreconciled_bank": unreconciled_bank,
            "unreconciled_mobile": unreconciled_mobile,
            "bank_variance": bank_variance,
            "mobile_variance": mobile_variance,
            "cash_discrepancy": cash_discrepancy,
            "variance_total": variance_total,
            "period_start": period_start,
            "period_end": period_end,
            "cash_date": cash_day,
            "bank_accounts": bank_accounts,
            "mobile_accounts": mobile_accounts,
            "bank_account_id": bank_account_id,
            "mobile_account_id": mobile_account_id,
            "cash_total": cash_total,
            "bank_total": bank_total,
            "mobile_total": mobile_total,
            "cash_receipts": cash_receipts[:25],
            "bank_receipts": bank_receipts[:25],
            "mobile_receipts": mobile_receipts[:25],
            "cash_opening_balance": opening_balance,
            "cash_in_day": cash_in_day,
            "cash_out_day": cash_out_day,
            "cash_expected_closing": expected_closing,
            "cash_actual_closing": actual_closing,
            "cash_computed_discrepancy": computed_discrepancy,
        }
    )
    return render(request, "admin_dashboard/financial_statements.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def manage_users(request):
    hospital = active_hospital(request)
    users = hospital.users.order_by("role", "first_name", "username") if hospital else User.objects.none()

    if request.method == "POST":
        form = HospitalStaffUserForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.hospital = hospital
            user.save()
            form.save_m2m()
            messages.success(request, f"{user.get_full_name() or user.username} added successfully.")
            return redirect("manage_users")
        messages.error(request, "Please fix the user details below.")
    else:
        form = HospitalStaffUserForm()

    context = hospital_admin_context(
        request,
        "hospital_users",
        "Hospital Users",
        "Create and review operational user accounts for this hospital.",
    )
    context.update({"users": users, "form": form})
    return render(request, "admin_dashboard/manage_users.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def edit_user(request, user_id):
    user = hospital_owned_or_404(User, request, pk=user_id)
    form = HospitalStaffUserUpdateForm(request.POST or None, instance=user)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            form.save_m2m()
            messages.success(request, f"{user.get_full_name() or user.username} updated.")
            return redirect("manage_users")
        messages.error(request, "Please fix the user details below.")

    context = hospital_admin_context(
        request,
        "hospital_users",
        "Edit Hospital User",
        "Update role, contact details, and active status for this team member.",
    )
    context.update({"form": form, "object_label": user.get_full_name() or user.username, "cancel_url": "manage_users"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def deactivate_user(request, user_id):
    user = hospital_owned_or_404(User, request, pk=user_id)
    if user == request.user:
        messages.error(request, "You cannot deactivate your own account from this screen.")
        return redirect("manage_users")

    if request.method == "POST":
        user.is_active = False
        user.save(update_fields=["is_active"])
        messages.success(request, f"{user.get_full_name() or user.username} has been deactivated.")
        return redirect("manage_users")

    context = hospital_admin_context(
        request,
        "hospital_users",
        "Deactivate Hospital User",
        "Deactivate this user while keeping operational history and payroll links intact.",
    )
    context.update(
        {
            "object_label": user.get_full_name() or user.username,
            "object_type": "user",
            "confirm_label": "Deactivate User",
            "cancel_url": "manage_users",
            "danger_note": "This is a safety-first removal. The account will be inactive but historical records will stay linked.",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def manage_services(request):
    hospital = active_hospital(request)
    services = Service.objects.filter(hospital=hospital).order_by("category", "name") if hospital else Service.objects.none()

    if request.method == "POST":
        form = HospitalServiceForm(request.POST)
        if form.is_valid():
            service = form.save(commit=False)
            service.hospital = hospital
            service.save()
            messages.success(request, f"Service '{service.name}' saved.")
            return redirect("manage_services")
        messages.error(request, "Please fix the service details below.")
    else:
        form = HospitalServiceForm()

    context = hospital_admin_context(
        request,
        "hospital_services",
        "Services and Prices",
        "Configure the services this hospital offers and what each one costs.",
    )
    context.update({"services": services, "form": form})
    return render(request, "admin_dashboard/manage_services.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def edit_service(request, service_id):
    service = hospital_owned_or_404(Service, request, pk=service_id)
    form = HospitalServiceForm(request.POST or None, instance=service)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Service '{service.name}' updated.")
            return redirect("manage_services")
        messages.error(request, "Please fix the service details below.")

    context = hospital_admin_context(
        request,
        "hospital_services",
        "Edit Service",
        "Adjust pricing, category, or activation state without losing the service history already linked to visits.",
    )
    context.update({"form": form, "object_label": service.name, "cancel_url": "manage_services"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def delete_service(request, service_id):
    service = hospital_owned_or_404(Service, request, pk=service_id)
    if request.method == "POST":
        service_name = service.name
        service.delete()
        messages.success(request, f"Service '{service_name}' deleted.")
        return redirect("manage_services")

    context = hospital_admin_context(
        request,
        "hospital_services",
        "Delete Service",
        "Remove this service definition if it is no longer needed.",
    )
    context.update(
        {
            "object_label": service.name,
            "object_type": "service",
            "confirm_label": "Delete Service",
            "cancel_url": "manage_services",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@hospital_admin_only
def manage_expenses(request):
    hospital = active_hospital(request)
    expenses = (
        Expense.objects.filter(hospital=hospital)
        .select_related("bank_account", "mobile_money_account", "cash_drawer")
        .order_by("-date", "-id")
        if hospital
        else Expense.objects.none()
    )

    if request.method == "POST":
        form = ExpenseForm(request.POST, hospital=hospital)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.hospital = hospital
            expense.save()
            messages.success(request, f"Expense '{expense.description}' recorded.")
            return redirect("manage_expenses")
        messages.error(request, "Please fix the expense details below.")
    else:
        form = ExpenseForm(hospital=hospital)

    context = hospital_admin_context(
        request,
        "hospital_expenses",
        "Expenses",
        "Track operational costs such as rent, utilities, consumables, and other outflows.",
    )
    context.update({"expenses": expenses[:20], "form": form})
    return render(request, "admin_dashboard/manage_expenses.html", context)


@hospital_admin_only
def edit_expense(request, expense_id):
    expense = hospital_owned_or_404(Expense, request, pk=expense_id)
    form = ExpenseForm(request.POST or None, instance=expense, hospital=active_hospital(request))
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Expense '{expense.description}' updated.")
            return redirect("manage_expenses")
        messages.error(request, "Please fix the expense details below.")

    context = hospital_admin_context(
        request,
        "hospital_expenses",
        "Edit Expense",
        "Correct the category, description, or amount for this recorded expense.",
    )
    context.update({"form": form, "object_label": expense.description, "cancel_url": "manage_expenses"})
    return render(request, "admin_dashboard/object_form.html", context)


@hospital_admin_only
def delete_expense(request, expense_id):
    expense = hospital_owned_or_404(Expense, request, pk=expense_id)
    if request.method == "POST":
        expense_label = expense.description
        expense.delete()
        messages.success(request, f"Expense '{expense_label}' deleted.")
        return redirect("manage_expenses")

    context = hospital_admin_context(
        request,
        "hospital_expenses",
        "Delete Expense",
        "Remove this expense entry and recalculate the hospital balance.",
    )
    context.update(
        {
            "object_label": expense.description,
            "object_type": "expense",
            "confirm_label": "Delete Expense",
            "cancel_url": "manage_expenses",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def manage_salaries(request):
    hospital = active_hospital(request)
    salaries = Salary.objects.filter(hospital=hospital).select_related("employee").order_by("-month", "-id") if hospital else Salary.objects.none()

    if request.method == "POST":
        form = SalaryForm(request.POST, hospital=hospital)
        if form.is_valid():
            salary = form.save(commit=False)
            salary.hospital = hospital
            salary.save()
            messages.success(request, f"Salary record for {salary.employee} saved.")
            return redirect("manage_salaries")
        messages.error(request, "Please fix the salary details below.")
    else:
        form = SalaryForm(hospital=hospital)

    context = hospital_admin_context(
        request,
        "hospital_salaries",
        "Salaries",
        "Track payroll obligations and paid salary entries for hospital staff.",
    )
    context.update({"salaries": salaries[:20], "form": form})
    return render(request, "admin_dashboard/manage_salaries.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def edit_salary(request, salary_id):
    salary = hospital_owned_or_404(Salary, request, pk=salary_id)
    form = SalaryForm(request.POST or None, instance=salary, hospital=active_hospital(request))
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Salary record for {salary.employee} updated.")
            return redirect("manage_salaries")
        messages.error(request, "Please fix the salary details below.")

    context = hospital_admin_context(
        request,
        "hospital_salaries",
        "Edit Salary Record",
        "Update payroll status, amount, or notes for this salary entry.",
    )
    context.update({"form": form, "object_label": str(salary.employee), "cancel_url": "manage_salaries"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def delete_salary(request, salary_id):
    salary = hospital_owned_or_404(Salary, request, pk=salary_id)
    if request.method == "POST":
        salary_label = str(salary.employee)
        salary.delete()
        messages.success(request, f"Salary record for {salary_label} deleted.")
        return redirect("manage_salaries")

    context = hospital_admin_context(
        request,
        "hospital_salaries",
        "Delete Salary Record",
        "Remove this salary entry and update the hospital balance if it had been marked as paid.",
    )
    context.update(
        {
            "object_label": str(salary.employee),
            "object_type": "salary record",
            "confirm_label": "Delete Salary Record",
            "cancel_url": "manage_salaries",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def manage_inventory(request):
    hospital = active_hospital(request)
    inventory_items = InventoryItem.objects.filter(hospital=hospital).order_by("name") if hospital else InventoryItem.objects.none()

    if request.method == "POST":
        form = InventoryItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.hospital = hospital
            item.save()
            messages.success(request, f"Inventory item '{item.name}' saved.")
            return redirect("manage_inventory")
        messages.error(request, "Please fix the inventory details below.")
    else:
        form = InventoryItemForm()

    context = hospital_admin_context(
        request,
        "hospital_inventory",
        "Inventory",
        "Maintain stock visibility and watch low-stock items before they disrupt care.",
    )
    context.update({"inventory_items": inventory_items, "form": form})
    return render(request, "admin_dashboard/manage_inventory.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def edit_inventory_item(request, item_id):
    item = hospital_owned_or_404(InventoryItem, request, pk=item_id)
    form = InventoryItemForm(request.POST or None, instance=item)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Inventory item '{item.name}' updated.")
            return redirect("manage_inventory")
        messages.error(request, "Please fix the inventory details below.")

    context = hospital_admin_context(
        request,
        "hospital_inventory",
        "Edit Inventory Item",
        "Update stock counts, pricing, or thresholds for this inventory item.",
    )
    context.update({"form": form, "object_label": item.name, "cancel_url": "manage_inventory"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def delete_inventory_item(request, item_id):
    item = hospital_owned_or_404(InventoryItem, request, pk=item_id)
    if request.method == "POST":
        item_name = item.name
        item.delete()
        messages.success(request, f"Inventory item '{item_name}' deleted.")
        return redirect("manage_inventory")

    context = hospital_admin_context(
        request,
        "hospital_inventory",
        "Delete Inventory Item",
        "Remove this inventory record if it is no longer needed.",
    )
    context.update(
        {
            "object_label": item.name,
            "object_type": "inventory item",
            "confirm_label": "Delete Inventory Item",
            "cancel_url": "manage_inventory",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@hospital_admin_only
def bank_account_list(request):
    hospital = active_hospital(request)
    accounts = BankAccount.objects.filter(hospital=hospital).order_by("bank_name", "account_name") if hospital else BankAccount.objects.none()
    context = finance_context(
        request,
        "hospital_bank_accounts",
        "Bank Accounts",
        "Manage the hospital bank accounts used for reconciliation and card settlement tracking.",
    )
    context.update({"accounts": accounts})
    return render(request, "admin_dashboard/bank_account_list.html", context)


@hospital_admin_only
def bank_account_create(request):
    hospital = active_hospital(request)
    form = BankAccountForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            account = form.save(commit=False)
            account.hospital = hospital
            account.save()
            messages.success(request, f"Bank account '{account.account_name}' added.")
            return redirect("bank_account_list")
        messages.error(request, "Please correct the bank account details below.")

    context = finance_context(
        request,
        "hospital_bank_accounts",
        "Add Bank Account",
        "Register a bank account for this hospital before reconciling deposits and withdrawals.",
    )
    context.update({"form": form, "cancel_url": "bank_account_list"})
    return render(request, "admin_dashboard/object_form.html", context)


@hospital_admin_only
def edit_bank_account(request, account_id):
    account = hospital_owned_or_404(BankAccount, request, pk=account_id)
    form = BankAccountForm(request.POST or None, instance=account)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Bank account '{account.account_name}' updated.")
            return redirect("bank_account_list")
        messages.error(request, "Please correct the bank account details below.")

    context = finance_context(
        request,
        "hospital_bank_accounts",
        "Edit Bank Account",
        "Update account details or activation state for this hospital bank account.",
    )
    context.update({"form": form, "object_label": account.account_name, "cancel_url": "bank_account_list"})
    return render(request, "admin_dashboard/object_form.html", context)


@hospital_admin_only
def delete_bank_account(request, account_id):
    account = hospital_owned_or_404(BankAccount, request, pk=account_id)
    if request.method == "POST":
        account_label = str(account)
        account.delete()
        messages.success(request, f"Bank account '{account_label}' deleted.")
        return redirect("bank_account_list")

    context = finance_context(
        request,
        "hospital_bank_accounts",
        "Delete Bank Account",
        "Remove this bank account and any transactions recorded against it.",
    )
    context.update(
        {
            "object_label": str(account),
            "object_type": "bank account",
            "confirm_label": "Delete Bank Account",
            "cancel_url": "bank_account_list",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@hospital_admin_only
def bank_account_detail(request, account_id):
    account = hospital_owned_or_404(BankAccount, request, pk=account_id)
    transaction_form = BankTransactionForm(request.POST or None, hospital=active_hospital(request))
    if request.method == "POST":
        if transaction_form.is_valid():
            bank_transaction = transaction_form.save(commit=False)
            bank_transaction.bank_account = account
            if not bank_transaction.reconciled_with_id and bank_transaction.reference:
                matched = payment_from_receipt_reference(bank_transaction.reference, active_hospital(request))
                if matched:
                    bank_transaction.reconciled_with = matched
            bank_transaction.is_reconciled = bool(bank_transaction.reconciled_with_id)
            bank_transaction.save()
            messages.success(request, "Bank transaction recorded.")
            return redirect("bank_account_detail", account_id=account.pk)
        messages.error(request, "Please correct the bank transaction details below.")

    context = finance_context(
        request,
        "hospital_bank_accounts",
        "Bank Account Detail",
        "Review statement lines and record new bank transactions for reconciliation.",
    )
    context.update(
        {
            "account": account,
            "transaction_form": transaction_form,
            "transactions": account.transactions.select_related("reconciled_with__visit__patient"),
        }
    )
    return render(request, "admin_dashboard/bank_account_detail.html", context)


@hospital_admin_only
def mobile_money_list(request):
    hospital = active_hospital(request)
    accounts = MobileMoneyAccount.objects.filter(hospital=hospital).order_by("provider", "number") if hospital else MobileMoneyAccount.objects.none()
    context = finance_context(
        request,
        "hospital_mobile_money",
        "Mobile Money Accounts",
        "Track the active mobile money numbers used to receive hospital payments.",
    )
    context.update({"accounts": accounts})
    return render(request, "admin_dashboard/mobile_money_list.html", context)


@hospital_admin_only
def mobile_money_create(request):
    hospital = active_hospital(request)
    form = MobileMoneyAccountForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            account = form.save(commit=False)
            account.hospital = hospital
            account.save()
            messages.success(request, f"Mobile money number '{account.number}' added.")
            return redirect("mobile_money_list")
        messages.error(request, "Please correct the mobile money details below.")

    context = finance_context(
        request,
        "hospital_mobile_money",
        "Add Mobile Money Account",
        "Register a payment number so reconciliations and receipts stay tied to known channels.",
    )
    context.update({"form": form, "cancel_url": "mobile_money_list"})
    return render(request, "admin_dashboard/object_form.html", context)


@hospital_admin_only
def edit_mobile_money(request, account_id):
    account = hospital_owned_or_404(MobileMoneyAccount, request, pk=account_id)
    form = MobileMoneyAccountForm(request.POST or None, instance=account)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Mobile money number '{account.number}' updated.")
            return redirect("mobile_money_list")
        messages.error(request, "Please correct the mobile money details below.")

    context = finance_context(
        request,
        "hospital_mobile_money",
        "Edit Mobile Money Account",
        "Update provider, number, or activation state for this mobile money account.",
    )
    context.update({"form": form, "object_label": account.number, "cancel_url": "mobile_money_list"})
    return render(request, "admin_dashboard/object_form.html", context)


@hospital_admin_only
def delete_mobile_money(request, account_id):
    account = hospital_owned_or_404(MobileMoneyAccount, request, pk=account_id)
    if request.method == "POST":
        account_label = str(account)
        account.delete()
        messages.success(request, f"Mobile money account '{account_label}' deleted.")
        return redirect("mobile_money_list")

    context = finance_context(
        request,
        "hospital_mobile_money",
        "Delete Mobile Money Account",
        "Remove this mobile money payment channel from the hospital finance setup.",
    )
    context.update(
        {
            "object_label": str(account),
            "object_type": "mobile money account",
            "confirm_label": "Delete Mobile Money Account",
            "cancel_url": "mobile_money_list",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@hospital_admin_only
def mobile_money_account_detail(request, account_id):
    account = hospital_owned_or_404(MobileMoneyAccount, request, pk=account_id)
    transaction_form = MobileMoneyTransactionForm(request.POST or None, hospital=active_hospital(request))

    if request.method == "POST":
        if transaction_form.is_valid():
            txn = transaction_form.save(commit=False)
            txn.mobile_money_account = account
            if not txn.reconciled_with_id and txn.reference:
                matched = payment_from_receipt_reference(
                    txn.reference,
                    active_hospital(request),
                    mode=Payment.MODE_MOBILE_MONEY,
                )
                if matched:
                    txn.reconciled_with = matched
            txn.is_reconciled = bool(txn.reconciled_with_id)
            txn.save()
            messages.success(request, "Mobile money transaction recorded.")
            return redirect("mobile_money_account_detail", account_id=account.pk)
        messages.error(request, "Please correct the mobile money transaction details below.")

    context = finance_context(
        request,
        "hospital_mobile_money",
        "Mobile Money Detail",
        "Review statement lines and record new mobile money transactions for reconciliation.",
    )
    context.update(
        {
            "account": account,
            "transaction_form": transaction_form,
            "transactions": account.transactions.select_related("reconciled_with__visit__patient"),
        }
    )
    return render(request, "admin_dashboard/mobile_money_detail.html", context)


@hospital_admin_only
def cash_drawer_list(request):
    hospital = active_hospital(request)
    drawers = CashDrawer.objects.filter(hospital=hospital).order_by("-date", "-id") if hospital else CashDrawer.objects.none()
    open_drawer = drawers.filter(closed_at__isnull=True).first() if hospital else None
    context = finance_context(
        request,
        "hospital_cash_drawer",
        "Cash Drawer",
        "Open, track, and close the daily cash drawer while monitoring expected cash and discrepancies.",
    )
    context.update({"drawers": drawers, "open_drawer": open_drawer})
    return render(request, "admin_dashboard/cash_drawer_list.html", context)


@hospital_admin_only
def open_cash_drawer(request):
    hospital = active_hospital(request)
    open_drawer = CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=True).first() if hospital else None
    if open_drawer:
        messages.info(request, "There is already an open cash drawer for this hospital.")
        return redirect("cash_drawer_detail", pk=open_drawer.pk)

    form = OpenCashDrawerForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            drawer = CashDrawer.objects.create(
                hospital=hospital,
                opening_balance=form.cleaned_data["opening_balance"],
            )
            messages.success(request, "Cash drawer opened.")
            return redirect("cash_drawer_detail", pk=drawer.pk)
        messages.error(request, "Please enter a valid opening balance.")

    context = finance_context(
        request,
        "hospital_cash_drawer",
        "Open Cash Drawer",
        "Start the day by recording the opening cash balance.",
    )
    context.update({"form": form, "cancel_url": "cash_drawer_list"})
    return render(request, "admin_dashboard/object_form.html", context)


@hospital_admin_only
def cash_drawer_detail(request, pk):
    drawer = hospital_owned_or_404(CashDrawer, request, pk=pk)
    cash_in = drawer.transactions.filter(transaction_type=CashTransaction.TYPE_CASH_IN).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    cash_out = drawer.transactions.filter(transaction_type=CashTransaction.TYPE_CASH_OUT).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    expected = drawer.opening_balance + cash_in - cash_out
    transaction_form = CashTransactionForm(prefix="txn")
    close_form = CloseCashDrawerForm(prefix="close")

    if request.method == "POST":
        if "add_transaction" in request.POST:
            transaction_form = CashTransactionForm(request.POST, prefix="txn")
            if transaction_form.is_valid():
                transaction = transaction_form.save(commit=False)
                transaction.cash_drawer = drawer
                transaction.save()
                messages.success(request, "Cash drawer transaction added.")
                return redirect("cash_drawer_detail", pk=drawer.pk)
            messages.error(request, "Please correct the cash transaction below.")
        elif "close_drawer" in request.POST:
            close_form = CloseCashDrawerForm(request.POST, prefix="close")
            if close_form.is_valid():
                closing = close_form.cleaned_data["closing_balance"]
                drawer.closing_balance = closing
                drawer.expected_closing = expected
                drawer.discrepancy = closing - expected
                drawer.closed_by = request.user
                drawer.closed_at = timezone.now()
                drawer.save(update_fields=["closing_balance", "expected_closing", "discrepancy", "closed_by", "closed_at"])
                messages.success(request, "Cash drawer closed.")
                return redirect("cash_drawer_list")
            messages.error(request, "Please provide a valid closing balance.")

    context = finance_context(
        request,
        "hospital_cash_drawer",
        "Cash Drawer Detail",
        "Track cash-in and cash-out movements, then close the drawer against the expected balance.",
    )
    context.update(
        {
            "drawer": drawer,
            "cash_in": cash_in,
            "cash_out": cash_out,
            "expected_closing": expected,
            "transaction_form": transaction_form,
            "close_form": close_form,
        }
    )
    return render(request, "admin_dashboard/cash_drawer_detail.html", context)


@hospital_admin_only
def receipts_list(request):
    hospital = active_hospital(request)
    bank_accounts = BankAccount.objects.filter(hospital=hospital, is_active=True).order_by("bank_name", "account_name") if hospital else BankAccount.objects.none()
    mobile_accounts = (
        MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True).order_by("provider", "number")
        if hospital
        else MobileMoneyAccount.objects.none()
    )
    payments = (
        Payment.objects.filter(visit__hospital=hospital)
        .select_related("visit__patient", "recorded_by", "bank_account", "mobile_account")
        .prefetch_related("visit__visit_services__service", "bank_transactions", "mobile_money_transactions")
        .order_by("-paid_at", "-id")
        if hospital
        else Payment.objects.none()
    )

    q = request.GET.get("q", "").strip()
    mode = request.GET.get("mode", "").strip()
    start = request.GET.get("start_date", "").strip()
    end = request.GET.get("end_date", "").strip()
    reconciled = request.GET.get("reconciled", "").strip()
    bank_account_id = request.GET.get("bank_account", "").strip()
    mobile_account_id = request.GET.get("mobile_account", "").strip()

    if q:
        payments = payments.filter(visit__patient__name__icontains=q)
    if mode:
        payments = payments.filter(mode=mode)
    if start:
        payments = payments.filter(paid_at__date__gte=start)
    if end:
        payments = payments.filter(paid_at__date__lte=end)
    if reconciled == "yes":
        payments = payments.filter(Q(bank_transactions__isnull=False) | Q(mobile_money_transactions__isnull=False)).distinct()
    elif reconciled == "no":
        payments = payments.filter(bank_transactions__isnull=True, mobile_money_transactions__isnull=True)
    if bank_account_id:
        payments = payments.filter(mode=Payment.MODE_CARD, bank_account_id=bank_account_id)
    if mobile_account_id:
        payments = payments.filter(mode=Payment.MODE_MOBILE_MONEY, mobile_account_id=mobile_account_id)

    context = finance_context(
        request,
        "hospital_receipts",
        "Receipts",
        "Review the full payment trail with filters for payment mode, date range, and reconciliation status.",
    )
    context.update(
        {
            "payments": payments,
            "q": q,
            "mode": mode,
            "start_date": start,
            "end_date": end,
            "reconciled": reconciled,
            "bank_account_id": bank_account_id,
            "mobile_account_id": mobile_account_id,
            "bank_accounts": bank_accounts,
            "mobile_accounts": mobile_accounts,
            "payment_modes": Payment.MODE_CHOICES,
        }
    )
    return render(request, "admin_dashboard/receipts_list.html", context)


@hospital_admin_only
def bank_reconciliation(request):
    hospital = active_hospital(request)
    form = BankReconciliationForm(request.POST or None, hospital=hospital)
    recent_statements = ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_BANK).select_related("bank_account", "generated_by")[:8] if hospital else ReconciliationStatement.objects.none()
    selected_bank = None
    if request.method == "POST":
        selected_bank = form.data.get("bank_account")
    elif request.GET.get("bank_account"):
        selected_bank = request.GET.get("bank_account")
    internal_payments = Payment.objects.none()
    if selected_bank and hospital:
        internal_payments = (
            Payment.objects.filter(
                visit__hospital=hospital,
                mode=Payment.MODE_CARD,
                bank_account_id=selected_bank,
            )
            .select_related("visit__patient", "bank_account")
            .order_by("-paid_at", "-id")[:100]
        )

    if request.method == "POST":
        if form.is_valid():
            account = form.cleaned_data["bank_account"]
            period_start = form.cleaned_data["period_start"]
            period_end = form.cleaned_data["period_end"]

            internal_total = (
                Payment.objects.filter(
                    visit__hospital=hospital,
                    paid_at__date__gte=period_start,
                    paid_at__date__lte=period_end,
                    mode__in=[Payment.MODE_CARD, Payment.MODE_MOBILE_MONEY],
                ).aggregate(total=Sum("amount_paid"))["total"]
                or Decimal("0")
            )

            bank_txns = BankTransaction.objects.filter(
                bank_account=account,
                transaction_date__gte=period_start,
                transaction_date__lte=period_end,
            )
            deposits = bank_txns.filter(transaction_type=BankTransaction.TYPE_CREDIT).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            withdrawals = bank_txns.filter(transaction_type=BankTransaction.TYPE_DEBIT).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            outstanding = bank_txns.filter(is_reconciled=False).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            statement = ReconciliationStatement.objects.create(
                hospital=hospital,
                statement_type=ReconciliationStatement.TYPE_BANK,
                period_start=period_start,
                period_end=period_end,
                bank_account=account,
                opening_balance=account.opening_balance,
                total_deposits=deposits,
                total_withdrawals=withdrawals,
                outstanding_checks=outstanding,
                closing_balance=account.opening_balance + deposits - withdrawals,
                reconciled_balance=internal_total,
                generated_by=request.user,
            )
            messages.success(request, "Bank statement generated.")
            return redirect("reconciliation_detail", pk=statement.pk)
        messages.error(request, "Please correct the reconciliation details below.")

    context = finance_context(
        request,
        "hospital_bank_reconciliation",
        "Bank Statements",
        "Capture bank statement lines and compare them with internal payment records.",
    )
    context.update({"form": form, "recent_statements": recent_statements, "internal_payments": internal_payments})
    return render(request, "admin_dashboard/bank_reconciliation.html", context)


@hospital_admin_only
def mobile_money_statement(request):
    hospital = active_hospital(request)
    form = MobileMoneyStatementForm(request.POST or None, hospital=hospital)
    recent_statements = (
        ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_MOBILE_MONEY)
        .select_related("mobile_money_account", "generated_by")
        [:8]
        if hospital
        else ReconciliationStatement.objects.none()
    )
    selected_mobile = None
    if request.method == "POST":
        selected_mobile = form.data.get("mobile_money_account")
    elif request.GET.get("mobile_money_account"):
        selected_mobile = request.GET.get("mobile_money_account")
    internal_payments = Payment.objects.none()
    if selected_mobile and hospital:
        internal_payments = (
            Payment.objects.filter(
                visit__hospital=hospital,
                mode=Payment.MODE_MOBILE_MONEY,
                mobile_account_id=selected_mobile,
            )
            .select_related("visit__patient", "mobile_account")
            .order_by("-paid_at", "-id")[:100]
        )

    if request.method == "POST":
        if form.is_valid():
            account = form.cleaned_data["mobile_money_account"]
            period_start = form.cleaned_data["period_start"]
            period_end = form.cleaned_data["period_end"]

            internal_total = (
                Payment.objects.filter(
                    visit__hospital=hospital,
                    paid_at__date__gte=period_start,
                    paid_at__date__lte=period_end,
                    mode=Payment.MODE_MOBILE_MONEY,
                ).aggregate(total=Sum("amount_paid"))["total"]
                or Decimal("0")
            )

            txns = MobileMoneyTransaction.objects.filter(
                mobile_money_account=account,
                transaction_date__gte=period_start,
                transaction_date__lte=period_end,
            )
            credits = txns.filter(transaction_type=MobileMoneyTransaction.TYPE_CREDIT).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            debits = txns.filter(transaction_type=MobileMoneyTransaction.TYPE_DEBIT).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            unreconciled_credits = txns.filter(transaction_type=MobileMoneyTransaction.TYPE_CREDIT, is_reconciled=False).aggregate(total=Sum("amount"))["total"] or Decimal("0")

            statement = ReconciliationStatement.objects.create(
                hospital=hospital,
                statement_type=ReconciliationStatement.TYPE_MOBILE_MONEY,
                period_start=period_start,
                period_end=period_end,
                mobile_money_account=account,
                opening_balance=Decimal("0"),
                total_deposits=credits,
                total_withdrawals=debits,
                outstanding_checks=unreconciled_credits,
                closing_balance=credits - debits,
                reconciled_balance=internal_total,
                generated_by=request.user,
            )
            messages.success(request, "Mobile money statement generated.")
            return redirect("reconciliation_detail", pk=statement.pk)
        messages.error(request, "Please correct the mobile money statement details below.")

    context = finance_context(
        request,
        "hospital_mobile_money_statement",
        "Mobile Money Statement",
        "Compare internal mobile money totals against recorded mobile money statement transactions.",
    )
    context.update({"form": form, "recent_statements": recent_statements, "internal_payments": internal_payments})
    return render(request, "admin_dashboard/mobile_money_statement.html", context)


@hospital_admin_only
def three_way_reconciliation(request):
    hospital = active_hospital(request)
    form = ThreeWayReconciliationForm(request.POST or None)
    recent_statements = ReconciliationStatement.objects.filter(hospital=hospital, statement_type=ReconciliationStatement.TYPE_THREE_WAY).select_related("generated_by")[:8] if hospital else ReconciliationStatement.objects.none()

    if request.method == "POST":
        if form.is_valid():
            start = form.cleaned_data["period_start"]
            end = form.cleaned_data["period_end"]
            bank_total = (
                BankTransaction.objects.filter(
                    bank_account__hospital=hospital,
                    transaction_date__gte=start,
                    transaction_date__lte=end,
                    transaction_type=BankTransaction.TYPE_CREDIT,
                ).aggregate(total=Sum("amount"))["total"]
                or Decimal("0")
            )
            internal_total = (
                Payment.objects.filter(
                    visit__hospital=hospital,
                    paid_at__date__gte=start,
                    paid_at__date__lte=end,
                ).aggregate(total=Sum("amount_paid"))["total"]
                or Decimal("0")
            )
            patient_total = (
                Visit.objects.filter(
                    hospital=hospital,
                    visit_date__date__gte=start,
                    visit_date__date__lte=end,
                ).aggregate(total=Sum("total_amount"))["total"]
                or Decimal("0")
            )
            statement = ReconciliationStatement.objects.create(
                hospital=hospital,
                statement_type=ReconciliationStatement.TYPE_THREE_WAY,
                period_start=start,
                period_end=end,
                total_deposits=bank_total,
                total_withdrawals=patient_total,
                closing_balance=internal_total,
                reconciled_balance=bank_total - internal_total,
                generated_by=request.user,
            )
            messages.success(request, "Three-way reconciliation statement generated.")
            return redirect("reconciliation_detail", pk=statement.pk)
        messages.error(request, "Please correct the reconciliation period below.")

    context = finance_context(
        request,
        "hospital_three_way_reconciliation",
        "Three-Way Reconciliation",
        "Compare bank deposits, internal payment totals, and patient bill totals for the same period.",
    )
    context.update({"form": form, "recent_statements": recent_statements})
    return render(request, "admin_dashboard/three_way_reconciliation.html", context)


@hospital_admin_only
def reconciliation_detail(request, pk):
    statement = hospital_owned_or_404(ReconciliationStatement, request, pk=pk)
    if statement.statement_type == ReconciliationStatement.TYPE_BANK:
        active_nav = "hospital_bank_reconciliation"
    elif statement.statement_type == ReconciliationStatement.TYPE_MOBILE_MONEY:
        active_nav = "hospital_mobile_money_statement"
    else:
        active_nav = "hospital_three_way_reconciliation"

    context = finance_context(
        request,
        active_nav,
        "Reconciliation Detail",
        "Review the generated reconciliation figures and compare the resulting balances.",
    )
    context.update({"statement": statement})
    return render(request, "admin_dashboard/reconciliation_detail.html", context)


# =====================================
# SUPERADMIN VIEWS - Hospitals Management
# =====================================


@role_required(User.ROLE_SUPERADMIN)
def manage_hospitals(request):
    query = request.GET.get("q", "").strip()
    hospitals = Hospital.objects.select_related("subscription_plan").prefetch_related("users").order_by("name")
    if query:
        hospitals = hospitals.filter(Q(name__icontains=query) | Q(subdomain__icontains=query))

    if request.method == "POST":
        form = HospitalForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                with transaction.atomic():
                    hospital = form.save()
                    User.objects.create_user(
                        username=form.cleaned_data["admin_username"],
                        password=form.cleaned_data["admin_password"],
                        role=User.ROLE_HOSPITAL_ADMIN,
                        hospital=hospital,
                        is_active=True,
                        is_staff=True,
                        email=form.cleaned_data["email"],
                    )
            except Exception as exc:
                form.add_error(None, f"Hospital onboarding could not be completed: {exc}")
                messages.error(request, "Hospital onboarding failed. Please review the details below.")
            else:
                messages.success(
                    request,
                    f"Hospital '{hospital.name}' created successfully. Hospital admin user created.",
                )
                return redirect("manage_hospitals")
        messages.error(request, "Please fix the hospital details below.")
    else:
        form = HospitalForm()

    context = superadmin_context(
        request,
        "superadmin_hospitals",
        "Hospitals",
        "Manage hospital accounts, subscriptions, and deployment details.",
    )
    context.update({"hospitals": hospitals, "form": form, "query": query})
    return render(request, "admin_dashboard/manage_hospitals.html", context)


@role_required(User.ROLE_SUPERADMIN)
def edit_hospital(request, hospital_id):
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    form = HospitalForm(request.POST or None, request.FILES or None, instance=hospital, require_admin_credentials=False)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Hospital '{hospital.name}' updated.")
            return redirect("manage_hospitals")
        messages.error(request, "Please fix the hospital details below.")

    context = superadmin_context(
        request,
        "superadmin_hospitals",
        "Edit Hospital",
        "Update hospital name, subdomain, subscription tier, and status.",
    )
    context.update({"form": form, "object_label": hospital.name, "cancel_url": "manage_hospitals"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_SUPERADMIN)
def delete_hospital(request, hospital_id):
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    if request.method == "POST":
        hospital_name = hospital.name
        hospital.delete()
        messages.success(request, f"Hospital '{hospital_name}' deleted.")
        return redirect("manage_hospitals")

    context = superadmin_context(
        request,
        "superadmin_hospitals",
        "Delete Hospital",
        "Remove this hospital account and all its records.",
    )
    context.update(
        {
            "object_label": hospital.name,
            "object_type": "hospital",
            "confirm_label": "Delete Hospital",
            "cancel_url": "manage_hospitals",
            "danger_note": "This will permanently remove the hospital and all associated data including users, reports, and payments.",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


# =====================================
# SUPERADMIN VIEWS - Subscription Plans
# =====================================


@role_required(User.ROLE_SUPERADMIN)
def manage_subscription_plans(request):
    plans = SubscriptionPlan.objects.order_by("-is_active", "name")

    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Subscription plan '{form.cleaned_data['name']}' created successfully.")
            return redirect("manage_subscription_plans")
        messages.error(request, "Please fix the plan details below.")
    else:
        form = SubscriptionPlanForm()

    context = superadmin_context(
        request,
        "superadmin_plans",
        "Subscription Plans",
        "Define and manage tiers offerings for hospitals covering users, storage, and pricing.",
    )
    context.update({"plans": plans, "form": form})
    return render(request, "admin_dashboard/manage_subscription_plans.html", context)


@role_required(User.ROLE_SUPERADMIN)
def edit_subscription_plan(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
    form = SubscriptionPlanForm(request.POST or None, instance=plan)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Subscription plan '{plan.name}' updated.")
            return redirect("manage_subscription_plans")
        messages.error(request, "Please fix the plan details below.")

    context = superadmin_context(
        request,
        "superadmin_plans",
        "Edit Subscription Plan",
        "Update pricing, limits, features, and active status for this plan.",
    )
    context.update({"form": form, "object_label": plan.name, "cancel_url": "manage_subscription_plans"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_SUPERADMIN)
def delete_subscription_plan(request, plan_id):
    plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
    if request.method == "POST":
        plan_name = plan.name
        plan.delete()
        messages.success(request, f"Subscription plan '{plan_name}' deleted.")
        return redirect("manage_subscription_plans")

    context = superadmin_context(
        request,
        "superadmin_plans",
        "Delete Subscription Plan",
        "Remove this subscription plan tier.",
    )
    context.update(
        {
            "object_label": plan.name,
            "object_type": "subscription plan",
            "confirm_label": "Delete Plan",
            "cancel_url": "manage_subscription_plans",
            "danger_note": "Any hospitals currently using this plan will be orphaned.",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


# =====================================
# SUPERADMIN VIEWS - Subscription Payments
# =====================================


@role_required(User.ROLE_SUPERADMIN)
def manage_subscription_payments(request):
    payments = HospitalSubscriptionPayment.objects.select_related("hospital").order_by("-paid_at")

    if request.method == "POST":
        form = HospitalSubscriptionPaymentForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Subscription payment recorded successfully.")
            return redirect("manage_subscription_payments")
        messages.error(request, "Please fix the payment details below.")
    else:
        form = HospitalSubscriptionPaymentForm()

    context = superadmin_context(
        request,
        "superadmin_payments",
        "Subscription Payments",
        "Track incoming payments from hospitals for subscription periods.",
    )
    context.update({"payments": payments, "form": form})
    return render(request, "admin_dashboard/manage_subscription_payments.html", context)


@role_required(User.ROLE_SUPERADMIN)
def edit_subscription_payment(request, payment_id):
    payment = get_object_or_404(HospitalSubscriptionPayment, pk=payment_id)
    form = HospitalSubscriptionPaymentForm(request.POST or None, instance=payment)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Subscription payment updated.")
            return redirect("manage_subscription_payments")
        messages.error(request, "Please fix the payment details below.")

    context = superadmin_context(
        request,
        "superadmin_payments",
        "Edit Subscription Payment",
        "Adjust payment amount, period, or notes for hospital billing.",
    )
    context.update({"form": form, "object_label": f"{payment.hospital.name}", "cancel_url": "manage_subscription_payments"})
    return render(request, "admin_dashboard/object_form.html", context)


@role_required(User.ROLE_SUPERADMIN)
def delete_subscription_payment(request, payment_id):
    payment = get_object_or_404(HospitalSubscriptionPayment, pk=payment_id)
    if request.method == "POST":
        hospital_name = payment.hospital.name
        messages.success(request, f"Subscription payment for '{hospital_name}' deleted.")
        payment.delete()
        return redirect("manage_subscription_payments")

    context = superadmin_context(
        request,
        "superadmin_payments",
        "Delete Subscription Payment",
        "Remove this payment record from the system.",
    )
    context.update(
        {
            "object_label": f"{payment.hospital.name} - {payment.amount}",
            "object_type": "payment record",
            "confirm_label": "Delete Payment",
            "cancel_url": "manage_subscription_payments",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


# =====================================
# SUPERADMIN VIEWS - Audit Logs
# =====================================


@role_required(User.ROLE_SUPERADMIN)
def view_audit_logs(request):
    from accounts.models import AuditLog

    logs = AuditLog.objects.select_related("user", "hospital").order_by("-timestamp")

    # Optional filtering
    hospital_filter = request.GET.get("hospital")
    action_filter = request.GET.get("action")

    if hospital_filter:
        logs = logs.filter(hospital_id=hospital_filter)
    if action_filter:
        logs = logs.filter(action=action_filter)

    context = superadmin_context(
        request,
        "superadmin_audit",
        "Audit Logs",
        "Review historical system activity and changes across all hospitals.",
    )
    context.update(
        {
            "audit_logs": logs[:200],  # Last 200 entries
            "hospitals": Hospital.objects.order_by("name"),
            "actions": AuditLog.objects.values_list("action", flat=True).distinct().order_by("action"),
            "hospital_filter": hospital_filter,
            "action_filter": action_filter,
        }
    )
    return render(request, "admin_dashboard/audit_logs.html", context)
