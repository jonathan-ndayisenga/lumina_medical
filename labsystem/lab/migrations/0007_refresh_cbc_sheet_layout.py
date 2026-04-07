from django.db import migrations


def refresh_cbc_profile(apps, schema_editor):
    TestCatalog = apps.get_model('lab', 'TestCatalog')
    TestProfile = apps.get_model('lab', 'TestProfile')
    TestProfileParameter = apps.get_model('lab', 'TestProfileParameter')

    profile = TestProfile.objects.filter(code='cbc').first()
    if not profile:
        return

    profile.description = 'Official Lumina CBC sheet with Section A rows, result, reference range, units, and comment column.'
    profile.default_specimen_type = 'BLOOD'
    profile.save(update_fields=['description', 'default_specimen_type'])

    cbc_rows = [
        ('Mean Cell Hb (MCH)', '23.5 - 33.7', 'Pg'),
        ('Platelet Distribution Width (PDW)', '9.0 - 17.0', '%'),
        ('Mean Platelet Volume (MPV)', '6.7 - 10.1', 'fL'),
        ('Thrombocrit (PCT)', '0.10 - 0.28', '%'),
        ('RBC Distribution Width (RDW)', '11.0 - 16.8', '%'),
        ('Granulocytes %', '32.2 - 59.3', '%'),
        ('Granulocytes (Absolute)', '0.9 - 3.9', '10³/µL'),
        ('Platelet Count', '109 - 384', '10³/µL'),
        ('Mean Cell Hb Conc (MCHC)', '32.5 - 35.3', 'g/dL'),
        ('Lymphocytes', '1.2 - 3.7', '10³/µL'),
        ('Mean Cell Volume (MCV)', '71 - 97', 'fL'),
        ('Hematocrit', '31.2 - 49.5', '%'),
        ('Hemoglobin', '10.8 - 17.1', 'g/dL'),
        ('Red Blood Cell (RBC)', '3.5 - 6.10', '10⁶/µL'),
        ('Total WBC Count', '2.8 - 8.2', '10³/µL'),
        ('Monocytes %', '4.7 - 12.7', '%'),
        ('Monocytes', '0.2 - 0.7', '10³/µL'),
        ('Lymphocytes %', '25.0 - 40.0', '%'),
    ]

    for index, (name, reference_range, unit) in enumerate(cbc_rows, start=1):
        test, _ = TestCatalog.objects.get_or_create(
            name=name,
            defaults={'unit': unit, 'display_order': index},
        )
        updates = {}
        if test.unit != unit:
            updates['unit'] = unit
        if test.display_order != index:
            updates['display_order'] = index
        if updates:
            for field, value in updates.items():
                setattr(test, field, value)
            test.save(update_fields=list(updates.keys()))

        TestProfileParameter.objects.update_or_create(
            profile=profile,
            test=test,
            defaults={
                'section_name': 'SECTION A: COMPLETE BLOOD COUNT (CBC)',
                'display_order': index,
                'input_type': 'numeric',
                'choice_options': '',
                'default_reference_range': reference_range,
                'default_unit': unit,
                'default_comment': '',
                'is_required': False,
                'allow_range_learning': True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('lab', '0006_replace_urinalysis_with_paper_layout'),
    ]

    operations = [
        migrations.RunPython(refresh_cbc_profile, migrations.RunPython.noop),
    ]
