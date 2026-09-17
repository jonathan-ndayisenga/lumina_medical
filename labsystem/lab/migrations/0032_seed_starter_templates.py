from decimal import Decimal

from django.conf import settings
from django.db import migrations


# Exact snapshot of the 5 curated starter templates as they exist in local
# dev today (built up over many iterations this engagement: a 20-parameter
# CBC with sex-specific Hemoglobin/RBC/Hematocrit ranges, a full Urinalysis
# panel, a Urine Culture & Sensitivity panel with a grouped antibiotic
# picker, Malaria and Typhoid rapid tests). None of this exists anywhere
# outside one local sqlite file — this migration is what makes the "Clone a
# Test" starter picker work on any other database, live included.
STARTER_TEMPLATES = [
    {
        "name": "Complete Blood Count", "code": "CBC", "category": "Hematology",
        "result_type": "parameter_panel", "specimens": ["Blood"],
        "options": [],
        "params": [
            {"name": "WBC", "unit": "10^3/uL", "value_type": "numeric", "group_label": "", "sort_order": 0,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("4.000"), "ref_high": Decimal("10.000"), "ref_text": ""}]},
            {"name": "Lymph#", "unit": "10^3/uL", "value_type": "numeric", "group_label": "", "sort_order": 1,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("0.800"), "ref_high": Decimal("4.000"), "ref_text": ""}]},
            {"name": "Mid#", "unit": "10^3/uL", "value_type": "numeric", "group_label": "", "sort_order": 2,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("0.100"), "ref_high": Decimal("1.200"), "ref_text": ""}]},
            {"name": "Gran#", "unit": "10^3/uL", "value_type": "numeric", "group_label": "", "sort_order": 3,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("2.000"), "ref_high": Decimal("7.000"), "ref_text": ""}]},
            {"name": "Lymph%", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 4,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("20.000"), "ref_high": Decimal("40.000"), "ref_text": ""}]},
            {"name": "Mid%", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 5,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("3.000"), "ref_high": Decimal("14.000"), "ref_text": ""}]},
            {"name": "Gran%", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 6,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("50.000"), "ref_high": Decimal("70.000"), "ref_text": ""}]},
            {"name": "HGB", "unit": "g/dL", "value_type": "numeric", "group_label": "", "sort_order": 7,
             "ranges": [
                 {"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("11.000"), "ref_high": Decimal("16.000"), "ref_text": ""},
                 {"sex": "female", "age_min": None, "age_max": None, "ref_low": Decimal("11.000"), "ref_high": Decimal("15.000"), "ref_text": ""},
                 {"sex": "male", "age_min": None, "age_max": None, "ref_low": Decimal("12.000"), "ref_high": Decimal("16.000"), "ref_text": ""},
             ]},
            {"name": "RBC", "unit": "10^6/uL", "value_type": "numeric", "group_label": "", "sort_order": 8,
             "ranges": [
                 {"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("3.500"), "ref_high": Decimal("5.500"), "ref_text": ""},
                 {"sex": "female", "age_min": None, "age_max": None, "ref_low": Decimal("3.500"), "ref_high": Decimal("5.000"), "ref_text": ""},
                 {"sex": "male", "age_min": None, "age_max": None, "ref_low": Decimal("4.000"), "ref_high": Decimal("5.500"), "ref_text": ""},
             ]},
            {"name": "HCT", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 9,
             "ranges": [
                 {"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("37.000"), "ref_high": Decimal("54.000"), "ref_text": ""},
                 {"sex": "female", "age_min": None, "age_max": None, "ref_low": Decimal("37.000"), "ref_high": Decimal("47.000"), "ref_text": ""},
                 {"sex": "male", "age_min": None, "age_max": None, "ref_low": Decimal("40.000"), "ref_high": Decimal("54.000"), "ref_text": ""},
             ]},
            {"name": "MCV", "unit": "fL", "value_type": "numeric", "group_label": "", "sort_order": 10,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("80.000"), "ref_high": Decimal("100.000"), "ref_text": ""}]},
            {"name": "MCH", "unit": "pg", "value_type": "numeric", "group_label": "", "sort_order": 11,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("27.000"), "ref_high": Decimal("34.000"), "ref_text": ""}]},
            {"name": "MCHC", "unit": "g/dL", "value_type": "numeric", "group_label": "", "sort_order": 12,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("32.000"), "ref_high": Decimal("36.000"), "ref_text": ""}]},
            {"name": "RDW-CV", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 13,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("11.000"), "ref_high": Decimal("16.000"), "ref_text": ""}]},
            {"name": "RDW-SD", "unit": "fL", "value_type": "numeric", "group_label": "", "sort_order": 14,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("35.000"), "ref_high": Decimal("56.000"), "ref_text": ""}]},
            {"name": "PLT", "unit": "10^3/uL", "value_type": "numeric", "group_label": "", "sort_order": 15,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("100.000"), "ref_high": Decimal("300.000"), "ref_text": ""}]},
            {"name": "MPV", "unit": "fL", "value_type": "numeric", "group_label": "", "sort_order": 16,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("6.500"), "ref_high": Decimal("12.000"), "ref_text": ""}]},
            {"name": "PDW", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 17,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("9.000"), "ref_high": Decimal("17.000"), "ref_text": ""}]},
            {"name": "PCT", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 18,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("0.108"), "ref_high": Decimal("0.282"), "ref_text": ""}]},
            {"name": "P-LCR", "unit": "%", "value_type": "numeric", "group_label": "", "sort_order": 19,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("11.000"), "ref_high": Decimal("45.000"), "ref_text": ""}]},
        ],
    },
    {
        "name": "Malaria Rapid Test", "code": "MAL-RDT", "category": "Parasitology",
        "result_type": "defined_option", "specimens": ["Blood"],
        "options": [{"label": "Positive", "sort_order": 1, "is_abnormal": True}, {"label": "Negative", "sort_order": 2, "is_abnormal": False}],
        "params": [],
    },
    {
        "name": "Typhoid Test", "code": "TYPH", "category": "Serology",
        "result_type": "defined_option", "specimens": ["Serum"],
        "options": [{"label": "Positive", "sort_order": 1, "is_abnormal": True}, {"label": "Negative", "sort_order": 2, "is_abnormal": False}],
        "params": [],
    },
    {
        "name": "Urinalysis", "code": "UA", "category": "Chemistry",
        "result_type": "parameter_panel", "specimens": ["Urine"],
        "options": [],
        "params": [
            {"name": "Color", "unit": "", "value_type": "text", "group_label": "Physical Examination", "sort_order": 0,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Appearance", "unit": "", "value_type": "text", "group_label": "Physical Examination", "sort_order": 1,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "pH", "unit": "", "value_type": "numeric", "group_label": "Chemical Examination", "sort_order": 2,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("4.500"), "ref_high": Decimal("8.000"), "ref_text": ""}]},
            {"name": "Specific Gravity", "unit": "", "value_type": "numeric", "group_label": "Chemical Examination", "sort_order": 3,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": Decimal("1.005"), "ref_high": Decimal("1.030"), "ref_text": ""}]},
            {"name": "Protein", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 4,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Glucose", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 5,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Ketones", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 6,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Blood", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 7,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Bilirubin", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 8,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Urobilinogen", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 9,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Nitrites", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 10,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Leukocyte Esterase", "unit": "", "value_type": "level", "group_label": "Chemical Examination", "sort_order": 11,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "RBC", "unit": "/HPF", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 12,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "WBC", "unit": "/HPF", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 13,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Epithelial Cells", "unit": "/HPF", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 14,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Casts", "unit": "/LPF", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 15,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Crystals", "unit": "", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 16,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Bacteria", "unit": "", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 17,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Yeast Cells", "unit": "", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 18,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Mucus Threads", "unit": "", "value_type": "text", "group_label": "Microscopic Examination", "sort_order": 19,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
        ],
    },
    {
        "name": "Urine Culture & Sensitivity", "code": "UCS", "category": "Microbiology",
        "result_type": "parameter_panel", "specimens": ["Urine"],
        "options": [],
        "params": [
            {"name": "Bacterial Growth", "unit": "", "value_type": "growth", "group_label": "", "sort_order": 0,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Organism Isolated", "unit": "", "value_type": "text", "group_label": "", "sort_order": 1,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Other Organisms Observed (non-bacterial)", "unit": "", "value_type": "text", "group_label": "", "sort_order": 2,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Ampicillin", "unit": "", "value_type": "coded", "group_label": "Penicillins", "sort_order": 3,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Amoxicillin-Clavulanate", "unit": "", "value_type": "coded", "group_label": "Penicillins", "sort_order": 4,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Ciprofloxacin", "unit": "", "value_type": "coded", "group_label": "Fluoroquinolones", "sort_order": 5,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Nitrofurantoin", "unit": "", "value_type": "coded", "group_label": "Nitrofurans", "sort_order": 6,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Trimethoprim-Sulfamethoxazole", "unit": "", "value_type": "coded", "group_label": "Sulfonamides", "sort_order": 7,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Gentamicin", "unit": "", "value_type": "coded", "group_label": "Aminoglycosides", "sort_order": 8,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
            {"name": "Ceftriaxone", "unit": "", "value_type": "coded", "group_label": "Cephalosporins", "sort_order": 9,
             "ranges": [{"sex": "any", "age_min": None, "age_max": None, "ref_low": None, "ref_high": None, "ref_text": ""}]},
        ],
    },
]


def seed_starter_templates(apps, schema_editor):
    Hospital = apps.get_model("accounts", "Hospital")
    ServiceCategory = apps.get_model("lab", "ServiceCategory")
    SpecimenType = apps.get_model("lab", "SpecimenType")
    LabTest = apps.get_model("lab", "LabTest")
    Parameter = apps.get_model("lab", "Parameter")
    ParameterRange = apps.get_model("lab", "ParameterRange")
    DefinedOption = apps.get_model("lab", "DefinedOption")

    # The Ternah Books tenant (seeded by accounts.0020, guaranteed to exist
    # on every database this codebase runs on) holds the curated catalog —
    # it's Ternah's own tenant, not a real hospital's working catalog, so no
    # real hospital's edits can ever drift the starters, and it's the one
    # anchor point guaranteed portable between local and any other database.
    subdomain = getattr(settings, "TERNAH_BOOKS_HOSPITAL_SUBDOMAIN", "ternah-books")
    template_hospital, _ = Hospital.objects.get_or_create(
        subdomain=subdomain, defaults={"name": "Ternah Software Company Ltd"},
    )

    for template in STARTER_TEMPLATES:
        if LabTest.objects.filter(
            hospital=template_hospital, name=template["name"], is_starter_template=True,
        ).exists():
            continue  # already seeded (e.g. this migration ran before)

        category, _ = ServiceCategory.objects.get_or_create(name=template["category"])

        test = LabTest.objects.create(
            hospital=template_hospital,
            name=template["name"],
            code=template["code"],
            category=category,
            result_type=template["result_type"],
            is_starter_template=True,
        )

        for specimen_name in template["specimens"]:
            specimen, _ = SpecimenType.objects.get_or_create(name=specimen_name)
            test.accepted_specimens.add(specimen)

        for option in template["options"]:
            DefinedOption.objects.create(
                test=test, label=option["label"], sort_order=option["sort_order"],
                is_abnormal=option["is_abnormal"],
            )

        for param in template["params"]:
            parameter = Parameter.objects.create(
                test=test, name=param["name"], unit=param["unit"], value_type=param["value_type"],
                group_label=param["group_label"], sort_order=param["sort_order"],
            )
            for rng in param["ranges"]:
                ParameterRange.objects.create(
                    parameter=parameter, sex=rng["sex"], age_min=rng["age_min"], age_max=rng["age_max"],
                    ref_low=rng["ref_low"], ref_high=rng["ref_high"], ref_text=rng["ref_text"],
                )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_seed_ternah_books_tenant"),
        ("lab", "0031_labtest_is_starter_template"),
    ]

    operations = [
        migrations.RunPython(seed_starter_templates, noop),
    ]
