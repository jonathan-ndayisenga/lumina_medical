import csv
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, TextIOWrapper
import json
import re
from xml.sax.saxutils import escape
import zipfile

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.forms import HospitalSubscriptionPaymentForm, SubscriptionPlanForm
from accounts.models import Hospital, HospitalInvoice, HospitalModuleSubscription, HospitalSubscriptionPayment, Module, SubscriptionPlan, User
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
    InventoryBulkUploadForm,
    InventoryRestockForm,
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
    InventoryBatch,
    InventoryItem,
    InventoryTransaction,
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


def inventory_access_required(view_func):
    """Requires the user's role/group to be eligible AND the hospital to have the Inventory module."""
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.can_access_inventory:
            return HttpResponseForbidden(
                "This hospital does not have the Pharmacy/Inventory module enabled, "
                "or your account does not have access to it."
            )
        return view_func(request, *args, **kwargs)

    return wrapped


def finance_access_required(view_func):
    """Requires the user's role/group to be eligible AND the hospital to have the Finance module."""
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.can_access_finance:
            return HttpResponseForbidden(
                "This hospital does not have the Finance module enabled, "
                "or your account does not have access to it."
            )
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
    """Best-effort match when a statement line includes our receipt number (INITIALS-YYYYMMDD-000123)."""
    if not reference or not hospital:
        return None
    # Format: {INITIALS}{YYYYMMDD}-{ZEROPADDED_ID}  e.g. LTH20250712-000001 or RCT20250712-000001
    match = re.search(r"[A-Z]+\d{8}-(\d+)", str(reference).strip(), re.IGNORECASE)
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


def inventory_dashboard_snapshot(hospital):
    if not hospital:
        return {
            "stats": {},
            "category_breakdown": [],
            "monthly_sales": [],
            "restock_items": [],
        }

    from doctor.models import Prescription

    items = InventoryItem.objects.filter(hospital=hospital).order_by("name")
    total_items = items.count()
    active_items = items.filter(is_active=True).count()
    out_of_stock_items = list(items.filter(current_quantity__lte=0).order_by("name"))
    low_stock_items = list(
        items.filter(current_quantity__gt=0, current_quantity__lte=models.F("reorder_level")).order_by("current_quantity", "name")
    )

    stock_cost_value = sum((item.current_quantity or 0) * (item.unit_cost or 0) for item in items)
    stock_retail_value = sum((item.current_quantity or 0) * (item.selling_price or 0) for item in items)

    category_rows = (
        items.values("category")
        .annotate(
            item_count=models.Count("id"),
            stock_units=Sum("current_quantity"),
        )
        .order_by("category")
    )
    max_category_units = max((row["stock_units"] or 0) for row in category_rows) if category_rows else 0
    category_breakdown = []
    for row in category_rows:
        stock_units = Decimal(row["stock_units"] or 0)
        width_pct = float((stock_units / max_category_units) * 100) if max_category_units else 0
        category_breakdown.append(
            {
                "label": dict(InventoryItem.CATEGORY_CHOICES).get(row["category"], row["category"]),
                "item_count": row["item_count"],
                "stock_units": stock_units,
                "width_pct": max(width_pct, 8 if stock_units else 0),
            }
        )

    today = timezone.localdate()
    period_start = today.replace(day=1)
    for _ in range(5):
        previous_day = period_start - timedelta(days=1)
        period_start = previous_day.replace(day=1)

    monthly_rows = (
        Prescription.objects.filter(
            visit__hospital=hospital,
            dispensed=True,
            dispensed_at__date__gte=period_start,
        )
        .annotate(month=TruncMonth("dispensed_at"))
        .values("month")
        .annotate(total=Sum("total_price"))
        .order_by("month")
    )
    monthly_map = {row["month"].date() if hasattr(row["month"], "date") else row["month"]: Decimal(row["total"] or 0) for row in monthly_rows}
    monthly_sales = []
    cursor = period_start
    while cursor <= today.replace(day=1):
        amount = monthly_map.get(cursor, Decimal("0"))
        monthly_sales.append({"label": cursor.strftime("%b %Y"), "amount": amount})
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        cursor = next_month

    # Net profit per month (pharmacy sales - expenses - salaries) for the same window
    from reception.models import Payment as _Payment
    monthly_income_rows = (
        _Payment.objects.filter(
            visit__hospital=hospital,
            paid_at__date__gte=period_start,
            paid_at__isnull=False,
            amount_paid__gt=0,
        )
        .exclude(status=_Payment.STATUS_WAIVED)
        .annotate(month=TruncMonth("paid_at"))
        .values("month")
        .annotate(total=Sum("amount_paid"))
    )
    monthly_expense_rows = (
        Expense.objects.filter(hospital=hospital, date__gte=period_start)
        .annotate(trunc_month=TruncMonth("date"))
        .values("trunc_month")
        .annotate(total=Sum("amount"))
    )
    monthly_salary_rows = (
        Salary.objects.filter(hospital=hospital, paid=True, paid_at__gte=period_start)
        .annotate(trunc_month=TruncMonth("paid_at"))
        .values("trunc_month")
        .annotate(total=Sum("amount"))
    )

    def _month_key(row):
        m = row.get("trunc_month") or row.get("month")
        return m.date() if hasattr(m, "date") else m

    income_map = {_month_key(r): Decimal(r["total"] or 0) for r in monthly_income_rows}
    expense_map = {_month_key(r): Decimal(r["total"] or 0) for r in monthly_expense_rows}
    salary_map = {_month_key(r): Decimal(r["total"] or 0) for r in monthly_salary_rows}

    pharma_chart_labels = [m["label"] for m in monthly_sales]
    pharma_chart_values = [str(m["amount"]) for m in monthly_sales]
    net_profit_chart_values = []
    for m in monthly_sales:
        key = None
        for ms in monthly_sales:
            if ms["label"] == m["label"]:
                break
        # derive the date key for this label
        from datetime import date as _date
        import calendar as _calendar
        for k in income_map.keys() | expense_map.keys() | salary_map.keys() | monthly_map.keys():
            if k.strftime("%b %Y") == m["label"]:
                key = k
                break
        if key is None:
            for ms_entry in monthly_sales:
                if ms_entry["label"] == m["label"]:
                    break
        net = income_map.get(key, Decimal("0")) - expense_map.get(key, Decimal("0")) - salary_map.get(key, Decimal("0")) if key else Decimal("0")
        net_profit_chart_values.append(str(net))

    stats = {
        "total_items": total_items,
        "active_items": active_items,
        "out_of_stock_count": len(out_of_stock_items),
        "low_stock_count": len(low_stock_items),
        "stock_cost_value": stock_cost_value,
        "stock_retail_value": stock_retail_value,
        "estimated_margin": stock_retail_value - stock_cost_value,
        "month_sales": sum((entry["amount"] for entry in monthly_sales if entry["label"] == today.replace(day=1).strftime("%b %Y")), Decimal("0")),
    }

    return {
        "stats": stats,
        "category_breakdown": category_breakdown,
        "monthly_sales": monthly_sales,
        "restock_items": (out_of_stock_items + low_stock_items)[:15],
        "out_of_stock_items": out_of_stock_items,
        "low_stock_items": low_stock_items,
        "pharma_chart_labels_json": json.dumps(pharma_chart_labels),
        "pharma_chart_values_json": json.dumps(pharma_chart_values),
        "net_profit_chart_values_json": json.dumps(net_profit_chart_values),
    }


INVENTORY_IMPORT_HEADERS = [
    "name",
    "category",
    "unit",
    "base_unit",
    "units_per_pack",
    "strength_mg_per_unit",
    "concentration_mg_per_ml",
    "pack_size_ml",
    "days_covered_per_pack",
    "current_quantity",
    "unit_cost",
    "selling_price",
    "reorder_level",
    "is_active",
    "opening_batch_number",
    "opening_expiry_date",
]


def normalize_inventory_choice(raw_value, choices):
    value = (raw_value or "").strip()
    if not value:
        return ""
    normalized = value.lower()
    choice_map = {}
    for choice_value, choice_label in choices:
        choice_map[str(choice_value).strip().lower()] = choice_value
        choice_map[str(choice_label).strip().lower()] = choice_value
    return choice_map.get(normalized, value)


def parse_inventory_boolean(raw_value):
    value = (raw_value or "").strip().lower()
    if not value:
        return True
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return raw_value


def build_inventory_import_form_data(row):
    return {
        "name": (row.get("name") or "").strip(),
        "category": normalize_inventory_choice(row.get("category"), InventoryItem.CATEGORY_CHOICES),
        "unit": normalize_inventory_choice(row.get("unit"), InventoryItemForm.PACK_TYPE_CHOICES),
        "base_unit": normalize_inventory_choice(row.get("base_unit"), InventoryItemForm.BASE_UNIT_CHOICES),
        "units_per_pack": (row.get("units_per_pack") or "").strip(),
        "strength_mg_per_unit": (row.get("strength_mg_per_unit") or "").strip(),
        "concentration_mg_per_ml": (row.get("concentration_mg_per_ml") or "").strip(),
        "pack_size_ml": (row.get("pack_size_ml") or "").strip(),
        "days_covered_per_pack": (row.get("days_covered_per_pack") or "").strip(),
        "current_quantity": (row.get("current_quantity") or "").strip(),
        "unit_cost": (row.get("unit_cost") or "").strip(),
        "selling_price": (row.get("selling_price") or "").strip(),
        "reorder_level": (row.get("reorder_level") or "").strip(),
        "is_active": parse_inventory_boolean(row.get("is_active")),
        "opening_batch_number": (row.get("opening_batch_number") or "").strip(),
        "opening_expiry_date": (row.get("opening_expiry_date") or "").strip(),
    }


def inventory_report_rows(items):
    rows = []
    totals = {
        "stock_cost": Decimal("0"),
        "stock_retail": Decimal("0"),
        "stock_units": Decimal("0"),
    }
    for item in items:
        stock_cost = (item.current_quantity or Decimal("0")) * (item.unit_cost or Decimal("0"))
        stock_retail = (item.current_quantity or Decimal("0")) * (item.selling_price or Decimal("0"))
        status = "Out of Stock" if item.current_quantity <= 0 else "Low Stock" if item.is_low_stock else "Healthy"
        positive_batches = list(item.batches.filter(quantity__gt=0).order_by("expiry_date", "batch_number", "id"))
        if positive_batches:
            for index, batch in enumerate(positive_batches):
                rows.append(
                    {
                        "name": item.name if index == 0 else "",
                        "category": item.get_category_display() if index == 0 else "",
                        "pack_type": item.unit if index == 0 else "",
                        "base_unit": item.base_unit if index == 0 else "",
                        "units_per_pack": item.units_per_pack if index == 0 else "",
                        "batch_number": batch.batch_number,
                        "batch_quantity": batch.quantity,
                        "batch_expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else "",
                        "batch_unit_cost": batch.unit_cost,
                        "current_stock": item.current_quantity if index == 0 else "",
                        "minimum_stock": item.reorder_level if index == 0 else "",
                        "buying_price": item.unit_cost if index == 0 else "",
                        "selling_price": item.selling_price or Decimal("0") if index == 0 else "",
                        "stock_cost": stock_cost if index == 0 else "",
                        "stock_retail": stock_retail if index == 0 else "",
                        "status": status if index == 0 else "",
                    }
                )
        else:
            rows.append(
                {
                    "name": item.name,
                    "category": item.get_category_display(),
                    "pack_type": item.unit,
                    "base_unit": item.base_unit,
                    "units_per_pack": item.units_per_pack,
                    "batch_number": "",
                    "batch_quantity": "",
                    "batch_expiry_date": "",
                    "batch_unit_cost": "",
                    "current_stock": item.current_quantity,
                    "minimum_stock": item.reorder_level,
                    "buying_price": item.unit_cost,
                    "selling_price": item.selling_price or Decimal("0"),
                    "stock_cost": stock_cost,
                    "stock_retail": stock_retail,
                    "status": status,
                }
            )
        totals["stock_cost"] += stock_cost
        totals["stock_retail"] += stock_retail
        totals["stock_units"] += item.current_quantity or Decimal("0")
    return rows, totals


def inventory_printable_rows(items):
    rows = []
    totals = {
        "stock_cost": Decimal("0"),
        "stock_retail": Decimal("0"),
        "stock_units": Decimal("0"),
    }
    for item in items:
        stock_cost = (item.current_quantity or Decimal("0")) * (item.unit_cost or Decimal("0"))
        stock_retail = (item.current_quantity or Decimal("0")) * (item.selling_price or Decimal("0"))
        rows.append(
            {
                "name": item.name,
                "category": item.get_category_display(),
                "stock_quantity": item.current_quantity or Decimal("0"),
                "buying_price": item.unit_cost or Decimal("0"),
                "stock_cost": stock_cost,
                "selling_price": item.selling_price or Decimal("0"),
                "stock_retail": stock_retail,
                "batches": list(item.batches.filter(quantity__gt=0).order_by("expiry_date", "batch_number", "id")),
            }
        )
        totals["stock_cost"] += stock_cost
        totals["stock_retail"] += stock_retail
        totals["stock_units"] += item.current_quantity or Decimal("0")
    return rows, totals


def _xlsx_cell_ref(column_index):
    letters = ""
    while column_index > 0:
        column_index, remainder = divmod(column_index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def build_inventory_xlsx_bytes(report_rows, totals):
    shared_strings = []
    string_index = {}

    def shared_string_id(value):
        value = "" if value is None else str(value)
        if value not in string_index:
            string_index[value] = len(shared_strings)
            shared_strings.append(value)
        return string_index[value]

    def text_cell(ref, value):
        idx = shared_string_id(value)
        return f'<c r="{ref}" t="s"><v>{idx}</v></c>'

    def number_cell(ref, value):
        number = Decimal(value or 0)
        return f'<c r="{ref}"><v>{number}</v></c>'

    headers = [
        "Item Name",
        "Category",
        "Pack Type",
        "Base Unit",
        "Units Per Pack",
        "Batch Number",
        "Batch Quantity",
        "Batch Expiry Date",
        "Batch Unit Cost",
        "Current Stock",
        "Minimum Stock Level",
        "Buying Price",
        "Selling Price",
        "Estimated Stock Cost",
        "Estimated Stock Retail",
        "Status",
    ]

    rows_xml = []
    rows_xml.append(
        "<row r=\"1\">"
        + "".join(text_cell(f"{_xlsx_cell_ref(idx)}1", header) for idx, header in enumerate(headers, start=1))
        + "</row>"
    )

    for row_number, row in enumerate(report_rows, start=2):
        cells = [
            text_cell(f"A{row_number}", row["name"]),
            text_cell(f"B{row_number}", row["category"]),
            text_cell(f"C{row_number}", row["pack_type"]),
            text_cell(f"D{row_number}", row["base_unit"]),
            number_cell(f"E{row_number}", row["units_per_pack"]),
            text_cell(f"F{row_number}", row["batch_number"]),
            number_cell(f"G{row_number}", row["batch_quantity"]),
            text_cell(f"H{row_number}", row["batch_expiry_date"]),
            number_cell(f"I{row_number}", row["batch_unit_cost"]),
            number_cell(f"J{row_number}", row["current_stock"]),
            number_cell(f"K{row_number}", row["minimum_stock"]),
            number_cell(f"L{row_number}", row["buying_price"]),
            number_cell(f"M{row_number}", row["selling_price"]),
            number_cell(f"N{row_number}", row["stock_cost"]),
            number_cell(f"O{row_number}", row["stock_retail"]),
            text_cell(f"P{row_number}", row["status"]),
        ]
        rows_xml.append(f"<row r=\"{row_number}\">{''.join(cells)}</row>")

    totals_row = len(report_rows) + 3
    rows_xml.append(
        f"<row r=\"{totals_row}\">"
        + text_cell(f"A{totals_row}", "Totals")
        + text_cell(f"B{totals_row}", "")
        + text_cell(f"C{totals_row}", "")
        + text_cell(f"D{totals_row}", "")
        + text_cell(f"E{totals_row}", "")
        + text_cell(f"F{totals_row}", "")
        + text_cell(f"G{totals_row}", "")
        + text_cell(f"H{totals_row}", "")
        + text_cell(f"I{totals_row}", "")
        + number_cell(f"J{totals_row}", totals["stock_units"])
        + text_cell(f"K{totals_row}", "")
        + text_cell(f"L{totals_row}", "")
        + text_cell(f"M{totals_row}", "")
        + number_cell(f"N{totals_row}", totals["stock_cost"])
        + number_cell(f"O{totals_row}", totals["stock_retail"])
        + text_cell(f"P{totals_row}", "")
        + "</row>"
    )

    worksheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:P{totals_row}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <cols>
     <col min="1" max="1" width="28" customWidth="1"/>
     <col min="2" max="5" width="16" customWidth="1"/>
     <col min="6" max="9" width="18" customWidth="1"/>
     <col min="10" max="15" width="16" customWidth="1"/>
     <col min="16" max="16" width="16" customWidth="1"/>
  </cols>
  <sheetData>
    {''.join(rows_xml)}
  </sheetData>
  <autoFilter ref="A1:P{max(2, len(report_rows) + 1)}"/>
</worksheet>
"""

    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
        + "".join(f"<si><t>{escape(value)}</t></si>" for value in shared_strings)
        + "</sst>"
    )

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Inventory Report" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""

    workbook_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>
"""

    root_rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""

    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""

    core_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>Inventory Report</dc:title>
  <dc:creator>Lumina Medical Services</dc:creator>
  <cp:lastModifiedBy>Lumina Medical Services</cp:lastModifiedBy>
</cp:coreProperties>
"""

    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Lumina Medical Services</Application>
</Properties>
"""

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml)
        workbook.writestr("_rels/.rels", root_rels_xml)
        workbook.writestr("docProps/core.xml", core_xml)
        workbook.writestr("docProps/app.xml", app_xml)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        workbook.writestr("xl/styles.xml", styles_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_strings_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return buffer.getvalue()


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

    # --- Pie chart: expected monthly income share per module, platform-wide ---
    modules = Module.objects.filter(is_active=True).order_by("display_order")
    pie_labels = []
    pie_values = []
    module_expected_total = Decimal("0")
    for module in modules:
        active_count = HospitalModuleSubscription.objects.filter(module=module, is_active=True).count()
        expected = module.monthly_price * active_count
        if expected > 0:
            pie_labels.append(module.name)
            pie_values.append(str(expected))
            module_expected_total += expected

    # --- Line chart: 6-month window, two switchable modes ---
    today = timezone.now().date()
    month_starts = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_starts.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    month_starts.reverse()
    month_labels = [m.strftime("%b %Y") for m in month_starts]

    # Mode 1: projected monthly income per hospital (current module mix, projected
    # back to each hospital's onboarding month — clearly a projection, not collected cash).
    income_datasets = []
    all_hospitals = list(hospitals)
    for hospital in all_hospitals:
        monthly_total = sum(
            (m.monthly_price for m in modules if m.code in hospital.active_module_codes),
            Decimal("0"),
        )
        onboarded_month = hospital.created_at.date().replace(day=1) if hospital.created_at else today.replace(day=1)
        data_points = [
            str(monthly_total) if month >= onboarded_month else "0"
            for month in month_starts
        ]
        if monthly_total > 0:
            income_datasets.append({"label": hospital.name, "data": data_points})

    # Mode 2: hospital onboarding trend — count of new hospitals per month.
    onboarding_counts = []
    for month in month_starts:
        next_month = (month + timedelta(days=32)).replace(day=1)
        count = sum(
            1 for h in all_hospitals
            if h.created_at and month <= h.created_at.date() < next_month
        )
        onboarding_counts.append(count)

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
        "module_expected_total": module_expected_total,
        "pie_labels_json": json.dumps(pie_labels),
        "pie_values_json": json.dumps(pie_values),
        "month_labels_json": json.dumps(month_labels),
        "income_datasets_json": json.dumps(income_datasets),
        "onboarding_counts_json": json.dumps(onboarding_counts),
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
    completed_visit_payments = (
        payments.filter(paid_at__isnull=False, amount_paid__gt=0)
        .exclude(status=Payment.STATUS_WAIVED)
    ) if hospital else Payment.objects.none()
    services = Service.objects.filter(hospital=hospital, is_active=True) if hospital else Service.objects.none()
    account = sync_hospital_account_balance(hospital) if hospital else None
    low_stock_items = InventoryItem.objects.filter(hospital=hospital, current_quantity__lte=models.F("reorder_level")) if hospital else InventoryItem.objects.none()

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
        "total_billed": completed_visit_payments.aggregate(total=Sum("amount"))["total"] or 0,
        "realized_income": completed_visit_payments.aggregate(total=Sum("amount_paid"))["total"] or 0,
        "account_balance": account.balance if account else 0,
        "paid_visits": completed_visit_payments.filter(status=Payment.STATUS_PAID).count(),
        "pending_payments": completed_visit_payments.exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).count(),
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


@finance_access_required
def financial_report(request):
    hospital = active_hospital(request)

    # Single source of truth: the Payment (receipts) table.
    # A receipt exists when paid_at is set and amount_paid > 0. Waived payments are excluded.
    receipts = (
        Payment.objects.filter(visit__hospital=hospital, paid_at__isnull=False, amount_paid__gt=0)
        .exclude(status=Payment.STATUS_WAIVED)
    ) if hospital else Payment.objects.none()

    paid_income = receipts.aggregate(total=Sum("amount_paid"))["total"] or 0
    expense_total = Expense.objects.filter(hospital=hospital).aggregate(total=Sum("amount"))["total"] or 0 if hospital else 0
    salary_total = Salary.objects.filter(hospital=hospital, paid=True).aggregate(total=Sum("amount"))["total"] or 0 if hospital else 0
    account = sync_hospital_account_balance(hospital) if hospital else None
    today = timezone.localdate()
    month_start = today.replace(day=1)

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

    receipts_today  = receipts.filter(paid_at__date=today)
    receipts_month  = receipts.filter(paid_at__date__gte=month_start, paid_at__date__lte=today)
    receipts_period = receipts.filter(paid_at__date__gte=period_start, paid_at__date__lte=period_end)

    income_period = receipts_period.aggregate(total=Sum("amount_paid"))["total"] or 0
    expenses_period = (
        Expense.objects.filter(hospital=hospital, date__gte=period_start, date__lte=period_end)
        .aggregate(total=Sum("amount"))["total"] or 0
        if hospital else 0
    )
    salaries_period = (
        Salary.objects.filter(hospital=hospital, paid=True, paid_at__gte=period_start, paid_at__lte=period_end)
        .aggregate(total=Sum("amount"))["total"] or 0
        if hospital else 0
    )

    open_drawer = CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=True).first() if hospital else None
    open_drawer_cash_in = (
        open_drawer.transactions.filter(transaction_type=CashTransaction.TYPE_CASH_IN).aggregate(total=Sum("amount"))["total"]
        if open_drawer else None
    ) or 0
    open_drawer_cash_out = (
        open_drawer.transactions.filter(transaction_type=CashTransaction.TYPE_CASH_OUT).aggregate(total=Sum("amount"))["total"]
        if open_drawer else None
    ) or 0
    open_drawer_expected = (open_drawer.opening_balance + open_drawer_cash_in - open_drawer_cash_out) if open_drawer else None
    last_closed_drawer = CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=False).order_by("-date", "-id").first() if hospital else None

    recent_receipts = (
        receipts.select_related("visit__patient", "recorded_by")
        .order_by("-paid_at", "-id")[:8]
        if hospital else Payment.objects.none()
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
        ("pharmacy_income", "Pharmacy Income"),
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
    pharma_sales_list = []

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
        # All income report types pull from receipts (Payment table).
        receipts_dash = receipts_period

        if report_type in {"income_overview", "cash_collection", "card_payments", "mobile_money"}:
            if report_type == "cash_collection":
                receipts_dash = receipts_dash.filter(mode=Payment.MODE_CASH)
                chart_title = "Cash Collections"
                chart_series_label = "Cash received"
            elif report_type == "card_payments":
                receipts_dash = receipts_dash.filter(mode=Payment.MODE_CARD)
                chart_title = "Card Payments"
                chart_series_label = "Card received"
                if bank_account_id:
                    receipts_dash = receipts_dash.filter(bank_account_id=bank_account_id)
            elif report_type == "mobile_money":
                receipts_dash = receipts_dash.filter(mode=Payment.MODE_MOBILE_MONEY)
                chart_title = "Mobile Money Payments"
                chart_series_label = "Mobile money received"
                if mobile_account_id:
                    receipts_dash = receipts_dash.filter(mobile_account_id=mobile_account_id)
            else:
                chart_title = "Income Overview"
                chart_series_label = "Collected income"
                if payment_mode and payment_mode != "all":
                    receipts_dash = receipts_dash.filter(mode=payment_mode)

            daily = (
                receipts_dash.annotate(day=TruncDate("paid_at"))
                .values("day")
                .annotate(total=Sum("amount_paid"))
                .order_by("day")
            )
            chart_labels = [row["day"].isoformat() for row in daily if row["day"]]
            chart_values = [str(row["total"] or 0) for row in daily]

            grouped = {}
            for payment in receipts_dash.select_related("bank_account", "mobile_account").order_by("-paid_at", "-id")[:800]:
                day = payment.paid_at.date()
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
            for (day, mode_value, account_label), amount_value in sorted(grouped.items(), key=lambda x: x[0][0], reverse=True)[:200]:
                append_summary(day, amount_value, mode_value, account_label)

        elif report_type == "expenses":
            chart_title = "Expenses"
            chart_series_label = "Expenses recorded"
            chart_kind = "bar"
            expenses_dash = Expense.objects.filter(
                hospital=hospital, date__gte=period_start, date__lte=period_end
            ).select_related("bank_account", "mobile_money_account", "cash_drawer")
            daily = expenses_dash.values("date").annotate(total=Sum("amount")).order_by("date")
            chart_labels = [row["date"].isoformat() for row in daily if row["date"]]
            chart_values = [str(row["total"] or 0) for row in daily]
            grouped = {}
            for expense in expenses_dash.order_by("-date", "-id")[:800]:
                key = (expense.date, "expense", expense.source_account_label)
                grouped[key] = grouped.get(key, Decimal("0")) + (expense.amount or Decimal("0"))
            for (day, mode_value, account_label), amount_value in sorted(grouped.items(), key=lambda x: x[0][0], reverse=True)[:200]:
                append_summary(day, amount_value, mode_value, account_label)

        elif report_type == "salaries":
            chart_title = "Salaries"
            chart_series_label = "Salaries paid"
            chart_kind = "bar"
            salaries_dash = Salary.objects.filter(
                hospital=hospital, paid=True,
                paid_at__gte=period_start, paid_at__lte=period_end,
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
            for (day, mode_value, account_label), amount_value in sorted(grouped.items(), key=lambda x: x[0][0], reverse=True)[:200]:
                append_summary(day, amount_value, mode_value, account_label)

        elif report_type == "net_profit":
            chart_title = "Net Profit"
            chart_series_label = "Net profit"
            chart_kind = "bar"
            expenses_dash = Expense.objects.filter(hospital=hospital, date__gte=period_start, date__lte=period_end)
            salaries_dash = Salary.objects.filter(hospital=hospital, paid=True, paid_at__gte=period_start, paid_at__lte=period_end)
            income_daily = {
                row["day"]: (row["total"] or Decimal("0"))
                for row in receipts_dash.annotate(day=TruncDate("paid_at")).values("day").annotate(total=Sum("amount_paid"))
            }
            exp_daily = {row["date"]: (row["total"] or Decimal("0")) for row in expenses_dash.values("date").annotate(total=Sum("amount"))}
            sal_daily = {row["paid_at"]: (row["total"] or Decimal("0")) for row in salaries_dash.values("paid_at").annotate(total=Sum("amount"))}
            cursor = period_start
            while cursor <= period_end:
                net = income_daily.get(cursor, Decimal("0")) - exp_daily.get(cursor, Decimal("0")) - sal_daily.get(cursor, Decimal("0"))
                chart_labels.append(cursor.isoformat())
                chart_values.append(str(net))
                cursor += timedelta(days=1)
            for label, value in zip(reversed(chart_labels), reversed(chart_values)):
                append_summary(timezone.datetime.fromisoformat(label).date(), Decimal(value), "net_profit", "Income - Expenses - Salaries")
                if len(summary_rows) >= 60:
                    break

        elif report_type == "pharmacy_income":
            # Pharmacy income: sum of receipt amounts for visits that included dispensed drugs,
            # attributed to the date the receipt was paid (not the dispensing date).
            from doctor.models import Prescription
            chart_title = "Pharmacy Income"
            chart_series_label = "Pharmacy receipts"
            chart_kind = "bar"

            # Get visit IDs that had at least one dispensed prescription in the period.
            pharma_visit_ids = set(
                Prescription.objects.filter(
                    visit__hospital=hospital,
                    dispensed=True,
                    dispensed_at__date__gte=period_start,
                    dispensed_at__date__lte=period_end,
                ).values_list("visit_id", flat=True)
            )

            # Find the receipts for those visits, using paid_at as the date.
            pharma_receipts = receipts_period.filter(visit_id__in=pharma_visit_ids)

            daily = (
                pharma_receipts.annotate(day=TruncDate("paid_at"))
                .values("day")
                .annotate(total=Sum("amount_paid"))
                .order_by("day")
            )
            chart_labels = [row["day"].isoformat() for row in daily if row["day"]]
            chart_values = [str(row["total"] or 0) for row in daily]

            # Drug sales breakdown for summary table (from Prescription for drug-level detail).
            drug_totals = {}
            for rx in Prescription.objects.filter(
                visit__hospital=hospital,
                dispensed=True,
                dispensed_at__date__gte=period_start,
                dispensed_at__date__lte=period_end,
            ).select_related("drug", "visit__patient").order_by("-dispensed_at")[:800]:
                day = rx.dispensed_at.date() if rx.dispensed_at else None
                if not day:
                    continue
                item_name = str(rx.drug) if rx.drug_id else "Unknown Drug"
                append_summary(day, rx.total_price or Decimal("0"), "pharmacy", item_name)
                drug_id = rx.drug_id or 0
                if drug_id not in drug_totals:
                    drug_totals[drug_id] = {"drug_name": item_name, "quantity_sold": Decimal("0"), "total_amount": Decimal("0"), "stock_remaining": rx.drug.current_quantity if rx.drug else Decimal("0")}
                drug_totals[drug_id]["quantity_sold"] += rx.total_quantity or Decimal("0")
                drug_totals[drug_id]["total_amount"] += rx.total_price or Decimal("0")
            pharma_sales_list = sorted(drug_totals.values(), key=lambda x: x["total_amount"], reverse=True)

    context = {
        "active_nav": "hospital_financials",
        "dashboard_title": "Financial Report",
        "dashboard_intro": "Track realized income, outgoing costs, and the running hospital balance.",
        "hospital": hospital,
        "today": today,
        "month_start": month_start,
        "paid_income": paid_income,
        "paid_income_today": receipts_today.aggregate(total=Sum("amount_paid"))["total"] or 0,
        "paid_income_month": receipts_month.aggregate(total=Sum("amount_paid"))["total"] or 0,
        "receipt_count_today": receipts_today.count(),
        "receipt_count_month": receipts_month.count(),
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
        "pending_payments": Payment.objects.filter(visit__hospital=hospital).exclude(status=Payment.STATUS_WAIVED).exclude(amount_paid=models.F("amount")).count() if hospital else 0,
        "expense_items": (
            Expense.objects.filter(hospital=hospital)
            .select_related("bank_account", "mobile_money_account", "cash_drawer")
            .order_by("-date", "-id")[:10]
            if hospital
            else []
        ),
        "salary_items": Salary.objects.filter(hospital=hospital).select_related("employee").order_by("-month", "-id")[:10] if hospital else [],
        "low_stock_items": InventoryItem.objects.filter(hospital=hospital, current_quantity__lte=models.F("reorder_level")).order_by("current_quantity", "name")[:10] if hospital else [],
        "part_paid_count": Payment.objects.filter(visit__hospital=hospital, status=Payment.STATUS_PART_PAID).count() if hospital else 0,
        "outstanding_balance": (
            Payment.objects.filter(visit__hospital=hospital)
            .exclude(status=Payment.STATUS_WAIVED)
            .aggregate(total=Sum(models.F("amount") - models.F("amount_paid")))["total"] or 0
            if hospital else 0
        ),
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
        "pharma_sales_list": pharma_sales_list,
    }
    return render(request, "admin_dashboard/financial_report.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def manage_users(request):
    from django.core.paginator import Paginator

    hospital = active_hospital(request)
    users_qs = hospital.users.order_by("role", "first_name", "username") if hospital else User.objects.none()

    if request.method == "POST":
        form = HospitalStaffUserForm(request.POST, hospital=hospital)
        if form.is_valid():
            user = form.save(commit=False)
            user.hospital = hospital
            user.save()
            form.save_m2m()
            messages.success(request, f"{user.get_full_name() or user.username} added successfully.")
            return redirect("manage_users")
        messages.error(request, "Please fix the user details below.")
    else:
        form = HospitalStaffUserForm(hospital=hospital)

    paginator = Paginator(users_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = hospital_admin_context(
        request,
        "hospital_users",
        "Hospital Users",
        "Create and review operational user accounts for this hospital.",
    )
    context.update({"page_obj": page_obj, "form": form})
    return render(request, "admin_dashboard/manage_users.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def edit_user(request, user_id):
    user = hospital_owned_or_404(User, request, pk=user_id)
    hospital = active_hospital(request)
    form = HospitalStaffUserUpdateForm(request.POST or None, instance=user, hospital=hospital)
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
def reset_user_password(request, user_id):
    user = hospital_owned_or_404(User, request, pk=user_id)

    if request.method == "POST":
        password1 = request.POST.get("password1", "").strip()
        password2 = request.POST.get("password2", "").strip()
        if not password1:
            messages.error(request, "Password cannot be empty.")
        elif password1 != password2:
            messages.error(request, "Passwords do not match.")
        elif len(password1) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        else:
            user.set_password(password1)
            user.save(update_fields=["password"])
            messages.success(request, f"Password for {user.get_full_name() or user.username} has been reset.")
            return redirect("manage_users")

    context = hospital_admin_context(
        request,
        "hospital_users",
        "Reset Password",
        f"Set a new password for {user.get_full_name() or user.username}.",
    )
    context["target_user"] = user
    return render(request, "admin_dashboard/reset_user_password.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def delete_user(request, user_id):
    user = hospital_owned_or_404(User, request, pk=user_id)
    if user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect("manage_users")

    if request.method == "POST":
        name = user.get_full_name() or user.username
        user.delete()
        messages.success(request, f"{name} has been permanently deleted.")
        return redirect("manage_users")

    context = hospital_admin_context(
        request,
        "hospital_users",
        "Delete Staff Member",
        "Permanently remove this staff account from the system.",
    )
    context.update(
        {
            "object_label": user.get_full_name() or user.username,
            "object_type": "user",
            "confirm_label": "Delete Permanently",
            "cancel_url": "manage_users",
            "danger_note": "This action cannot be undone. All data linked to this account (audit logs, created records) will lose the user reference. Consider Deactivate instead if you want to preserve history.",
        }
    )
    return render(request, "admin_dashboard/confirm_delete.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def manage_services(request):
    hospital = active_hospital(request)
    services_qs = Service.objects.filter(hospital=hospital).order_by("category", "name") if hospital else Service.objects.none()

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

    paginator = Paginator(services_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = hospital_admin_context(
        request,
        "hospital_services",
        "Services and Prices",
        "Configure the services this hospital offers and what each one costs.",
    )
    context.update({"services": page_obj, "page_obj": page_obj, "form": form})
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


@finance_access_required
def manage_expenses(request):
    from datetime import date as _date
    import urllib.parse as _urlparse

    hospital = active_hospital(request)
    search_term = (request.GET.get("search") or "").strip()
    filter_category = (request.GET.get("category") or "").strip()
    valid_categories = {c[0] for c in Expense.CATEGORY_CHOICES}

    try:
        date_start = _date.fromisoformat((request.GET.get("date_start") or "").strip())
    except ValueError:
        date_start = None
    try:
        date_end = _date.fromisoformat((request.GET.get("date_end") or "").strip())
    except ValueError:
        date_end = None

    expenses_qs = (
        Expense.objects.filter(hospital=hospital)
        .select_related("bank_account", "mobile_money_account", "cash_drawer")
        .order_by("-date", "-id")
        if hospital
        else Expense.objects.none()
    )
    if search_term:
        expenses_qs = expenses_qs.filter(description__icontains=search_term)
    if filter_category in valid_categories:
        expenses_qs = expenses_qs.filter(category=filter_category)
    else:
        filter_category = ""
    if date_start:
        expenses_qs = expenses_qs.filter(date__gte=date_start)
    if date_end:
        expenses_qs = expenses_qs.filter(date__lte=date_end)

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

    chart_base_qs = Expense.objects.filter(hospital=hospital) if hospital else Expense.objects.none()
    if date_start:
        chart_base_qs = chart_base_qs.filter(date__gte=date_start)
    if date_end:
        chart_base_qs = chart_base_qs.filter(date__lte=date_end)
    category_totals_qs = chart_base_qs.values("category").annotate(total=Sum("amount")).order_by("-total")
    category_label_map = dict(Expense.CATEGORY_CHOICES)
    chart_labels = [category_label_map.get(row["category"], row["category"]) for row in category_totals_qs]
    chart_values = [str(row["total"] or 0) for row in category_totals_qs]

    paginator = Paginator(expenses_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    filter_params = {}
    if search_term:
        filter_params["search"] = search_term
    if filter_category:
        filter_params["category"] = filter_category
    if date_start:
        filter_params["date_start"] = date_start.isoformat()
    if date_end:
        filter_params["date_end"] = date_end.isoformat()
    filter_qs = _urlparse.urlencode(filter_params)

    context = hospital_admin_context(
        request,
        "hospital_expenses",
        "Expenses",
        "Track operational costs such as rent, utilities, consumables, and other outflows.",
    )
    context.update({
        "expenses": page_obj,
        "page_obj": page_obj,
        "form": form,
        "chart_labels_json": json.dumps(chart_labels),
        "chart_values_json": json.dumps(chart_values),
        "search_term": search_term,
        "filter_category": filter_category,
        "category_choices": Expense.CATEGORY_CHOICES,
        "date_start": date_start.isoformat() if date_start else "",
        "date_end": date_end.isoformat() if date_end else "",
        "filter_qs": filter_qs,
    })
    return render(request, "admin_dashboard/manage_expenses.html", context)


@finance_access_required
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


@finance_access_required
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


@finance_access_required
def manage_salaries(request):
    hospital = active_hospital(request)
    salaries_qs = Salary.objects.filter(hospital=hospital).select_related("employee").order_by("-month", "-id") if hospital else Salary.objects.none()

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

    paginator = Paginator(salaries_qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = hospital_admin_context(
        request,
        "hospital_salaries",
        "Salaries",
        "Track payroll obligations and paid salary entries for hospital staff.",
    )
    context.update({"salaries": page_obj, "page_obj": page_obj, "form": form})
    return render(request, "admin_dashboard/manage_salaries.html", context)


@finance_access_required
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


@finance_access_required
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


@inventory_access_required
def manage_inventory(request):
    hospital = active_hospital(request)
    inventory_items = InventoryItem.objects.filter(hospital=hospital).order_by("name") if hospital else InventoryItem.objects.none()
    search_query = (request.GET.get("search") or "").strip()
    selected_category = (request.GET.get("category") or "").strip()
    selected_stock_filter = (request.GET.get("stock") or "").strip()

    valid_categories = {choice[0] for choice in InventoryItem.CATEGORY_CHOICES}
    valid_stock_filters = {
        "all",
        "out",
        "low",
        "healthy",
        "active",
        "inactive",
    }

    if search_query:
        inventory_items = inventory_items.filter(
            Q(name__icontains=search_query)
            | Q(unit__icontains=search_query)
            | Q(base_unit__icontains=search_query)
            | Q(category__icontains=search_query)
        )
    if selected_category in valid_categories:
        inventory_items = inventory_items.filter(category=selected_category)
    else:
        selected_category = ""

    if selected_stock_filter not in valid_stock_filters:
        selected_stock_filter = "all"

    if selected_stock_filter == "out":
        inventory_items = inventory_items.filter(current_quantity__lte=0)
    elif selected_stock_filter == "low":
        inventory_items = inventory_items.filter(
            current_quantity__gt=0,
            current_quantity__lte=models.F("reorder_level"),
        )
    elif selected_stock_filter == "healthy":
        inventory_items = inventory_items.filter(current_quantity__gt=models.F("reorder_level"))
    elif selected_stock_filter == "active":
        inventory_items = inventory_items.filter(is_active=True)
    elif selected_stock_filter == "inactive":
        inventory_items = inventory_items.filter(is_active=False)

    filtered_inventory_count = inventory_items.count()
    inventory_items = inventory_items.prefetch_related("transactions", "batches")
    paginator = Paginator(inventory_items, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    inventory_items = page_obj.object_list
    filter_query = request.GET.copy()
    filter_query.pop("page", None)

    if request.method == "POST":
        form = InventoryItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.hospital = hospital
            item.save()  # This now handles batch creation/sync via form.save() or item.save()
            # We must call form.save(commit=True) or similar to trigger the form's custom save logic.
            # Actually, item.save() only calls InventoryItem.save().
            # To trigger InventoryItemForm.save(commit=True), we should do:
            form.instance = item
            form.save()
            
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
    snapshot = inventory_dashboard_snapshot(hospital)
    all_inventory_items = InventoryItem.objects.filter(hospital=hospital) if hospital else InventoryItem.objects.none()
    context.update(
        {
            "inventory_items": inventory_items,
            "filtered_inventory_count": filtered_inventory_count,
            "inventory_search_query": search_query,
            "inventory_selected_category": selected_category,
            "inventory_selected_stock_filter": selected_stock_filter,
            "inventory_category_choices": InventoryItem.CATEGORY_CHOICES,
            "inventory_quick_filter_counts": {
                "all": snapshot["stats"]["total_items"],
                "low": snapshot["stats"]["low_stock_count"],
                "out": snapshot["stats"]["out_of_stock_count"],
                "syrup": all_inventory_items.filter(category=InventoryItem.CATEGORY_SYRUP).count(),
            },
            "page_obj": page_obj,
            "inventory_filter_querystring": filter_query.urlencode(),
            "form": form,
            "bulk_upload_form": InventoryBulkUploadForm(),
            "restock_form": InventoryRestockForm(),
            **snapshot,
        }
    )
    return render(request, "admin_dashboard/manage_inventory.html", context)


@inventory_access_required
def inventory_insights(request):
    from datetime import date as _date
    from doctor.models import Prescription

    hospital = active_hospital(request)
    today = timezone.localdate()

    period_start_raw = (request.GET.get("start") or "").strip()
    period_end_raw   = (request.GET.get("end")   or "").strip()
    try:
        period_start = _date.fromisoformat(period_start_raw)
    except ValueError:
        period_start = today - timedelta(days=29)
    try:
        period_end = _date.fromisoformat(period_end_raw)
    except ValueError:
        period_end = today

    # Single pass: chart daily maps + per-drug table data
    pharma_income_map = {}
    pharma_profit_map = {}
    drug_totals = {}
    for rx in Prescription.objects.filter(
        visit__hospital=hospital, dispensed=True,
        dispensed_at__date__gte=period_start,
        dispensed_at__date__lte=period_end,
    ).select_related("drug").order_by("dispensed_at"):
        d = rx.dispensed_at.date() if rx.dispensed_at else None
        revenue = rx.total_price or Decimal("0")
        unit_cost = (rx.drug.unit_cost or Decimal("0")) if rx.drug else Decimal("0")
        qty = rx.total_quantity or Decimal("0")
        cost = unit_cost * qty

        if d:
            pharma_income_map[d] = pharma_income_map.get(d, Decimal("0")) + revenue
            pharma_profit_map[d] = pharma_profit_map.get(d, Decimal("0")) + (revenue - cost)

        did = rx.drug_id or 0
        if did not in drug_totals:
            drug_totals[did] = {
                "name": str(rx.drug) if rx.drug else "Unknown",
                "qty": Decimal("0"),
                "revenue": Decimal("0"),
                "cost": Decimal("0"),
                "stock": rx.drug.current_quantity if rx.drug else Decimal("0"),
                "last_dispensed": d,
            }
        drug_totals[did]["qty"] += qty
        drug_totals[did]["revenue"] += revenue
        drug_totals[did]["cost"] += cost
        if d and (drug_totals[did]["last_dispensed"] is None or d > drug_totals[did]["last_dispensed"]):
            drug_totals[did]["last_dispensed"] = d

    for entry in drug_totals.values():
        entry["net_profit"] = entry["revenue"] - entry["cost"]

    # Build daily series — Line 1: Pharmacy Income, Line 2: Pharmacy Gross Profit
    chart_labels, pharma_income_values, pharma_profit_values = [], [], []
    cursor = period_start
    while cursor <= period_end:
        chart_labels.append(cursor.strftime("%d %b"))
        pharma_income_values.append(float(pharma_income_map.get(cursor, Decimal("0"))))
        pharma_profit_values.append(float(pharma_profit_map.get(cursor, Decimal("0"))))
        cursor += timedelta(days=1)

    dispensed_list = sorted(drug_totals.values(), key=lambda x: (x["last_dispensed"] or _date.min), reverse=True)
    drugs_paginator = Paginator(dispensed_list, 10)
    drugs_page_obj = drugs_paginator.get_page(request.GET.get("drugs_page"))

    snapshot = inventory_dashboard_snapshot(hospital)
    context = hospital_admin_context(
        request,
        "hospital_inventory_insights",
        "Inventory Insights",
        "Daily pharmacy income vs net profit, restock priorities, and dispensed drugs.",
    )
    context.update(snapshot)
    context.update({
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "chart_labels_json": json.dumps(chart_labels),
        "pharma_income_values_json": json.dumps(pharma_income_values),
        "pharma_profit_values_json": json.dumps(pharma_profit_values),
        "dispensed_list": drugs_page_obj,
        "drugs_page_obj": drugs_page_obj,
        "date_qs": f"start={period_start.isoformat()}&end={period_end.isoformat()}",
    })
    return render(request, "admin_dashboard/inventory_insights.html", context)


@inventory_access_required
def download_inventory_import_template(request):
    response = HttpResponse(content_type="text/csv")
    filename_date = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="inventory-import-template-{filename_date}.csv"'
    writer = csv.writer(response)
    writer.writerow(INVENTORY_IMPORT_HEADERS)
    return response


@inventory_access_required
@transaction.atomic
def upload_inventory_bulk(request):
    if request.method != "POST":
        return redirect("manage_inventory")

    hospital = active_hospital(request)
    form = InventoryBulkUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Please upload a valid inventory CSV file.")
        return redirect("manage_inventory")

    upload = form.cleaned_data["file"]
    reader = csv.DictReader(TextIOWrapper(upload.file, encoding="utf-8-sig"))
    if not reader.fieldnames:
        messages.error(request, "The uploaded CSV is empty.")
        return redirect("manage_inventory")

    normalized_headers = [str(header).strip() for header in reader.fieldnames]
    missing_headers = [header for header in INVENTORY_IMPORT_HEADERS if header not in normalized_headers]
    if missing_headers:
        messages.error(
            request,
            "The inventory CSV is missing required column(s): " + ", ".join(missing_headers),
        )
        return redirect("manage_inventory")

    created_count = 0
    updated_count = 0
    skipped_count = 0
    row_errors = []

    for line_number, raw_row in enumerate(reader, start=2):
        row = {}
        for key, value in raw_row.items():
            normalized_key = (key or "").strip()
            if isinstance(value, list):
                normalized_value = " ".join((part or "").strip() for part in value if part is not None).strip()
            else:
                normalized_value = (value or "").strip()
            row[normalized_key] = normalized_value
        if not any(row.values()):
            continue

        item_name = row.get("name", "")
        if not item_name:
            skipped_count += 1
            row_errors.append(f"Row {line_number}: name is required.")
            continue

        existing_item = InventoryItem.objects.filter(hospital=hospital, name=item_name).first()
        form_data = build_inventory_import_form_data(row)
        validation_form = InventoryItemForm(form_data, instance=existing_item)
        if not validation_form.is_valid():
            skipped_count += 1
            joined_errors = []
            for field_name, errors in validation_form.errors.items():
                if field_name == "__all__":
                    joined_errors.extend(errors)
                else:
                    joined_errors.extend([f"{field_name}: {error}" for error in errors])
            row_errors.append(f"Row {line_number}: {'; '.join(joined_errors)}")
            continue

        cleaned = validation_form.cleaned_data
        opening_stock = cleaned.get("current_quantity") or Decimal("0")
        batch_number = cleaned.get("opening_batch_number")
        expiry_date = cleaned.get("opening_expiry_date")

        if existing_item:
            # ModelForm._post_clean() set current_quantity to the CSV value on the instance.
            # Restore it so save() doesn't see a change and trigger sync_batches_to_stock —
            # stock is added below via add_or_update_batch instead.
            existing_item.refresh_from_db(fields=["current_quantity"])
            for attr in ["unit_cost", "selling_price", "reorder_level", "is_active", "units_per_pack",
                         "concentration_mg_per_ml", "pack_size_ml", "days_covered_per_pack", "strength_mg_per_unit"]:
                val = cleaned.get(attr)
                if val is not None:
                    setattr(existing_item, attr, val)
            existing_item.save()

            if opening_stock > 0 and batch_number:
                existing_item.add_or_update_batch(
                    batch_number, opening_stock,
                    expiry_date=expiry_date,
                    unit_cost=existing_item.unit_cost or Decimal("0"),
                )
                InventoryTransaction.objects.create(
                    hospital=hospital,
                    item=existing_item,
                    transaction_type=InventoryTransaction.TYPE_RECEIVE,
                    quantity=opening_stock,
                    unit_cost=existing_item.unit_cost or Decimal("0"),
                    performed_by=request.user,
                    notes=f"Imported stock adjustment through bulk inventory upload.",
                )
            updated_count += 1
            continue

        item = validation_form.save(commit=False)
        item.hospital = hospital
        item = validation_form.save() # Uses custom form logic to handle batches
        if opening_stock > 0:
            InventoryTransaction.objects.create(
                hospital=hospital,
                item=item,
                transaction_type=InventoryTransaction.TYPE_RECEIVE,
                quantity=opening_stock,
                unit_cost=item.unit_cost or Decimal("0"),
                performed_by=request.user,
                notes=f"Imported opening stock through bulk inventory upload.",
            )
        created_count += 1

    if created_count or updated_count:
        messages.success(
            request,
            f"Inventory upload complete: {created_count} item(s) created, {updated_count} item(s) updated.",
        )
    if skipped_count:
        preview_errors = " | ".join(row_errors[:4])
        if len(row_errors) > 4:
            preview_errors += " | ..."
        messages.warning(
            request,
            f"{skipped_count} row(s) were skipped. {preview_errors}",
        )
    elif not created_count and not updated_count:
        messages.info(request, "The CSV did not contain any inventory rows to import.")
    return redirect("manage_inventory")


@inventory_access_required
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


@inventory_access_required
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


@inventory_access_required
@transaction.atomic
def restock_inventory_item(request, item_id):
    item = hospital_owned_or_404(InventoryItem, request, pk=item_id)
    if request.method != "POST":
        return redirect("manage_inventory")

    form = InventoryRestockForm(request.POST)
    if not form.is_valid():
        messages.error(request, f"Please fix the restock details for {item.name}.")
        return redirect("manage_inventory")

    quantity_received = form.cleaned_data["quantity_received"]
    new_unit_cost = form.cleaned_data.get("unit_cost")
    batch_number = form.cleaned_data["batch_number"]
    expiry_date = form.cleaned_data.get("expiry_date")
    notes = form.cleaned_data.get("notes") or ""

    if new_unit_cost is not None:
        item.unit_cost = new_unit_cost
    item.save()
    batch = item.add_or_update_batch(
        batch_number,
        quantity_received,
        expiry_date=expiry_date,
        unit_cost=item.unit_cost,
    )

    InventoryTransaction.objects.create(
        hospital=item.hospital,
        item=item,
        transaction_type=InventoryTransaction.TYPE_RECEIVE,
        quantity=quantity_received,
        unit_cost=item.unit_cost or Decimal("0"),
        performed_by=request.user,
        notes=notes or f"Restocked batch {batch.batch_number} through inventory dashboard by {request.user.get_full_name() or request.user.username}.",
    )
    messages.success(request, f"Restocked {item.name} batch {batch.batch_number} with {quantity_received} {item.unit}(s).")
    return redirect("manage_inventory")


@inventory_access_required
def delete_inventory_batch(request, batch_id):
    if request.method != "POST":
        return redirect("manage_inventory")
    batch = get_object_or_404(InventoryBatch, pk=batch_id, item__hospital=active_hospital(request))
    item = batch.item
    batch_number = batch.batch_number
    batch.delete()
    item.recalculate_current_quantity()
    messages.success(request, f"Batch {batch_number} deleted from {item.name}.")
    return redirect("manage_inventory")


@inventory_access_required
def edit_inventory_batch(request, batch_id):
    """Edit batch_number, expiry_date, and unit_cost of an existing inventory batch."""
    batch = get_object_or_404(InventoryBatch, pk=batch_id, item__hospital=active_hospital(request))
    if request.method != "POST":
        return redirect("manage_inventory")

    batch_number = (request.POST.get("batch_number") or "").strip()
    expiry_date_raw = (request.POST.get("expiry_date") or "").strip()
    unit_cost_raw = (request.POST.get("unit_cost") or "").strip()

    if not batch_number:
        messages.error(request, "Batch number cannot be empty.")
        return redirect("manage_inventory")

    # Enforce unique_together: (item, batch_number)
    if InventoryBatch.objects.filter(item=batch.item, batch_number=batch_number).exclude(pk=batch.pk).exists():
        messages.error(request, f"Batch number '{batch_number}' already exists for {batch.item.name}.")
        return redirect("manage_inventory")

    batch.batch_number = batch_number
    if expiry_date_raw:
        from datetime import date
        try:
            batch.expiry_date = date.fromisoformat(expiry_date_raw)
        except ValueError:
            messages.error(request, "Invalid expiry date format.")
            return redirect("manage_inventory")
    else:
        batch.expiry_date = None
    if unit_cost_raw:
        try:
            batch.unit_cost = Decimal(unit_cost_raw)
        except Exception:
            messages.error(request, "Invalid unit cost value.")
            return redirect("manage_inventory")
    batch.save()
    messages.success(request, f"Batch {batch.batch_number} updated.")
    return redirect("manage_inventory")


# ── Reports hub ───────────────────────────────────────────────────────────────

@role_required(User.ROLE_HOSPITAL_ADMIN)
def hospital_reports(request):
    from decimal import Decimal as _Decimal
    from django.db.models import Sum as _Sum
    hospital = active_hospital(request)

    finance_rows = []
    finance_total = _Decimal("0")
    finance_enabled = hospital and hospital.has_module("finance") if hospital else False

    if finance_enabled:
        try:
            from finance.models import Account, JournalLine
            from django.utils import timezone as _tz
            today = _tz.localdate()
            date_from = request.GET.get("from", today.replace(day=1).isoformat())
            date_to = request.GET.get("to", today.isoformat())
            revenue_accounts = Account.objects.filter(
                hospital=hospital, account_type=Account.TYPE_REVENUE, is_active=True
            ).order_by("code")
            for acc in revenue_accounts:
                total = (
                    JournalLine.objects.filter(
                        account=acc,
                        entry__date__gte=date_from,
                        entry__date__lte=date_to,
                        entry__is_reversal=False,
                    ).aggregate(t=_Sum("credit"))["t"] or _Decimal("0")
                )
                finance_rows.append({"account": acc, "total": total})
                finance_total += total
        except Exception:
            finance_enabled = False
        else:
            pass
    else:
        date_from = date_to = ""

    context = hospital_admin_context(
        request,
        "hospital_reports",
        "Reports",
        "Data insights and exports for clinical and financial analysis.",
    )
    LEDGER_LINKS = [
        ("Finance Dashboard", "finance_dashboard", "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"),
        ("Journal Entries", "finance_journal", "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"),
        ("Cashbook", "finance_cashbook", "M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"),
        ("Trial Balance", "finance_trial_balance", "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"),
        ("Profit & Loss", "finance_pnl", "M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"),
        ("Balance Sheet", "finance_balance_sheet", "M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3"),
        ("Debtor Ledger", "finance_debtors", "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"),
    ]

    context.update({
        "finance_enabled": finance_enabled,
        "finance_rows": finance_rows,
        "finance_total": finance_total,
        "date_from": date_from if finance_enabled else "",
        "date_to": date_to if finance_enabled else "",
        "ledger_links": LEDGER_LINKS,
    })
    return render(request, "admin_dashboard/reports_index.html", context)


@role_required(User.ROLE_HOSPITAL_ADMIN)
def report_consultations(request):
    from datetime import date as _date
    from doctor.models import Consultation
    import urllib.parse as _urlparse

    hospital = active_hospital(request)
    today = timezone.localdate()

    try:
        date_start = _date.fromisoformat((request.GET.get("start") or "").strip())
    except ValueError:
        date_start = today.replace(day=1)
    try:
        date_end = _date.fromisoformat((request.GET.get("end") or "").strip())
    except ValueError:
        date_end = today

    doctor_id_raw = (request.GET.get("doctor") or "").strip()
    try:
        filter_doctor_id = int(doctor_id_raw)
    except ValueError:
        filter_doctor_id = None

    qs = (
        Consultation.objects.filter(
            visit__hospital=hospital,
            created_at__date__gte=date_start,
            created_at__date__lte=date_end,
        )
        .select_related("visit__patient", "created_by")
        .order_by("-created_at")
    )
    if filter_doctor_id:
        qs = qs.filter(created_by_id=filter_doctor_id)

    total_consultations = qs.count()
    unique_patients = qs.values("visit__patient_id").distinct().count()

    # CSV export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="patients-seen-{date_start}-{date_end}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(["#", "Date", "Patient", "Age", "Sex", "Doctor", "Follow-up Date"])
        for i, c in enumerate(qs, 1):
            writer.writerow([
                i,
                c.created_at.strftime("%Y-%m-%d %H:%M"),
                c.visit.patient.name,
                c.visit.patient.age,
                c.visit.patient.get_sex_display(),
                c.created_by.get_full_name() if c.created_by else "—",
                c.follow_up_date or "",
            ])
        return response

    doctors = (
        User.objects.filter(
            hospital=hospital,
            role=User.ROLE_DOCTOR,
            is_active=True,
        ).order_by("first_name", "last_name")
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    filter_params = {"start": date_start.isoformat(), "end": date_end.isoformat()}
    if filter_doctor_id:
        filter_params["doctor"] = filter_doctor_id
    filter_qs = _urlparse.urlencode(filter_params)

    context = hospital_admin_context(
        request,
        "hospital_reports",
        "Patients Seen Report",
        "Patients seen by doctors within the selected period.",
    )
    context.update({
        "consultations": page_obj,
        "page_obj": page_obj,
        "doctors": doctors,
        "date_start": date_start.isoformat(),
        "date_end": date_end.isoformat(),
        "filter_doctor_id": filter_doctor_id or "",
        "filter_qs": filter_qs,
        "total_consultations": total_consultations,
        "unique_patients": unique_patients,
    })
    return render(request, "admin_dashboard/report_consultations.html", context)


@inventory_access_required
def download_inventory_report(request):
    hospital = active_hospital(request)
    inventory_items = (
        InventoryItem.objects.filter(hospital=hospital).order_by("category", "name").prefetch_related("batches")
        if hospital
        else InventoryItem.objects.none()
    )
    report_rows, _ = inventory_report_rows(inventory_items)

    response = HttpResponse(content_type="text/csv")
    filename_date = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="inventory-report-{filename_date}.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "Item Name",
            "Category",
            "Pack Type",
            "Base Unit",
            "Units Per Pack",
            "Batch Number",
            "Batch Quantity",
            "Batch Expiry Date",
            "Batch Unit Cost",
            "Current Stock",
            "Minimum Stock Level",
            "Buying Price",
            "Selling Price",
            "Estimated Stock Cost",
            "Estimated Stock Retail",
            "Status",
        ]
    )

    for row in report_rows:
        writer.writerow(
            [
                row["name"],
                row["category"],
                row["pack_type"],
                row["base_unit"],
                row["units_per_pack"],
                row["batch_number"],
                row["batch_quantity"],
                row["batch_expiry_date"],
                row["batch_unit_cost"],
                row["current_stock"],
                row["minimum_stock"],
                row["buying_price"],
                row["selling_price"],
                row["stock_cost"],
                row["stock_retail"],
                row["status"],
            ]
        )

    return response


@inventory_access_required
def printable_inventory_report(request):
    hospital = active_hospital(request)
    inventory_items = (
        InventoryItem.objects.filter(hospital=hospital).order_by("category", "name").prefetch_related("batches")
        if hospital
        else InventoryItem.objects.none()
    )
    printable_rows, totals = inventory_printable_rows(inventory_items)
    context = hospital_admin_context(
        request,
        "hospital_inventory",
        "Printable Inventory Stock Sheet",
        "Use your browser print dialog to save this page as PDF or hand it to the procurement team as a clean stock sheet.",
    )
    context.update(
        {
            "report_rows": printable_rows,
            "totals": totals,
            "estimated_margin": totals["stock_retail"] - totals["stock_cost"],
            "generated_on": timezone.localtime(),
        }
    )
    return render(request, "admin_dashboard/inventory_printable_report.html", context)


@inventory_access_required
def download_inventory_xlsx(request):
    hospital = active_hospital(request)
    inventory_items = (
        InventoryItem.objects.filter(hospital=hospital).order_by("category", "name").prefetch_related("batches")
        if hospital
        else InventoryItem.objects.none()
    )
    report_rows, totals = inventory_report_rows(inventory_items)
    workbook_bytes = build_inventory_xlsx_bytes(report_rows, totals)

    response = HttpResponse(
        workbook_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    filename_date = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="inventory-report-{filename_date}.xlsx"'
    return response


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
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


@finance_access_required
def receipts_list(request):
    hospital = active_hospital(request)
    bank_accounts = BankAccount.objects.filter(hospital=hospital, is_active=True).order_by("bank_name", "account_name") if hospital else BankAccount.objects.none()
    mobile_accounts = (
        MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True).order_by("provider", "number")
        if hospital
        else MobileMoneyAccount.objects.none()
    )
    payments = (
        Payment.objects.filter(visit__hospital=hospital, paid_at__isnull=False, amount_paid__gt=0)
        .exclude(status=Payment.STATUS_WAIVED)
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


# =====================================
# SUPERADMIN VIEWS - Module Management
# =====================================

@role_required(User.ROLE_SUPERADMIN)
def manage_modules(request):
    """Superadmin view to see and edit module prices."""
    from .forms import ModuleForm
    from django.db.models import Count as _Count
    modules_qs = Module.objects.annotate(
        subscriber_count=_Count(
            "hospital_subscriptions",
            filter=models.Q(hospital_subscriptions__is_active=True),
        )
    ).order_by("display_order", "name")

    module_rows = [
        {
            "module": m,
            "subscriber_count": m.subscriber_count,
            "expected_monthly": m.monthly_price * m.subscriber_count,
        }
        for m in modules_qs
    ]
    platform_total = sum(r["expected_monthly"] for r in module_rows)

    return render(request, "admin_dashboard/manage_modules.html", {
        "active_nav": "superadmin_modules",
        "dashboard_title": "Module Prices",
        "dashboard_title": "Module Pricing",
        "dashboard_intro": "Set the monthly price for each platform module. Changes take effect on the next invoice generated.",
        "module_rows": module_rows,
        "platform_total": platform_total,
    })


@role_required(User.ROLE_SUPERADMIN)
def edit_module(request, module_id):
    from .forms import ModuleForm
    module = get_object_or_404(Module, pk=module_id)
    form = ModuleForm(request.POST or None, instance=module)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"Module '{module.name}' updated — price set to UGX {module.monthly_price}/mo.")
            return redirect("manage_modules")
        messages.error(request, "Please fix the details below.")
    context = superadmin_context(
        request, "superadmin_modules", f"Edit Module — {module.name}",
        "Update the module name, monthly price, or active state.",
    )
    context.update({"form": form, "object_label": module.name, "cancel_url": "manage_modules"})
    return render(request, "admin_dashboard/object_form.html", context)


# =====================================
# SUPERADMIN VIEWS - Invoices
# =====================================

@role_required(User.ROLE_SUPERADMIN)
def generate_invoice(request, hospital_id):
    """Generate an invoice for a hospital based on its current active module subscriptions."""
    if request.method != "POST":
        return redirect("manage_hospitals")

    from accounts.models import HospitalInvoice
    hospital = get_object_or_404(Hospital, pk=hospital_id)

    period_start_raw = request.POST.get("period_start", "").strip()
    period_end_raw   = request.POST.get("period_end", "").strip()

    try:
        period_start = timezone.datetime.fromisoformat(period_start_raw).date()
        period_end   = timezone.datetime.fromisoformat(period_end_raw).date()
    except (ValueError, TypeError):
        today = timezone.now().date()
        period_start = today.replace(day=1)
        period_end   = hospital.subscription_end_date or today

    active_modules = Module.objects.filter(
        hospital_subscriptions__hospital=hospital,
        hospital_subscriptions__is_active=True,
    ).order_by("display_order")

    total = sum((m.monthly_price for m in active_modules), Decimal("0"))

    invoice = HospitalInvoice.objects.create(
        hospital=hospital,
        period_start=period_start,
        period_end=period_end,
        total_amount=total,
        generated_by=request.user,
    )
    return redirect("invoice_print", invoice_id=invoice.pk)


@role_required(User.ROLE_SUPERADMIN)
def invoice_print(request, invoice_id):
    from accounts.models import HospitalInvoice
    invoice = get_object_or_404(HospitalInvoice, pk=invoice_id)
    active_modules = Module.objects.filter(
        hospital_subscriptions__hospital=invoice.hospital,
        hospital_subscriptions__is_active=True,
    ).order_by("display_order")
    return render(request, "admin_dashboard/invoice_print.html", {
        "invoice": invoice,
        "hospital": invoice.hospital,
        "active_modules": active_modules,
    })


@role_required(User.ROLE_SUPERADMIN)
def invoice_list(request, hospital_id):
    from accounts.models import HospitalInvoice
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    invoices = HospitalInvoice.objects.filter(hospital=hospital).order_by("-generated_at")
    return render(request, "admin_dashboard/invoice_list.html", {
        "active_nav": "superadmin_hospitals",
        "hospital": hospital,
        "invoices": invoices,
    })


@role_required(User.ROLE_SUPERADMIN)
def superadmin_invoices(request):
    from accounts.models import HospitalInvoice
    hospitals = Hospital.objects.order_by("name")
    selected_hospital_id = request.GET.get("hospital", "")
    qs = HospitalInvoice.objects.select_related("hospital").order_by("-generated_at")
    if selected_hospital_id:
        qs = qs.filter(hospital_id=selected_hospital_id)

    # Annotate paid status by checking if any payment period overlaps the invoice period
    invoice_list_data = []
    for inv in qs:
        is_paid = HospitalSubscriptionPayment.objects.filter(
            hospital=inv.hospital,
            period_start__lte=inv.period_end,
            period_end__gte=inv.period_start,
        ).exists()
        inv.is_paid = is_paid
        invoice_list_data.append(inv)

    context = superadmin_context(
        request,
        "superadmin_invoices",
        "Invoices",
        "All invoices generated across hospitals.",
    )
    context.update({
        "invoices": invoice_list_data,
        "hospitals": hospitals,
        "selected_hospital_id": selected_hospital_id,
    })
    return render(request, "admin_dashboard/superadmin_invoices.html", context)


@role_required(User.ROLE_SUPERADMIN)
def superadmin_receipts(request):
    hospitals = Hospital.objects.order_by("name")
    selected_hospital_id = request.GET.get("hospital", "")
    qs = HospitalSubscriptionPayment.objects.select_related("hospital").exclude(receipt_number="").order_by("-paid_at")
    if selected_hospital_id:
        qs = qs.filter(hospital_id=selected_hospital_id)

    context = superadmin_context(
        request,
        "superadmin_receipts",
        "Receipts",
        "Payment receipts issued to hospitals.",
    )
    context.update({
        "payments": qs,
        "hospitals": hospitals,
        "selected_hospital_id": selected_hospital_id,
    })
    return render(request, "admin_dashboard/superadmin_receipts.html", context)


@role_required(User.ROLE_SUPERADMIN)
def receipt_print(request, payment_id):
    payment = get_object_or_404(HospitalSubscriptionPayment, pk=payment_id)
    active_modules = Module.objects.filter(
        hospital_subscriptions__hospital=payment.hospital,
        hospital_subscriptions__is_active=True,
    ).order_by("display_order")
    return render(request, "admin_dashboard/receipt_print.html", {
        "payment": payment,
        "hospital": payment.hospital,
        "active_modules": active_modules,
    })


@role_required(User.ROLE_SUPERADMIN)
def hospital_modules_json(request, hospital_id):
    import json as _json
    from django.http import JsonResponse
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    active_ids = set(
        hospital.module_subscriptions.filter(is_active=True).values_list("module_id", flat=True)
    )
    modules = Module.objects.order_by("display_order")
    data = [
        {
            "id": m.pk,
            "name": m.name,
            "monthly_price": float(m.monthly_price),
            "is_core": m.is_core,
            "active": m.pk in active_ids,
        }
        for m in modules
    ]
    today = timezone.now().date().isoformat()
    return JsonResponse({"modules": data, "today": today})


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
                    form.save_subscription_end_date(hospital)
                    form.save_module_subscriptions(hospital)
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
    context.update({
        "hospitals": hospitals,
        "form": form,
        "query": query,
        "all_modules": Module.objects.filter(is_active=True),
        "today": timezone.now().date(),
    })
    return render(request, "admin_dashboard/manage_hospitals.html", context)


@role_required(User.ROLE_SUPERADMIN)
def edit_hospital(request, hospital_id):
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    form = HospitalForm(request.POST or None, request.FILES or None, instance=hospital, require_admin_credentials=False)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            form.save_subscription_end_date(hospital)
            form.save_module_subscriptions(hospital)
            messages.success(request, f"Hospital '{hospital.name}' updated.")
            return redirect("manage_hospitals")
        messages.error(request, "Please fix the hospital details below.")

    context = superadmin_context(
        request,
        "superadmin_hospitals",
        f"Edit Hospital — {hospital.name}",
        "Update hospital details, modules, and subscription period.",
    )
    context.update({
        "form": form,
        "hospital": hospital,
        "all_modules": Module.objects.filter(is_active=True),
        "today": timezone.now().date(),
    })
    return render(request, "admin_dashboard/edit_hospital.html", context)


@role_required(User.ROLE_SUPERADMIN)
def toggle_hospital_active(request, hospital_id):
    """One-click toggle: reactivate (paid) or deactivate a hospital subscription."""
    if request.method != "POST":
        return redirect("manage_hospitals")
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    from datetime import date, timedelta
    if hospital.is_active:
        hospital.is_active = False
        messages.warning(request, f"'{hospital.name}' has been deactivated.")
    else:
        hospital.is_active = True
        today = date.today()
        if not hospital.subscription_end_date or hospital.subscription_end_date < today:
            hospital.subscription_end_date = today + timedelta(days=30)
        messages.success(request, f"'{hospital.name}' reactivated — subscription extended to {hospital.subscription_end_date}.")
    hospital.save(update_fields=["is_active", "subscription_end_date"])
    return redirect("manage_hospitals")


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
    hospitals = Hospital.objects.order_by("name")

    if request.method == "POST":
        hospital_id = request.POST.get("hospital")
        amount_raw = request.POST.get("amount", "0")
        period_start_raw = request.POST.get("period_start", "")
        period_end_raw = request.POST.get("period_end", "")
        months_paid_raw = request.POST.get("months_paid", "1")
        notes = request.POST.get("notes", "")

        try:
            hospital = Hospital.objects.get(pk=hospital_id)
            amount = Decimal(amount_raw)
            period_start = timezone.datetime.fromisoformat(period_start_raw).date()
            period_end = timezone.datetime.fromisoformat(period_end_raw).date()
            months_paid = max(1, int(months_paid_raw))
        except Exception:
            messages.error(request, "Invalid payment details. Please check the form and try again.")
            context = superadmin_context(request, "superadmin_payments", "Subscription Payments", "")
            context.update({"payments": payments, "hospitals": hospitals, "duration_options": [1, 3, 6, 12]})
            return render(request, "admin_dashboard/manage_subscription_payments.html", context)

        payment = HospitalSubscriptionPayment.objects.create(
            hospital=hospital,
            amount=amount,
            months_paid=months_paid,
            period_start=period_start,
            period_end=period_end,
            notes=notes,
        )
        # Extend subscription end date by months_paid
        base = hospital.subscription_end_date if (hospital.subscription_end_date and hospital.subscription_end_date >= period_start) else period_start
        month = base.month - 1 + months_paid
        year = base.year + month // 12
        month = month % 12 + 1
        import calendar as _cal
        day = min(base.day, _cal.monthrange(year, month)[1])
        from datetime import date as _date
        hospital.subscription_end_date = _date(year, month, day)
        hospital.save(update_fields=["subscription_end_date"])

        messages.success(request, f"Payment recorded and receipt {payment.receipt_number} generated.")
        return redirect("receipt_print", payment_id=payment.pk)

    context = superadmin_context(
        request,
        "superadmin_payments",
        "Subscription Payments",
        "Record hospital subscription payments.",
    )
    context.update({"payments": payments, "hospitals": hospitals, "duration_options": [1, 3, 6, 12]})
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
