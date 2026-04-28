from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reception", "0011_patient_address_patient_date_of_birth_patient_email_and_more"),
        ("lab", "0012_testresult_source_profile_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="labreport",
            name="requested_visit_service",
            field=models.OneToOneField(
                blank=True,
                help_text="The specific requested visit service this report satisfies when tests are worked one-by-one.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="lab_report",
                to="reception.visitservice",
            ),
        ),
    ]
