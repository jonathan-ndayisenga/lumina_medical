from django.db import migrations


def deactivate_manual_simple(apps, schema_editor):
    TestProfile = apps.get_model("lab", "TestProfile")
    TestProfile.objects.filter(code="manual_simple").update(is_active=False)


def reactivate_manual_simple(apps, schema_editor):
    TestProfile = apps.get_model("lab", "TestProfile")
    TestProfile.objects.filter(code="manual_simple").update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0017_stool_analysis_and_textarea"),
    ]

    operations = [
        migrations.RunPython(deactivate_manual_simple, reactivate_manual_simple),
    ]
