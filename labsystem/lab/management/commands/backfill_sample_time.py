"""
Backfill sample_date time for lab reports that were saved at midnight (00:00:00).

Strategy:
  - Reports linked to a visit  → use visit.visit_date time (when the visit started)
  - Standalone reports         → use report.created_at time (when the report was entered)

The date portion of sample_date is kept unchanged; only the time is patched.
"""

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from lab.models import LabReport


class Command(BaseCommand):
    help = "Backfill midnight sample_date times from visit_date or created_at."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without saving.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        reports = LabReport.objects.select_related("visit").order_by("id")

        patched = 0
        skipped = 0

        for report in reports:
            sd = report.sample_date
            if sd is None:
                skipped += 1
                continue

            # Compare in UTC — records saved as date-only land at 00:00:00 UTC
            utc_sd = sd.astimezone(datetime.timezone.utc) if timezone.is_aware(sd) else sd
            local_sd = timezone.localtime(sd) if timezone.is_aware(sd) else sd

            if utc_sd.hour != 0 or utc_sd.minute != 0 or utc_sd.second != 0:
                # Already has a real time — leave it alone
                skipped += 1
                continue

            # Pick the time source
            if report.visit_id and report.visit.visit_date:
                source_dt = report.visit.visit_date
                source_label = f"visit #{report.visit_id}"
            else:
                source_dt = report.created_at
                source_label = "created_at"

            if source_dt is None:
                skipped += 1
                continue

            local_source = timezone.localtime(source_dt) if timezone.is_aware(source_dt) else source_dt

            # Keep sample_date's date, take time from source
            new_local = local_sd.replace(
                hour=local_source.hour,
                minute=local_source.minute,
                second=local_source.second,
                microsecond=0,
            )

            # Convert back to aware UTC if original was aware
            if timezone.is_aware(sd):
                new_dt = timezone.make_aware(
                    new_local.replace(tzinfo=None),
                    timezone.get_current_timezone(),
                )
            else:
                new_dt = new_local

            self.stdout.write(
                f"  Report #{report.id} ({report.patient_name}): "
                f"{local_sd.strftime('%Y-%m-%d %H:%M')} -> "
                f"{new_local.strftime('%Y-%m-%d %H:%M')}  [{source_label}]"
            )

            if not dry_run:
                report.sample_date = new_dt
                report.save(update_fields=["sample_date"])

            patched += 1

        verb = "Would patch" if dry_run else "Patched"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{verb} {patched} report(s). Skipped {skipped} (already had time or no source)."
            )
        )
