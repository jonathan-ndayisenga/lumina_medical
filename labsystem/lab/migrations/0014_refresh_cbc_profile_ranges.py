from django.db import migrations

CBC_PARAMETERS = [
    ("WBC", "10^3/uL"),
    ("Lymph#", "10^3/uL"),
    ("Mid#", "10^3/uL"),
    ("Gran#", "10^3/uL"),
    ("Lymph%", "%"),
    ("Mid%", "%"),
    ("Gran%", "%"),
    ("HGB", "g/dL"),
    ("RBC", "10^6/uL"),
    ("HCT", "%"),
    ("MCV", "fL"),
    ("MCH", "pg"),
    ("MCHC", "g/dL"),
    ("RDW-CV", "%"),
    ("RDW-SD", "fL"),
    ("PLT", "10^3/uL"),
    ("MPV", "fL"),
    ("PDW", "%"),
    ("PCT", "%"),
    ("P-LCR", "%"),
]

CBC_REFERENCE_RANGES = {
    "general": {
        "WBC": "4.0-10.0",
        "Lymph#": "0.8-4.0",
        "Mid#": "0.1-1.2",
        "Gran#": "2.0-7.0",
        "Lymph%": "20.0-40.0",
        "Mid%": "3.0-14.0",
        "Gran%": "50.0-70.0",
        "HGB": "11.0-16.0",
        "RBC": "3.50-5.50",
        "HCT": "37.0-54.0",
        "MCV": "80.0-100.0",
        "MCH": "27.0-34.0",
        "MCHC": "32.0-36.0",
        "RDW-CV": "11.0-16.0",
        "RDW-SD": "35.0-56.0",
        "PLT": "100-300",
        "MPV": "6.5-12.0",
        "PDW": "9.0-12.0",
        "PCT": "0.108-0.282",
        "P-LCR": "11.0-45.0",
    },
    "neonate": {
        "WBC": "4.0-20.0",
        "Lymph#": "0.1-2.5",
        "Gran#": "1.6-16.0",
        "Lymph%": "10.0-60.0",
        "Mid%": "3.0-15.0",
        "Gran%": "40.0-80.0",
        "HGB": "17.0-20.0",
        "RBC": "3.50-7.00",
        "HCT": "38.0-68.0",
        "MCV": "95.0-125.0",
        "MCH": "30.0-42.0",
        "MCHC": "30.0-34.0",
        "RDW-CV": "11.0-16.0",
        "RDW-SD": "35.0-56.0",
        "PLT": "100-300",
    },
    "child": {
        "WBC": "4.0-12.0",
        "Lymph#": "0.8-7.0",
        "Mid#": "0.1-1.2",
        "Gran#": "2.0-8.0",
        "Lymph%": "20.0-60.0",
        "Mid%": "3.0-14.0",
        "Gran%": "50.0-70.0",
        "HGB": "12.0-16.0",
        "RBC": "3.50-5.20",
        "HCT": "35.0-49.0",
        "MCV": "80.0-100.0",
        "MCH": "27.0-34.0",
        "MCHC": "31.0-37.0",
        "RDW-CV": "11.0-16.0",
        "RDW-SD": "35.0-56.0",
        "PLT": "100-300",
        "MPV": "6.5-12.0",
        "PDW": "9.0-17.0",
        "PCT": "0.108-0.282",
        "P-LCR": "11.0-45.0",
    },
    "woman": {
        "WBC": "4.0-10.0",
        "Lymph#": "0.8-4.0",
        "Mid#": "0.1-1.2",
        "Gran#": "2.0-7.0",
        "Lymph%": "20.0-40.0",
        "Mid%": "3.0-14.0",
        "Gran%": "50.0-70.0",
        "HGB": "11.0-15.0",
        "RBC": "3.50-5.00",
        "HCT": "37.0-47.0",
        "MCV": "80.0-100.0",
        "MCH": "27.0-34.0",
        "MCHC": "32.0-36.0",
        "RDW-CV": "11.0-16.0",
        "RDW-SD": "35.0-56.0",
        "PLT": "100-300",
        "MPV": "6.5-12.0",
        "PDW": "9.0-17.0",
        "PCT": "0.108-0.282",
        "P-LCR": "11.0-45.0",
    },
    "man": {
        "WBC": "4.0-10.0",
        "Lymph#": "0.8-4.0",
        "Mid#": "0.1-1.2",
        "Gran#": "2.0-7.0",
        "Lymph%": "20.0-40.0",
        "Mid%": "3.0-14.0",
        "Gran%": "50.0-70.0",
        "HGB": "12.0-16.0",
        "RBC": "4.00-5.50",
        "HCT": "40.0-54.0",
        "MCV": "80.0-100.0",
        "MCH": "27.0-34.0",
        "MCHC": "32.0-36.0",
        "RDW-CV": "11.0-16.0",
        "RDW-SD": "35.0-56.0",
        "PLT": "100-300",
        "MPV": "6.5-12.0",
        "PDW": "9.0-17.0",
        "PCT": "0.108-0.282",
        "P-LCR": "11.0-45.0",
    },
}


def refresh_cbc_profile(apps, schema_editor):
    TestCatalog = apps.get_model("lab", "TestCatalog")
    TestProfile = apps.get_model("lab", "TestProfile")
    TestProfileParameter = apps.get_model("lab", "TestProfileParameter")
    ReferenceRangeDefault = apps.get_model("lab", "ReferenceRangeDefault")

    profile = TestProfile.objects.filter(code="cbc").first()
    if not profile:
        return

    profile.description = "CBC template ordered to match the analyzer printout and age-aware reference ranges."
    profile.default_specimen_type = "BLOOD"
    profile.save(update_fields=["description", "default_specimen_type"])

    TestProfileParameter.objects.filter(profile=profile).delete()

    unit_map = dict(CBC_PARAMETERS)
    general_ranges = CBC_REFERENCE_RANGES["general"]

    for index, (name, unit) in enumerate(CBC_PARAMETERS, start=1):
        test, _ = TestCatalog.objects.get_or_create(
            name=name,
            defaults={"unit": unit, "display_order": index},
        )
        updates = {}
        if test.unit != unit:
            updates["unit"] = unit
        if test.display_order != index:
            updates["display_order"] = index
        if updates:
            for field, value in updates.items():
                setattr(test, field, value)
            test.save(update_fields=list(updates.keys()))

        TestProfileParameter.objects.create(
            profile=profile,
            test=test,
            section_name="",
            display_order=index,
            input_type="numeric",
            choice_options="",
            default_reference_range=general_ranges.get(name, ""),
            default_unit=unit,
            default_comment="",
            is_required=False,
            allow_range_learning=True,
        )

        ReferenceRangeDefault.objects.filter(test=test).delete()
        for group, ranges in CBC_REFERENCE_RANGES.items():
            reference_range = ranges.get(name, general_ranges.get(name, ""))
            ReferenceRangeDefault.objects.create(
                test=test,
                age_category=group,
                reference_range=reference_range,
                unit=unit_map.get(name, ""),
            )


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0013_labreport_requested_visit_service"),
    ]

    operations = [
        migrations.RunPython(refresh_cbc_profile, migrations.RunPython.noop),
    ]
