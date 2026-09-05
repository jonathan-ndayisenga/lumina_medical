from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from accounts.models import Hospital


class Command(BaseCommand):
    help = "Bootstrap Ternah Books: migrate, seed the chart of accounts/years/products, report what's left to do."

    def handle(self, *args, **options):
        call_command("migrate", "books")
        call_command("seed_books")

        subdomain = settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN
        if Hospital.objects.filter(subdomain=subdomain).exists():
            self.stdout.write(self.style.SUCCESS(f"Tenant hospital '{subdomain}' already exists."))
        else:
            self.stdout.write(self.style.WARNING(
                f"No hospital with subdomain '{subdomain}' exists yet. Create one (any name is fine) via the "
                "usual superadmin 'add hospital' flow, then set a user's role to Hospital Admin on that "
                "hospital to get access to Company Settings inside Ternah Books."
            ))
