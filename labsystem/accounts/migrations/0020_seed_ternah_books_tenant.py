from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


TEMP_PASSWORD = "ChangeMe-Ternah1!"


def create_ternah_tenant(apps, schema_editor):
    Hospital = apps.get_model("accounts", "Hospital")
    User = apps.get_model("accounts", "User")

    subdomain = getattr(settings, "TERNAH_BOOKS_HOSPITAL_SUBDOMAIN", "ternah-books")

    hospital, _ = Hospital.objects.get_or_create(
        subdomain=subdomain,
        defaults={"name": "Ternah Software Company Ltd"},
    )

    if not User.objects.filter(username="ternah_admin").exists():
        User.objects.create(
            username="ternah_admin",
            hospital=hospital,
            role="hospital_admin",
            is_active=True,
            password=make_password(TEMP_PASSWORD),
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_lab_consumables_datetime_sample"),
    ]

    operations = [
        migrations.RunPython(create_ternah_tenant, noop),
    ]
