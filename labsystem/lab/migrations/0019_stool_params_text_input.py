from django.db import migrations


def stool_params_to_text(apps, schema_editor):
    TestProfileParameter = apps.get_model("lab", "TestProfileParameter")
    TestProfileParameter.objects.filter(
        profile__code="stool_analysis",
        input_type="textarea",
    ).update(input_type="text")


def stool_params_to_textarea(apps, schema_editor):
    TestProfileParameter = apps.get_model("lab", "TestProfileParameter")
    names = ["Ova/Cysts/Parasites", "Other Findings"]
    TestProfileParameter.objects.filter(
        profile__code="stool_analysis",
        test_name__in=names,
    ).update(input_type="textarea")


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0018_deactivate_manual_simple"),
    ]

    operations = [
        migrations.RunPython(stool_params_to_text, stool_params_to_textarea),
    ]
