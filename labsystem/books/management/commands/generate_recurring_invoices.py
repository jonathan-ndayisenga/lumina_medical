from django.core.management.base import BaseCommand

from books.planning_models import RecurringInvoice


class Command(BaseCommand):
    help = "Issue every recurring (monthly) invoice that has fallen due. Safe to run daily."

    def handle(self, *args, **options):
        issued, errors = RecurringInvoice.generate_all_due()
        for invoice in issued:
            self.stdout.write(f"Issued {invoice.number} to {invoice.client.name} for UGX {invoice.total:,.0f}")
        for error in errors:
            self.stderr.write(error)
        self.stdout.write(self.style.SUCCESS(f"{len(issued)} invoice(s) issued."))
