from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_add_sonographer_module"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                max_length=50,
                choices=[
                    ("superadmin", "Super Admin"),
                    ("hospital_admin", "Hospital Admin"),
                    ("accountant", "Accountant"),
                    ("receptionist", "Receptionist"),
                    ("lab_attendant", "Lab Attendant"),
                    ("doctor", "Doctor"),
                    ("nurse", "Nurse"),
                    ("sonographer", "Sonographer"),
                ],
                default="lab_attendant",
            ),
        ),
    ]
