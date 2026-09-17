from django.db import migrations


DEFAULTS = [
    ("Blood", "Whole blood or blood-derived specimen."),
    ("Serum", "Separated serum specimen."),
    ("Stool", "Stool sample."),
    ("Swab", "Swab specimen."),
    ("Urine", "Urine sample."),
]


def seed_defaults(apps, schema_editor):
    SpecimenType = apps.get_model("lab", "SpecimenType")
    for name, description in DEFAULTS:
        obj, created = SpecimenType.objects.get_or_create(
            name=name, defaults={"description": description, "is_default": True},
        )
        if not created:
            obj.is_default = True
            if not obj.description:
                obj.description = description
            obj.save(update_fields=["is_default", "description"])


def unseed_defaults(apps, schema_editor):
    SpecimenType = apps.get_model("lab", "SpecimenType")
    SpecimenType.objects.filter(name__in=[n for n, _ in DEFAULTS], is_default=True).update(is_default=False)


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0023_specimentype_description_specimentype_is_default'),
    ]

    operations = [
        migrations.RunPython(seed_defaults, unseed_defaults),
    ]
