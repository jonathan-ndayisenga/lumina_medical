from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copy uploads from the server's local media folder into Spaces. Files already there are left alone."

    def handle(self, *args, **options):
        if not settings.SPACES_ENABLED:
            raise CommandError("Set the SPACES_* settings first, so uploads have somewhere to go.")
        root = Path(settings.MEDIA_ROOT)
        copied = skipped = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            if default_storage.exists(name):
                skipped += 1
                continue
            with path.open("rb") as handle:
                default_storage.save(name, File(handle))
            copied += 1
            self.stdout.write(f"Copied {name}")
        self.stdout.write(self.style.SUCCESS(f"{copied} copied, {skipped} already there."))
