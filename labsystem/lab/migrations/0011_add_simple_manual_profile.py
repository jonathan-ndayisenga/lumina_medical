from django.db import migrations


def add_simple_manual_profile(apps, schema_editor):
    TestProfile = apps.get_model("lab", "TestProfile")

    # Provide a built-in "simple manual" template that hides ref/unit/comment in the UI.
    # Parameters are intentionally empty; users add rows manually.
    TestProfile.objects.get_or_create(
        code="manual_simple",
        defaults={
            "name": "Manual Entry (Simple)",
            "default_specimen_type": "",
            "description": "Simple manual entry: test + result only. Reference range, unit, and comment are optional/hidden.",
            "is_active": True,
            "display_order": 0,
        },
    )


def remove_simple_manual_profile(apps, schema_editor):
    TestProfile = apps.get_model("lab", "TestProfile")
    TestProfile.objects.filter(code="manual_simple").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("lab", "0010_labreport_lab_request_labreport_sent_to_doctor_and_more"),
    ]

    operations = [
        migrations.RunPython(add_simple_manual_profile, remove_simple_manual_profile),
    ]

