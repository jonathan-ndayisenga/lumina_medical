from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_add_city_to_hospital"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("superadmin", "Super Admin"),
                    ("hospital_admin", "Hospital Admin"),
                    ("accountant", "Accountant"),
                    ("receptionist", "Receptionist"),
                    ("lab_attendant", "Lab Attendant"),
                    ("doctor", "Doctor"),
                    ("nurse", "Nurse"),
                ],
                default="lab_attendant",
                max_length=20,
            ),
        ),
    ]
