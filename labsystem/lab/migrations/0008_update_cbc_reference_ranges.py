from django.db import migrations


def update_cbc_reference_ranges(apps, schema_editor):
    TestProfile = apps.get_model("lab", "TestProfile")
    TestProfileParameter = apps.get_model("lab", "TestProfileParameter")

    profile = TestProfile.objects.filter(code="cbc").first()
    if not profile:
        return

    updated_ranges = {
        "Mean Cell Hb (MCH)": "27.0-34.0",
        "Platelet Distribution Width (PDW)": "9.0-17.0",
        "Mean Platelet Volume (MPV)": "6.5-12.0",
        "Thrombocrit (PCT)": "0.10-0.28",
        "RBC Distribution Width (RDW)": "11.0-16.0",
        "Granulocytes %": "50.0-70.7",
        "Granulocytes (Absolute)": "2.0-8.0",
        "Platelet Count": "100-300",
        "Mean Cell Hb Conc (MCHC)": "31.0-37.0",
        "Lymphocytes": "0.8-7.0",
        "Mean Cell Volume (MCV)": "80.0-100.0",
        "Hematocrit": "31.2-49.5",
        "Hemoglobin": "12.0-16.0",
        "Red Blood Cell (RBC)": "3.50-5.20",
        "Total WBC Count": "4.0-12.0",
        "Monocytes %": "3.0-14.0",
        "Monocytes": "0.1-1.2",
        "Lymphocytes %": "20.0-60.0",
    }

    for parameter in TestProfileParameter.objects.select_related("test").filter(profile=profile):
        new_range = updated_ranges.get(parameter.test.name)
        if new_range and parameter.default_reference_range != new_range:
            parameter.default_reference_range = new_range
            parameter.save(update_fields=["default_reference_range"])


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0007_refresh_cbc_sheet_layout"),
    ]

    operations = [
        migrations.RunPython(update_cbc_reference_ranges, migrations.RunPython.noop),
    ]
