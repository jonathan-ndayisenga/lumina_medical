"""
Reverse journal entries whose source FK has been NULLed by a cascade delete
(e.g. a patient was deleted, which deleted their VisitServices/Payments, which
SET_NULL'd the source_visit_service / source_payment on the JournalEntry before
the post_delete signal could reverse them).

Identifies orphaned entries as:
  - source_type = visit_service  AND  source_visit_service IS NULL
  - source_type = payment         AND  source_payment IS NULL
  - Not already reversed (reversal_of IS NULL  AND  is_reversal = False)

Run with:
    python manage.py reverse_orphaned_entries
    python manage.py reverse_orphaned_entries --dry-run
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from finance.models import JournalEntry, JournalLine


class Command(BaseCommand):
    help = "Reverse journal entries left orphaned by cascade-deleted patients/visits."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be reversed without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        orphans = JournalEntry.objects.filter(
            is_reversal=False,
            reversal_of__isnull=True,
            source_type__in=[
                JournalEntry.SOURCE_VISIT_CHARGE,
                JournalEntry.SOURCE_PAYMENT,
            ],
        ).filter(
            # source FK was NULLed (cascade deleted) but entry was never reversed
            source_visit_service__isnull=True,
            source_payment__isnull=True,
        ).select_related("hospital")

        count = orphans.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No orphaned entries found. Ledger is clean."))
            return

        self.stdout.write(f"Found {count} orphaned entr{'y' if count == 1 else 'ies'}.")

        reversed_count = 0
        for entry in orphans:
            lines = list(entry.lines.all())
            if not lines:
                self.stdout.write(f"  SKIP {entry.reference} — no lines")
                continue

            self.stdout.write(
                f"  {'DRY-RUN ' if dry_run else ''}Reverse {entry.reference} | "
                f"{entry.description} | {entry.date}"
            )

            if not dry_run:
                reversal = JournalEntry.objects.create(
                    hospital=entry.hospital,
                    date=timezone.localdate(),
                    description=f"Reversal of {entry.reference} (orphan cleanup)",
                    source_type=JournalEntry.SOURCE_REVERSAL,
                    is_reversal=True,
                )
                for line in lines:
                    JournalLine.objects.create(
                        entry=reversal,
                        account=line.account,
                        debit=line.credit,
                        credit=line.debit,
                    )
                reversal.reversed_entry = entry
                reversal.save(update_fields=["reversed_entry"])

            reversed_count += 1

        label = "Would reverse" if dry_run else "Reversed"
        self.stdout.write(
            self.style.SUCCESS(f"{label} {reversed_count} orphaned journal entr{'y' if reversed_count == 1 else 'ies'}.")
        )
