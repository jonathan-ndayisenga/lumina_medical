from django.db import migrations


def link_cbc_services(apps, schema_editor):
    Service = apps.get_model("reception", "Service")
    TestProfile = apps.get_model("lab", "TestProfile")

    cbc_profile = TestProfile.objects.filter(code="cbc").first()
    if not cbc_profile:
        return

    Service.objects.filter(
        category="lab",
        name__iexact="CBC",
        test_profile__isnull=True,
    ).update(test_profile=cbc_profile)

    Service.objects.filter(
        category="lab",
        name__iexact="CBC Test",
        test_profile__isnull=True,
    ).update(test_profile=cbc_profile)


class Migration(migrations.Migration):

    dependencies = [
        ("reception", "0018_visit_weight_kg"),
        ("lab", "0020_lab_consumables_datetime_sample"),
    ]

    operations = [
        migrations.RunPython(link_cbc_services, migrations.RunPython.noop),
    ]
