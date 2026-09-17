from django.db import migrations, models
import django.db.models.deletion


def backfill_labtest_hospital(apps, schema_editor):
    """
    Runs once, on whatever database first applies this migration (local dev
    already has this backfill baked into its data by hand, so this is a
    no-op there — every LabTest row already has a hospital). On any other
    database — a fresh deploy, most importantly — LabTest rows from before
    this migration have no hospital at all, because the catalog used to be
    shared platform-wide. We're about to make hospital NOT NULL, so every
    row needs an owner before that runs, and any hospital that actually has
    real order history against a test needs to keep its own copy rather
    than silently start sharing (and being able to edit) another hospital's
    definition.

    For each un-owned LabTest:
      - No hospital has ever ordered it -> hand it to the platform's first
        hospital (arbitrary but deterministic; it's just an unused draft).
      - Exactly one hospital has ordered it -> that hospital owns it, as is.
      - Multiple hospitals have ordered it -> the hospital with the most
        order history keeps the original row; every other hospital with
        order history gets its own full clone (parameters, ranges, defined
        options, culture antibiotics), and that hospital's own LabOrder rows
        and Service.lab_test_next pointer are repointed onto the clone so
        they stop referencing the keeper's copy.
    """
    LabTest = apps.get_model("lab", "LabTest")
    Parameter = apps.get_model("lab", "Parameter")
    ParameterRange = apps.get_model("lab", "ParameterRange")
    DefinedOption = apps.get_model("lab", "DefinedOption")
    CultureAntibiotic = apps.get_model("lab", "CultureAntibiotic")
    LabOrder = apps.get_model("lab", "LabOrder")
    Service = apps.get_model("reception", "Service")
    Hospital = apps.get_model("accounts", "Hospital")

    unowned = list(LabTest.objects.filter(hospital__isnull=True).order_by("id"))
    if not unowned:
        return

    fallback_hospital = Hospital.objects.order_by("id").first()
    clones_made = 0
    orphans_assigned = 0

    for test in unowned:
        hospital_counts = {}
        for hospital_id in LabOrder.objects.filter(test=test).values_list("hospital_id", flat=True):
            hospital_counts[hospital_id] = hospital_counts.get(hospital_id, 0) + 1

        if not hospital_counts:
            if fallback_hospital is None:
                continue
            LabTest.objects.filter(pk=test.pk).update(hospital_id=fallback_hospital.id)
            orphans_assigned += 1
            continue

        ordered_hospital_ids = sorted(hospital_counts, key=lambda hid: (-hospital_counts[hid], hid))
        keeper_id = ordered_hospital_ids[0]
        LabTest.objects.filter(pk=test.pk).update(hospital_id=keeper_id)

        for hospital_id in ordered_hospital_ids[1:]:
            # is_starter_template doesn't exist on this historical model yet
            # (it's added by the next migration, 0031) -- every row, clones
            # included, gets its default (False) when that one runs.
            clone = LabTest.objects.create(
                hospital_id=hospital_id,
                name=test.name,
                code=test.code,
                category_id=test.category_id,
                description=test.description,
                active_for_ordering=test.active_for_ordering,
                result_type=test.result_type,
            )
            clone.accepted_specimens.set(test.accepted_specimens.all())

            for option in DefinedOption.objects.filter(test=test):
                DefinedOption.objects.create(
                    test=clone, label=option.label, sort_order=option.sort_order,
                    is_abnormal=option.is_abnormal,
                )

            for antibiotic in CultureAntibiotic.objects.filter(test=test):
                CultureAntibiotic.objects.create(
                    test=clone, drug_class=antibiotic.drug_class, name=antibiotic.name,
                    sort_order=antibiotic.sort_order,
                )

            for parameter in Parameter.objects.filter(test=test):
                new_parameter = Parameter.objects.create(
                    test=clone, name=parameter.name, unit=parameter.unit,
                    value_type=parameter.value_type, group_label=parameter.group_label,
                    sort_order=parameter.sort_order,
                )
                for rng in ParameterRange.objects.filter(parameter=parameter):
                    ParameterRange.objects.create(
                        parameter=new_parameter, sex=rng.sex, age_min=rng.age_min,
                        age_max=rng.age_max, ref_low=rng.ref_low, ref_high=rng.ref_high,
                        ref_text=rng.ref_text,
                    )

            LabOrder.objects.filter(test=test, hospital_id=hospital_id).update(test=clone)
            Service.objects.filter(hospital_id=hospital_id, lab_test_next=test).update(lab_test_next=clone)
            clones_made += 1

    print(
        f"  lab.0030 backfill: {len(unowned)} unowned LabTest row(s) -> "
        f"{orphans_assigned} assigned with no order history, "
        f"{clones_made} clone(s) made for hospitals sharing a test with someone else."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0021_organization_alter_user_role_hospital_organization_and_more"),
        ("lab", "0029_labtest_hospital"),
        ("reception", "0022_service_lab_test_next"),
    ]

    operations = [
        migrations.RunPython(backfill_labtest_hospital, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="labtest",
            name="hospital",
            field=models.ForeignKey(
                help_text="The hospital that owns this test definition.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="lab_tests",
                to="accounts.hospital",
            ),
        ),
    ]
