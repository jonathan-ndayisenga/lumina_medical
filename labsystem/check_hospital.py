#!/usr/bin/env python
import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import Hospital, User

hospitals = Hospital.objects.all()
print(f"Hospitals in database: {hospitals.count()}")
for h in hospitals:
    print(f"  - {h.name} (subdomain: {h.subdomain})")

superuser = User.objects.filter(role='superadmin').first()
if superuser:
    print(f"\nSuperuser: {superuser.username}")
    print(f"Assigned hospital: {superuser.hospital}")
    
    # If no hospital is assigned, assign the first one
    if not superuser.hospital and hospitals.exists():
        superuser.hospital = hospitals.first()
        superuser.save()
        print(f"✓ Assigned hospital: {superuser.hospital.name}")
