from django.core.management.base import BaseCommand

from finance.posting import retry_posting_failures


class Command(BaseCommand):
    help = "Post again every automatic ledger posting that failed (see finance.PostingFailure)."

    def handle(self, *args, **options):
        fixed, still_failing = retry_posting_failures()
        self.stdout.write(f"Posted {fixed}; still failing {still_failing}.")
