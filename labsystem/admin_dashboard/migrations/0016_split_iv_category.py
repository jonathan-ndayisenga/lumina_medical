from django.db import migrations


def split_iv_category(apps, schema_editor):
    InventoryItem = apps.get_model("admin_dashboard", "InventoryItem")
    # All existing "iv" items become "iv_fluid" — historically the category held
    # IV bags (Normal Saline, Ringer's Lactate) which use ml/fluid math.
    InventoryItem.objects.filter(category="iv").update(category="iv_fluid")


def reverse_split(apps, schema_editor):
    InventoryItem = apps.get_model("admin_dashboard", "InventoryItem")
    InventoryItem.objects.filter(category__in=["iv_fluid", "iv_med"]).update(category="iv")


class Migration(migrations.Migration):

    dependencies = [
        ("admin_dashboard", "0015_expense_date_editable"),
    ]

    operations = [
        migrations.RunPython(split_iv_category, reverse_code=reverse_split),
    ]
