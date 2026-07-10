"""
python manage.py setup_finance

Provisions the Chart of Accounts for all hospitals that don't have one yet,
then backfills journal entries for all existing VisitServices, Payments,
and Expenses so the books are complete from day one.

Safe to re-run — already-posted entries are skipped via the reverse-then-repost
logic in the posting engine (it detects existing entries and doesn't double-post).
"""

from django.core.management.base import BaseCommand

from accounts.models import Hospital
from finance.accounts_seed import provision_chart_of_accounts
from finance.models import JournalEntry
from finance.posting import post_expense, post_payment, post_visit_service


class Command(BaseCommand):
    help = "Provision Chart of Accounts and backfill journal entries for all hospitals."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hospital",
            type=int,
            help="Only process this hospital ID (omit for all hospitals).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be posted without writing anything.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        hospital_id = options.get("hospital")

        hospitals = Hospital.objects.filter(is_active=True)
        if hospital_id:
            hospitals = hospitals.filter(pk=hospital_id)

        for hospital in hospitals:
            self.stdout.write(f"\n{'-'*60}")
            self.stdout.write(f"Hospital: {hospital.name}")

            # 1. Provision Chart of Accounts
            if dry:
                self.stdout.write("  [dry-run] Would provision Chart of Accounts")
            else:
                created, skipped = provision_chart_of_accounts(hospital)
                self.stdout.write(
                    f"  Chart of Accounts: {created} created, {skipped} already existed"
                )

            # 2. Backfill VisitServices
            from reception.models import VisitService
            services = VisitService.objects.filter(
                visit__hospital=hospital
            ).select_related("visit__patient", "service").order_by("created_at")

            svc_posted = 0
            svc_skipped = 0
            for vs in services:
                already = JournalEntry.objects.filter(
                    source_visit_service=vs, is_reversal=False
                ).exists()
                if already:
                    svc_skipped += 1
                    continue
                if dry:
                    self.stdout.write(
                        f"  [dry-run] Would post charge: {vs.service.name} "
                        f"— {vs.visit.patient.name} UGX {vs.price_at_time}"
                    )
                else:
                    post_visit_service(vs)
                svc_posted += 1

            if not dry:
                self.stdout.write(
                    f"  VisitServices: {svc_posted} posted, {svc_skipped} already in ledger"
                )

            # 3. Backfill Payments
            from reception.models import Payment
            payments = Payment.objects.filter(
                visit__hospital=hospital, amount_paid__gt=0
            ).select_related("visit__patient").order_by("id")

            pmt_posted = 0
            pmt_skipped = 0
            for pmt in payments:
                already = JournalEntry.objects.filter(
                    source_payment=pmt, is_reversal=False
                ).exists()
                if already:
                    pmt_skipped += 1
                    continue
                if dry:
                    self.stdout.write(
                        f"  [dry-run] Would post payment: {pmt.receipt_number} "
                        f"UGX {pmt.amount_paid} ({pmt.mode})"
                    )
                else:
                    post_payment(pmt)
                pmt_posted += 1

            if not dry:
                self.stdout.write(
                    f"  Payments: {pmt_posted} posted, {pmt_skipped} already in ledger"
                )

            # 4. Backfill Expenses
            from admin_dashboard.models import Expense
            expenses = Expense.objects.filter(hospital=hospital).order_by("id")

            exp_posted = 0
            exp_skipped = 0
            for exp in expenses:
                already = JournalEntry.objects.filter(
                    source_expense=exp, is_reversal=False
                ).exists()
                if already:
                    exp_skipped += 1
                    continue
                if dry:
                    self.stdout.write(
                        f"  [dry-run] Would post expense: {exp.description} UGX {exp.amount}"
                    )
                else:
                    post_expense(exp)
                exp_posted += 1

            if not dry:
                self.stdout.write(
                    f"  Expenses: {exp_posted} posted, {exp_skipped} already in ledger"
                )

        self.stdout.write(self.style.SUCCESS("\nDone."))
