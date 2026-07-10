"""
Default Chart of Accounts for a new hospital.
Call provision_chart_of_accounts(hospital) on hospital creation or
via the management command setup_finance_accounts.
"""

from .models import Account

STANDARD_ACCOUNTS = [
    # ── ASSETS ──────────────────────────────────────────────────────────────
    {"code": "1010", "name": "Cash on Hand",           "account_type": Account.TYPE_ASSET,     "sub_type": Account.SUB_CASH},
    {"code": "1020", "name": "Bank Account",           "account_type": Account.TYPE_ASSET,     "sub_type": Account.SUB_BANK},
    {"code": "1030", "name": "Mobile Money",           "account_type": Account.TYPE_ASSET,     "sub_type": Account.SUB_MOBILE},
    {"code": "1100", "name": "Accounts Receivable",    "account_type": Account.TYPE_ASSET,     "sub_type": Account.SUB_RECEIVABLE},

    # ── LIABILITIES ─────────────────────────────────────────────────────────
    {"code": "2010", "name": "Accounts Payable",       "account_type": Account.TYPE_LIABILITY, "sub_type": Account.SUB_PAYABLE},
    {"code": "2020", "name": "Patient Deposits",       "account_type": Account.TYPE_LIABILITY, "sub_type": Account.SUB_DEPOSIT},
    {"code": "2030", "name": "VAT Payable",            "account_type": Account.TYPE_LIABILITY, "sub_type": Account.SUB_TAX},

    # ── EQUITY ──────────────────────────────────────────────────────────────
    {"code": "3010", "name": "Owner's Equity",         "account_type": Account.TYPE_EQUITY,    "sub_type": Account.SUB_EQUITY},
    {"code": "3020", "name": "Retained Earnings",      "account_type": Account.TYPE_EQUITY,    "sub_type": Account.SUB_EQUITY},

    # ── REVENUE ─────────────────────────────────────────────────────────────
    {"code": "4001", "name": "Consultation Revenue",   "account_type": Account.TYPE_REVENUE,   "sub_type": "revenue_consultation"},
    {"code": "4002", "name": "Laboratory Revenue",     "account_type": Account.TYPE_REVENUE,   "sub_type": "revenue_lab"},
    {"code": "4003", "name": "Pharmacy Revenue",       "account_type": Account.TYPE_REVENUE,   "sub_type": "revenue_pharmacy"},
    {"code": "4004", "name": "Procedure Revenue",      "account_type": Account.TYPE_REVENUE,   "sub_type": "revenue_procedure"},
    {"code": "4005", "name": "Scan / Ultrasound Revenue", "account_type": Account.TYPE_REVENUE, "sub_type": "revenue_scan"},
    {"code": "4006", "name": "Triage Revenue",         "account_type": Account.TYPE_REVENUE,   "sub_type": "revenue_triage"},
    {"code": "4007", "name": "Other Revenue",          "account_type": Account.TYPE_REVENUE,   "sub_type": "revenue_other"},

    # ── EXPENSES ────────────────────────────────────────────────────────────
    {"code": "5001", "name": "Staff Salaries",         "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_salary"},
    {"code": "5002", "name": "Reagents & Consumables", "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_consumables"},
    {"code": "5003", "name": "Utilities",              "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_utilities"},
    {"code": "5004", "name": "Rent",                   "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_rent"},
    {"code": "5005", "name": "Medicine & Drugs",       "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_medicine"},
    {"code": "5006", "name": "Maintenance & Repairs",  "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_maintenance"},
    {"code": "5007", "name": "Logistics & Transport",  "account_type": Account.TYPE_EXPENSE,   "sub_type": "expense_logistics"},
    {"code": "5008", "name": "Other Expenses",         "account_type": Account.TYPE_EXPENSE,   "sub_type": Account.SUB_EXPENSE},
]


def provision_chart_of_accounts(hospital):
    """
    Create the standard accounts for a hospital if they don't already exist.
    Safe to call multiple times (uses get_or_create).
    Returns (created_count, skipped_count).
    """
    created = 0
    skipped = 0
    for spec in STANDARD_ACCOUNTS:
        _, was_created = Account.objects.get_or_create(
            hospital=hospital,
            code=spec["code"],
            defaults={
                "name": spec["name"],
                "account_type": spec["account_type"],
                "sub_type": spec["sub_type"],
                "is_system": True,
                "is_active": True,
            },
        )
        if was_created:
            created += 1
        else:
            skipped += 1
    return created, skipped
