from decimal import Decimal

from django.db import migrations


MODULE_SEED = [
    # code, name, monthly_price, is_core, url_name, display_order
    ("reception", "Reception", Decimal("0"), True, "reception_dashboard", 1),
    ("doctor", "Doctor", Decimal("50000"), False, "doctor_queue", 2),
    ("nurse", "Nurse", Decimal("50000"), False, "nurse_queue", 3),
    ("lab", "Lab", Decimal("50000"), False, "lab_queue", 4),
    ("inventory", "Pharmacy / Inventory", Decimal("50000"), False, "manage_inventory", 5),
    ("finance", "Finance", Decimal("50000"), False, "financial_report", 6),
]

NEW_GROUP_NAMES = ["Inventory", "Finance"]


def seed_modules_and_backfill(apps, schema_editor):
    Module = apps.get_model("accounts", "Module")
    Hospital = apps.get_model("accounts", "Hospital")
    HospitalModuleSubscription = apps.get_model("accounts", "HospitalModuleSubscription")
    Group = apps.get_model("auth", "Group")

    modules = {}
    for code, name, price, is_core, url_name, order in MODULE_SEED:
        module, _ = Module.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "monthly_price": price,
                "is_core": is_core,
                "url_name": url_name,
                "display_order": order,
            },
        )
        modules[code] = module

    for name in NEW_GROUP_NAMES:
        Group.objects.get_or_create(name=name)

    # Backfill: every existing hospital already uses all of these modules today
    # (they were never gated before). Subscribe them to everything so nobody
    # gets locked out the moment this migration runs.
    for hospital in Hospital.objects.all().iterator():
        for module in modules.values():
            HospitalModuleSubscription.objects.get_or_create(
                hospital=hospital,
                module=module,
                defaults={"is_active": True},
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_add_module_and_subscription_models"),
    ]

    operations = [
        migrations.RunPython(seed_modules_and_backfill, noop),
    ]
