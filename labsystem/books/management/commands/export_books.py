import json

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Hospital, User


class Command(BaseCommand):
    help = (
        "Write Ternah Books' data and its staff logins to a JSON file, for moving Books to its own server. "
        "No hospital or patient data is included. The file holds password hashes and financial records: "
        "keep it private and delete it after the move."
    )

    def add_arguments(self, parser):
        parser.add_argument("output", help="Where to write the JSON file.")

    def handle(self, *args, **options):
        tenant = Hospital.objects.filter(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN).first()
        if tenant is None:
            raise CommandError("There is no Ternah Books tenant in this database.")
        users = list(User.objects.filter(hospital=tenant))
        user_ids = {user.pk for user in users}

        for field in Hospital._meta.concrete_fields:
            if field.is_relation:
                setattr(tenant, field.name, None)
        objects = [tenant, *users]
        for model in apps.get_app_config("books").get_models():
            objects.extend(model.objects.all())

        rows = json.loads(serializers.serialize("json", objects))
        for row, obj in zip(rows, objects):
            for field in obj._meta.concrete_fields:
                if not field.is_relation or field.related_model._meta.app_label == "books":
                    continue
                value = row["fields"].get(field.name)
                # The new server has no customer hospitals, and only Books' own staff.
                if field.related_model is User and value in user_ids:
                    continue
                if field.related_model is Hospital and value == tenant.pk:
                    continue
                row["fields"][field.name] = None
            for field in obj._meta.many_to_many:
                if field.related_model._meta.app_label != "books":
                    row["fields"][field.name] = []

        with open(options["output"], "w", encoding="utf-8") as handle:
            json.dump(rows, handle)
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} records to {options['output']}."))
