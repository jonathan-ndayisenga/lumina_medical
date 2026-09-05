from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from books.document_models import CompanySettings, Product
from books.ledger_models import Account, FinancialYear


ACCOUNTS = [
    # code, name, type, role, is_direct_cost, is_deductible, is_payment_account
    ("1000", "Bank", "asset", "bank", False, True, True),
    ("1001", "Cash", "asset", "cash", False, True, True),
    ("1002", "MTN Mobile Money", "asset", "momo_mtn", False, True, True),
    ("1003", "Airtel Mobile Money", "asset", "momo_airtel", False, True, True),
    ("1010", "Accounts receivable", "asset", "ar", False, True, False),
    ("1030", "Withholding tax receivable", "asset", "wht", False, True, False),
    ("1090", "Prepayments", "asset", "", False, True, False),
    ("1200", "Computer equipment", "asset", "", False, True, False),
    ("1210", "Office equipment", "asset", "", False, True, False),
    ("1290", "Accumulated depreciation", "asset", "", False, True, False),

    ("2010", "VAT payable", "liability", "vat", False, True, False),
    ("2020", "Customer credits", "liability", "credits", False, True, False),
    ("2030", "Accounts payable", "liability", "ap", False, True, False),
    ("2040", "Deferred revenue", "liability", "deferred", False, True, False),
    ("2100", "Director's current account", "liability", "director", False, True, False),
    ("2200", "PAYE payable", "liability", "", False, True, False),
    ("2210", "NSSF payable", "liability", "", False, True, False),

    ("3000", "Share capital", "equity", "", False, True, False),
    ("3010", "Retained earnings", "equity", "retained", False, True, False),
    ("3020", "Director's drawings", "equity", "", False, True, False),

    ("4000", "Ternah Health subscriptions", "income", "", False, True, False),
    ("4010", "Mykashop subscriptions", "income", "", False, True, False),
    ("4020", "Factory Mode subscriptions", "income", "", False, True, False),
    ("4030", "Property/REMS subscriptions", "income", "", False, True, False),
    ("4040", "SACCO platform subscriptions", "income", "", False, True, False),
    ("4050", "Pharmacy subscriptions", "income", "", False, True, False),
    ("4060", "Onboarding fees", "income", "", False, True, False),
    ("4070", "Website builds", "income", "", False, True, False),
    ("4080", "Custom development", "income", "", False, True, False),
    ("4090", "Support retainers", "income", "", False, True, False),

    ("5000", "Hosting & servers", "expense", "", True, True, False),
    ("5010", "Domain names", "expense", "", True, True, False),
    ("5020", "Third-party APIs", "expense", "", True, True, False),
    ("5030", "Software licences", "expense", "", True, True, False),
    ("5040", "Contractors & freelancers", "expense", "", True, True, False),
    ("5050", "SMS / email gateway costs", "expense", "", True, True, False),
    ("5900", "Bank & MoMo transaction charges", "expense", "charges", False, True, False),

    ("6000", "Salaries & wages", "expense", "", False, True, False),
    ("6010", "NSSF employer contribution", "expense", "", False, True, False),
    ("6020", "Rent", "expense", "", False, True, False),
    ("6030", "Transport & fuel", "expense", "", False, True, False),
    ("6040", "Marketing & advertising", "expense", "", False, True, False),
    ("6050", "Professional fees (legal/audit)", "expense", "", False, True, False),
    ("6060", "Bank charges", "expense", "", False, True, False),
    ("6070", "Depreciation", "expense", "", False, True, False),
    ("6080", "Office supplies", "expense", "", False, True, False),
    ("6090", "Utilities", "expense", "", False, True, False),
    ("6100", "Telephone & data", "expense", "", False, True, False),
    ("6110", "Insurance", "expense", "", False, True, False),
    ("6120", "Repairs & maintenance", "expense", "", False, True, False),
    ("6130", "Staff welfare", "expense", "", False, True, False),
    ("6140", "Training & development", "expense", "", False, True, False),
    ("6150", "Subscriptions & memberships", "expense", "", False, True, False),
    ("6160", "Postage & courier", "expense", "", False, True, False),
    ("6170", "Printing & stationery", "expense", "", False, True, False),
    ("6180", "Foreign exchange loss", "expense", "fx", False, True, False),

    ("6900", "Fines & penalties", "expense", "", False, False, False),
    ("6910", "Entertainment", "expense", "", False, False, False),
    ("6920", "Private / non-business spending", "expense", "", False, False, False),
]

FINANCIAL_YEARS = [
    ("2024/25", date(2024, 7, 1), date(2025, 6, 30)),
    ("2025/26", date(2025, 7, 1), date(2026, 6, 30)),
    ("2026/27", date(2026, 7, 1), date(2027, 6, 30)),
    ("2027/28", date(2027, 7, 1), date(2028, 6, 30)),
]

PRODUCTS = [
    ("Ternah Health", "ternah-health", "4000"),
    ("Mykashop", "mykashop", "4010"),
    ("Factory Mode", "factory-mode", "4020"),
    ("Property / REMS", "property-rems", "4030"),
    ("SACCO Platform", "sacco-platform", "4040"),
    ("Pharmacy", "pharmacy", "4050"),
    ("Onboarding", "onboarding", "4060"),
    ("Website Build", "website-build", "4070"),
    ("Custom Development", "custom-development", "4080"),
    ("Support Retainer", "support-retainer", "4090"),
]


class Command(BaseCommand):
    help = "Seed (or update, by code — never duplicate) the Ternah Books chart of accounts, financial years and products."

    @transaction.atomic
    def handle(self, *args, **options):
        created_accounts = 0
        for code, name, type_, role, is_direct_cost, is_deductible, is_payment_account in ACCOUNTS:
            account, created = Account.objects.update_or_create(
                code=code,
                defaults=dict(
                    name=name, type=type_, role=role,
                    is_direct_cost=is_direct_cost, is_deductible=is_deductible,
                    is_payment_account=is_payment_account,
                ),
            )
            created_accounts += int(created)
        self.stdout.write(f"Chart of accounts: {len(ACCOUNTS)} rows ({created_accounts} new).")

        created_years = 0
        for label, start_date, end_date in FINANCIAL_YEARS:
            _, created = FinancialYear.objects.update_or_create(
                label=label, defaults=dict(start_date=start_date, end_date=end_date),
            )
            created_years += int(created)
        self.stdout.write(f"Financial years: {len(FINANCIAL_YEARS)} rows ({created_years} new).")

        created_products = 0
        for name, code, account_code in PRODUCTS:
            revenue_account = Account.objects.get(code=account_code)
            _, created = Product.objects.update_or_create(
                code=code, defaults=dict(name=name, revenue_account=revenue_account),
            )
            created_products += int(created)
        self.stdout.write(f"Products: {len(PRODUCTS)} rows ({created_products} new).")

        settings_row = CompanySettings.load()
        missing = settings_row.missing_fields_for_invoicing()
        if missing:
            self.stdout.write(self.style.WARNING(f"Company letterhead still missing: {', '.join(missing)}."))
        else:
            self.stdout.write(self.style.SUCCESS("Company letterhead is complete."))
