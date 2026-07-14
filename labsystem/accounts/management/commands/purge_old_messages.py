from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import DirectMessage, InternalNotification, InternalNotificationRead, NotificationRead, PlatformSettings, SystemNotification


class Command(BaseCommand):
    help = "Delete messages and notifications older than the platform retention setting (default: 7 days)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override retention days. Defaults to the platform setting (message_retention_days).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days is None:
            days = PlatformSettings.get().message_retention_days

        if days == 0:
            self.stdout.write(self.style.WARNING("Auto-purge is disabled (retention days = 0). Nothing deleted."))
            return

        cutoff = timezone.now() - timedelta(days=days)

        sys_qs = SystemNotification.objects.filter(created_at__lt=cutoff)
        int_qs = InternalNotification.objects.filter(created_at__lt=cutoff)

        sys_read_deleted, _ = NotificationRead.objects.filter(notification__in=sys_qs).delete()
        int_read_deleted, _ = InternalNotificationRead.objects.filter(notification__in=int_qs).delete()

        sys_deleted, _ = sys_qs.delete()
        int_deleted, _ = int_qs.delete()

        dm_qs = DirectMessage.objects.filter(created_at__lt=cutoff)
        dm_deleted, _ = dm_qs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Purged: {sys_deleted} broadcast(s), {int_deleted} internal message(s), "
                f"{dm_deleted} direct message(s), "
                f"{sys_read_deleted + int_read_deleted} read record(s) older than {days} days."
            )
        )
