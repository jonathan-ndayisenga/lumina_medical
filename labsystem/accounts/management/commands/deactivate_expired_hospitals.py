"""
Management command: deactivate_expired_hospitals

Run this daily (e.g. via DigitalOcean App Platform Console or a scheduled job):

    python manage.py deactivate_expired_hospitals

It sets is_active=False on any hospital whose subscription_end_date has passed.
Deactivated hospitals are immediately blocked by HospitalMiddleware.

To renew: update subscription_end_date via the Edit Hospital form in the
superadmin panel and re-enable is_active.
"""

from datetime import date

from django.core.management.base import BaseCommand

from accounts.models import Hospital


class Command(BaseCommand):
    help = "Deactivate hospitals whose subscription_end_date has passed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print which hospitals would be deactivated without actually doing it.",
        )

    def handle(self, *args, **options):
        today = date.today()
        dry_run = options["dry_run"]

        expired = Hospital.objects.filter(
            subscription_end_date__lt=today,
            is_active=True,
        )

        count = expired.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No expired hospitals found. All subscriptions are current."))
            return

        for hospital in expired:
            self.stdout.write(
                f"{'[DRY RUN] Would deactivate' if dry_run else 'Deactivating'}: "
                f"{hospital.name} (expired {hospital.subscription_end_date})"
            )

        if not dry_run:
            expired.update(is_active=False)
            self.stdout.write(
                self.style.WARNING(f"Deactivated {count} hospital(s) with expired subscriptions.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"[DRY RUN] {count} hospital(s) would be deactivated.")
            )
