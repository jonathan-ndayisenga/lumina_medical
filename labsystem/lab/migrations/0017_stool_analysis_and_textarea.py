from django.db import migrations, models


def add_stool_analysis_profile(apps, schema_editor):
    TestCatalog = apps.get_model('lab', 'TestCatalog')
    TestProfile = apps.get_model('lab', 'TestProfile')
    TestProfileParameter = apps.get_model('lab', 'TestProfileParameter')

    if TestProfile.objects.filter(code='stool_analysis').exists():
        return

    profile = TestProfile.objects.create(
        name='Stool Analysis',
        code='stool_analysis',
        default_specimen_type='STOOL',
        description='Descriptive stool analysis with macroscopic and microscopic findings.',
        is_active=True,
        display_order=30,
    )

    parameters = [
        ('Macroscopy', 'Appearance', 'text'),
        ('Macroscopy', 'Colour', 'text'),
        ('Macroscopy', 'Consistency', 'text'),
        ('Macroscopy', 'Mucus', 'text'),
        ('Macroscopy', 'Blood/Occult Blood', 'text'),
        ('Microscopy', 'Pus Cells', 'text'),
        ('Microscopy', 'Red Blood Cells', 'text'),
        ('Microscopy', 'Epithelial Cells', 'text'),
        ('Microscopy', 'Ova/Cysts/Parasites', 'textarea'),
        ('Microscopy', 'Other Findings', 'textarea'),
    ]

    for index, (section_name, test_name, input_type) in enumerate(parameters, start=1):
        test, _ = TestCatalog.objects.get_or_create(
            name=test_name,
            defaults={'unit': '', 'display_order': 300 + index},
        )
        TestProfileParameter.objects.create(
            profile=profile,
            test=test,
            section_name=section_name,
            display_order=index,
            input_type=input_type,
            choice_options='',
            default_reference_range='',
            default_unit='',
            default_comment='',
            is_required=False,
            allow_range_learning=False,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0016_alter_referencerangedefault_age_category'),
    ]

    operations = [
        migrations.AlterField(
            model_name='testresult',
            name='result_value',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='testprofileparameter',
            name='input_type',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('text', 'Text'),
                    ('numeric', 'Numeric'),
                    ('choice', 'Choice'),
                    ('textarea', 'Text Area'),
                ],
                default='text',
            ),
        ),
        migrations.RunPython(add_stool_analysis_profile, migrations.RunPython.noop),
    ]
