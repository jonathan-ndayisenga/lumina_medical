from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_add_hospital_invoice_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="hospital",
            name="tagline",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
