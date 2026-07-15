from django.db import migrations


def seed_sonographer(apps, schema_editor):
    Module = apps.get_model("accounts", "Module")
    Hospital = apps.get_model("accounts", "Hospital")
    HospitalModuleSubscription = apps.get_model("accounts", "HospitalModuleSubscription")
    Group = apps.get_model("auth", "Group")

    # Create the Sonographer Django group
    Group.objects.get_or_create(name="Sonographer")

    # Create the sonographer module
    module, _ = Module.objects.get_or_create(
        code="sonographer",
        defaults={
            "name": "Sonographer",
            "monthly_price": 50000,
            "is_core": False,
            "is_active": True,
            "display_order": 25,
            "url_name": "scan_queue",
        },
    )


def remove_sonographer(apps, schema_editor):
    Module = apps.get_model("accounts", "Module")
    Group = apps.get_model("auth", "Group")
    Module.objects.filter(code="sonographer").delete()
    Group.objects.filter(name="Sonographer").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_support_tokens"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(seed_sonographer, remove_sonographer),
    ]
