import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0032_seed_starter_templates"),
        ("reception", "0024_service_lab_tests_next_m2m"),
    ]

    operations = [
        migrations.AlterField(
            model_name="laborder",
            name="visit_service",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lab_orders_next",
                to="reception.visitservice",
            ),
        ),
    ]
