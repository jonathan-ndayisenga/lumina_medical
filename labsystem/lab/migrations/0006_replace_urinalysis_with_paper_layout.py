from django.db import migrations


def replace_urinalysis_profile(apps, schema_editor):
    TestCatalog = apps.get_model('lab', 'TestCatalog')
    TestProfile = apps.get_model('lab', 'TestProfile')
    TestProfileParameter = apps.get_model('lab', 'TestProfileParameter')

    profile = TestProfile.objects.filter(code='urinalysis').first()
    if not profile:
        return

    profile.name = 'Urinalysis'
    profile.default_specimen_type = 'URINE'
    profile.description = 'Paper-style urinalysis sheet with Macroscopy, Microscopy, and Others.'
    profile.save(update_fields=['name', 'default_specimen_type', 'description'])

    TestProfileParameter.objects.filter(profile=profile).delete()

    paper_rows = [
        ('Macroscopy', 'Appearance'),
        ('Macroscopy', 'Leukocytes'),
        ('Macroscopy', 'Nitrites'),
        ('Macroscopy', 'Blood'),
        ('Macroscopy', 'Bilirubin'),
        ('Macroscopy', 'Proteins'),
        ('Macroscopy', 'Glucose'),
        ('Macroscopy', 'Ketones'),
        ('Macroscopy', 'PH'),
        ('Macroscopy', 'SG'),
        ('Microscopy', 'Epithelial cells'),
        ('Microscopy', 'Pus cells'),
        ('Microscopy', 'Mucus threads'),
    ]

    for index, (section_name, test_name) in enumerate(paper_rows, start=1):
        test, _ = TestCatalog.objects.get_or_create(
            name=test_name,
            defaults={'unit': '', 'display_order': 200 + index},
        )
        TestProfileParameter.objects.create(
            profile=profile,
            test=test,
            section_name=section_name,
            display_order=index,
            input_type='text',
            choice_options='',
            default_reference_range='',
            default_unit='',
            default_comment='',
            is_required=False,
            allow_range_learning=False,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0005_test_profiles_and_cbc_template'),
    ]

    operations = [
        migrations.RunPython(replace_urinalysis_profile, migrations.RunPython.noop),
    ]
