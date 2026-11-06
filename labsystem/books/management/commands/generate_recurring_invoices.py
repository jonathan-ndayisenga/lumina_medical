from django.core.management.base import BaseCommand

from books.planning_models import RecurringInvoice


class Command(BaseCommand):
    help = "Make every recurring (monthly) invoice that has fallen due: issued, or a draft, as each plan says. Safe to run daily."

    def handle(self, *args, **options):
        issued, errors = RecurringInvoice.generate_all_due()
        for invoice in issued:
            label = f"Issued {invoice.number}" if invoice.number else "Draft prepared"
            self.stdout.write(f"{label} for {invoice.client.name}: UGX {invoice.total:,.0f}")
        for error in errors:
            self.stderr.write(error)
        self.stdout.write(self.style.SUCCESS(f"{len(issued)} invoice(s) issued or prepared."))
