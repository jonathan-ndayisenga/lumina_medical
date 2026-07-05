from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_add_hospital_tagline"),
    ]

    operations = [
        migrations.AddField(
            model_name="hospitalsubscriptionpayment",
            name="months_paid",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="hospitalsubscriptionpayment",
            name="receipt_number",
            field=models.CharField(blank=True, max_length=50, unique=True),
        ),
    ]
