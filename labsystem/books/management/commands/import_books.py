from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction

from accounts.models import Hospital, User
from books.document_models import Invoice, Payment
from books.reports import trial_balance


class Command(BaseCommand):
    help = "Load a file from export_books into a new, empty Books server. Refuses to touch one that already has invoices or payments."

    def add_arguments(self, parser):
        parser.add_argument("input", help="The JSON file written by export_books.")

    def handle(self, *args, **options):
        if Invoice.objects.exists() or Payment.objects.exists():
            raise CommandError("This database already has invoices or payments. Import only into a new server.")
        with transaction.atomic():
            # Migrations seed a default chart of accounts and tenant; the export replaces them.
            tables = [model._meta.db_table for model in apps.get_app_config("books").get_models()]
            with connection.cursor() as cursor:
                for sql in connection.ops.sql_flush(no_style(), tables, allow_cascade=True):
                    cursor.execute(sql)
            User.objects.filter(hospital__subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN).delete()
            Hospital.objects.filter(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN).delete()
            call_command("loaddata", options["input"], verbosity=0)
        balance = trial_balance()
        self.stdout.write(f"Invoices: {Invoice.objects.count()}  Payments: {Payment.objects.count()}")
        self.stdout.write(f"Trial balance: debits {balance['total_debit']:,.2f}, credits {balance['total_credit']:,.2f}")
        if not balance["balanced"]:
            raise CommandError("The imported trial balance does not balance. Do not switch over.")
        self.stdout.write(self.style.SUCCESS("Imported, and the trial balance balances."))
