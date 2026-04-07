from django.db import migrations, models
import django.db.models.deletion


def seed_test_profiles(apps, schema_editor):
    TestCatalog = apps.get_model('lab', 'TestCatalog')
    TestProfile = apps.get_model('lab', 'TestProfile')
    TestProfileParameter = apps.get_model('lab', 'TestProfileParameter')

    cbc_profile, _ = TestProfile.objects.get_or_create(
        code='cbc',
        defaults={
            'name': 'Complete Blood Count (CBC)',
            'default_specimen_type': 'BLOOD',
            'description': 'Lumina CBC template with seeded ranges and units.',
            'is_active': True,
            'display_order': 1,
        },
    )
    urinalysis_profile, _ = TestProfile.objects.get_or_create(
        code='urinalysis',
        defaults={
            'name': 'Urinalysis',
            'default_specimen_type': 'URINE',
            'description': 'Starter urinalysis template grouped into physical, chemical, and microscopy sections.',
            'is_active': True,
            'display_order': 2,
        },
    )

    cbc_rows = [
        ('Mean Cell Hb (MCH)', '23.5 - 33.7', 'Pg'),
        ('Platelet Distribution Width (PDW)', '9.0 - 17.0', '%'),
        ('Mean Platelet Volume (MPV)', '6.7 - 10.1', 'fL'),
        ('Thrombocrit (PCT)', '0.10 - 0.28', '%'),
        ('RBC Distribution Width (RDW)', '11.0 - 16.8', '%'),
        ('Granulocytes %', '32.2 - 59.3', '%'),
        ('Granulocytes (Absolute)', '0.9 - 3.9', '10^3/uL'),
        ('Platelet Count', '109 - 384', '10^3/uL'),
        ('Mean Cell Hb Conc (MCHC)', '32.5 - 35.3', 'g/dL'),
        ('Lymphocytes', '1.2 - 3.7', '10^3/uL'),
        ('Mean Cell Volume (MCV)', '71 - 97', 'fL'),
        ('Hematocrit', '31.2 - 49.5', '%'),
        ('Hemoglobin', '10.8 - 17.1', 'g/dL'),
        ('Red Blood Cell (RBC)', '3.5 - 6.10', '10^6/uL'),
        ('Total WBC Count', '2.8 - 8.2', '10^3/uL'),
        ('Monocytes %', '4.7 - 12.7', '%'),
        ('Monocytes', '0.2 - 0.7', '10^3/uL'),
        ('Lymphocytes %', '25.0 - 40.0', '%'),
    ]

    for index, (name, ref_range, unit) in enumerate(cbc_rows, start=1):
        test, _ = TestCatalog.objects.get_or_create(
            name=name,
            defaults={'unit': unit, 'display_order': index},
        )
        if unit and not test.unit:
            test.unit = unit
            test.save(update_fields=['unit'])
        TestProfileParameter.objects.update_or_create(
            profile=cbc_profile,
            test=test,
            display_order=index,
            defaults={
                'section_name': 'SECTION A: COMPLETE BLOOD COUNT (CBC)',
                'input_type': 'numeric',
                'choice_options': '',
                'default_reference_range': ref_range,
                'default_unit': unit,
                'default_comment': '',
                'is_required': False,
                'allow_range_learning': True,
            },
        )

    urinalysis_rows = [
        ('SECTION B: URINALYSIS - PHYSICAL EXAMINATION', 'Color', 'choice', 'Yellow\nStraw\nAmber\nRed\nBrown', '', '', ''),
        ('SECTION B: URINALYSIS - PHYSICAL EXAMINATION', 'Appearance / Clarity', 'choice', 'Clear\nSlightly Cloudy\nCloudy\nTurbid', '', '', ''),
        ('SECTION B: URINALYSIS - PHYSICAL EXAMINATION', 'Odor', 'text', '', '', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'pH', 'numeric', '', '4.5 - 8.0', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Specific Gravity', 'numeric', '', '1.005 - 1.030', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Protein', 'choice', 'Negative\nTrace\n1+\n2+\n3+\n4+', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Glucose', 'choice', 'Negative\nTrace\n1+\n2+\n3+\n4+', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Ketones', 'choice', 'Negative\nTrace\n1+\n2+\n3+\n4+', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Bilirubin', 'choice', 'Negative\nTrace\nPositive', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Blood / Hemoglobin', 'choice', 'Negative\nTrace\nPositive', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Nitrite', 'choice', 'Negative\nPositive', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Leukocyte Esterase', 'choice', 'Negative\nTrace\nPositive', 'Negative', '', ''),
        ('SECTION B: URINALYSIS - CHEMICAL EXAMINATION', 'Urobilinogen', 'text', '', 'Normal', '', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'WBC / HPF', 'numeric', '', '0 - 5', '/HPF', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'RBC / HPF', 'numeric', '', '0 - 2', '/HPF', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'Epithelial Cells', 'text', '', 'Few', '', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'Casts', 'text', '', 'None Seen', '', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'Crystals', 'text', '', 'None Seen', '', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'Bacteria', 'text', '', 'None Seen', '', ''),
        ('SECTION B: URINALYSIS - MICROSCOPY', 'Yeast / Mucus', 'text', '', 'None Seen', '', ''),
    ]

    for index, row in enumerate(urinalysis_rows, start=1):
        section_name, name, input_type, choice_options, ref_range, unit, comment = row
        test, _ = TestCatalog.objects.get_or_create(
            name=name,
            defaults={'unit': unit, 'display_order': 100 + index},
        )
        if unit and not test.unit:
            test.unit = unit
            test.save(update_fields=['unit'])
        TestProfileParameter.objects.update_or_create(
            profile=urinalysis_profile,
            test=test,
            display_order=index,
            defaults={
                'section_name': section_name,
                'input_type': input_type,
                'choice_options': choice_options,
                'default_reference_range': ref_range,
                'default_unit': unit,
                'default_comment': comment,
                'is_required': False,
                'allow_range_learning': True,
            },
        )


def unseed_test_profiles(apps, schema_editor):
    TestProfile = apps.get_model('lab', 'TestProfile')
    TestProfile.objects.filter(code__in=['cbc', 'urinalysis']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0004_catalog_learning'),
    ]

    operations = [
        migrations.CreateModel(
            name='TestProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('code', models.SlugField(max_length=50, unique=True)),
                ('default_specimen_type', models.CharField(blank=True, max_length=50)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('display_order', models.PositiveIntegerField(default=0)),
            ],
            options={
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='labreport',
            name='profile',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports', to='lab.testprofile'),
        ),
        migrations.AddField(
            model_name='labreport',
            name='referred_by',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.CreateModel(
            name='TestProfileParameter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('section_name', models.CharField(blank=True, max_length=100)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('input_type', models.CharField(choices=[('text', 'Text'), ('numeric', 'Numeric'), ('choice', 'Choice')], default='text', max_length=20)),
                ('choice_options', models.TextField(blank=True, help_text='Optional newline-separated values for choice inputs.')),
                ('default_reference_range', models.CharField(blank=True, max_length=50)),
                ('default_unit', models.CharField(blank=True, max_length=20)),
                ('default_comment', models.CharField(blank=True, max_length=255)),
                ('is_required', models.BooleanField(default=False)),
                ('allow_range_learning', models.BooleanField(default=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='parameters', to='lab.testprofile')),
                ('test', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profile_parameters', to='lab.testcatalog')),
            ],
            options={
                'ordering': ['profile__display_order', 'display_order', 'id'],
            },
        ),
        migrations.AddField(
            model_name='testresult',
            name='comment',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='testresult',
            name='display_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='testresult',
            name='section_name',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterModelOptions(
            name='testresult',
            options={'ordering': ['display_order', 'id']},
        ),
        migrations.RunPython(seed_test_profiles, unseed_test_profiles),
    ]
