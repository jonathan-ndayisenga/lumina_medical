from decimal import Decimal
from django.db import migrations


def add_hospital_management_module(apps, schema_editor):
    Module = apps.get_model("accounts", "Module")
    Hospital = apps.get_model("accounts", "Hospital")
    HospitalModuleSubscription = apps.get_model("accounts", "HospitalModuleSubscription")

    module, created = Module.objects.get_or_create(
        code="hospital_mgmt",
        defaults={
            "name": "Hospital Management",
            "monthly_price": Decimal("50000"),
            "is_core": False,
            "url_name": "hospital_dashboard",
            "display_order": 0,
        },
    )

    # Backfill all existing hospitals — they already use this module
    for hospital in Hospital.objects.all().iterator():
        HospitalModuleSubscription.objects.get_or_create(
            hospital=hospital,
            module=module,
            defaults={"is_active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_seed_modules_and_backfill_hospitals"),
    ]

    operations = [
        migrations.RunPython(add_hospital_management_module, migrations.RunPython.noop),
    ]
