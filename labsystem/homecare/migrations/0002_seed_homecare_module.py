from decimal import Decimal
from django.db import migrations


CLINICAL_MODULE_CODES = {"reception", "doctor", "nurse", "lab", "inventory", "finance"}


def seed_homecare_module(apps, schema_editor):
    Module = apps.get_model("accounts", "Module")
    Group = apps.get_model("auth", "Group")

    Module.objects.get_or_create(
        code="home_care",
        defaults={
            "name": "Home Care Management",
            "monthly_price": Decimal("50000"),
            "is_core": False,
            "url_name": "homecare_dashboard",
            "display_order": 7,
        },
    )
    Group.objects.get_or_create(name="Home Care")


def patch_reception_carve_out(apps, schema_editor):
    """
    Ensure existing hospitals that subscribed ONLY to home_care
    do NOT have Reception force-included.

    Since existing hospitals were backfilled with ALL modules in
    accounts/migrations/0005_seed_modules_and_backfill_hospitals.py,
    no hospital currently has home_care as its sole module — this
    migration is therefore a no-op today but correctly implements
    the business rule for new hospitals created going forward.

    The actual carve-out logic lives in
    admin_dashboard.forms.HospitalForm.save_module_subscriptions(),
    where we skip force-including Reception when the hospital's
    selected modules contain home_care but no clinical modules.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("homecare", "0001_initial_homecare_models"),
        ("accounts", "0005_seed_modules_and_backfill_hospitals"),
    ]

    operations = [
        migrations.RunPython(seed_homecare_module, migrations.RunPython.noop),
        migrations.RunPython(patch_reception_carve_out, migrations.RunPython.noop),
    ]
