from django.core.management.base import BaseCommand

from books.delivery import send_due_reminders


class Command(BaseCommand):
    help = "Email payment reminders for invoices near or past their due date. Safe to run daily: each is sent once."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List what would be sent, without sending.")

    def handle(self, *args, **options):
        result = send_due_reminders(dry_run=options["dry_run"])
        for label in result["sent"]:
            self.stdout.write(("Would send " if options["dry_run"] else "Sent ") + label)
        for label in result["skipped"]:
            self.stdout.write(f"Skipped: {label}")
        for label in result["errors"]:
            self.stderr.write(label)
        self.stdout.write(self.style.SUCCESS(f"{len(result['sent'])} reminder(s)."))
