from django.db import migrations


def clear_is_staff(apps, schema_editor):
    """is_staff used to be switched on for every hospital role, which opened
    Django's /admin/ sign-in to all staff and let every role through the lab
    permission check. Only platform superadmins keep it now."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_staff=True, is_superuser=False).exclude(role="superadmin").update(is_staff=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_pharmacy_db_defaults"),
    ]

    operations = [
        migrations.RunPython(clear_is_staff, migrations.RunPython.noop),
    ]
