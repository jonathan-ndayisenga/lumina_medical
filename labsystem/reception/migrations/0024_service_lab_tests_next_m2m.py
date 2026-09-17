from django.db import migrations, models


def copy_lab_test_next_to_m2m(apps, schema_editor):
    Service = apps.get_model("reception", "Service")
    LabTest = apps.get_model("lab", "LabTest")
    for service in Service.objects.exclude(lab_test_next__isnull=True):
        test = LabTest.objects.filter(pk=service.lab_test_next_id).first()
        if test is not None:
            service.lab_tests_next.add(test)


def copy_m2m_to_lab_test_next(apps, schema_editor):
    """Reverse: a service linked to more than one test can only carry the
    first one back into the single old FK -- lossy by necessity, same as
    any FK-to-M2M reversal."""
    Service = apps.get_model("reception", "Service")
    for service in Service.objects.all():
        first_test = service.lab_tests_next.first()
        service.lab_test_next_id = first_test.pk if first_test else None
        service.save(update_fields=["lab_test_next"])


class Migration(migrations.Migration):

    dependencies = [
        # Must run after lab.0030's backfill, not just lab.0021 (where the
        # FK it's converting was created) -- that backfill still reads and
        # writes Service.lab_test_next, so this conversion has to be
        # strictly ordered after it or the migration graph's topological
        # sort is free to interleave them the other way, dropping the field
        # out from under that still-pending read.
        ("lab", "0030_alter_labtest_hospital"),
        ("reception", "0023_alter_service_lab_test_next"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="lab_tests_next",
            field=models.ManyToManyField(
                blank=True,
                help_text="Which lab.LabTest(es) this billed service creates orders for. Billed once, "
                          "but can fan out into more than one order -- e.g. a bundled \"Malaria Test\" "
                          "service linking both MRDT and B/S, each entered independently. Required for "
                          "the service to be picked up by the lab queue; unrelated to `test_profile` "
                          "above, which only the historical report archive still reads.",
                related_name="services",
                to="lab.labtest",
            ),
        ),
        migrations.RunPython(copy_lab_test_next_to_m2m, copy_m2m_to_lab_test_next),
        migrations.RemoveField(
            model_name="service",
            name="lab_test_next",
        ),
    ]
