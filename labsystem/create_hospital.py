#!/usr/bin/env python
import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import Hospital, SubscriptionPlan, User

# Create a default subscription plan if none exists
plan, created = SubscriptionPlan.objects.get_or_create(
    name="Premium",
    defaults={
        'price_monthly': 299.99,
        'price_yearly': 2999.99,
        'max_users': 50,
        'max_storage_mb': 1000,
        'description': 'Professional hospital management suite',
        'is_active': True
    }
)
print(f"Subscription Plan: {plan.name}")

# Create a default hospital
hospital, created = Hospital.objects.get_or_create(
    name="Lumina Medical Center",
    defaults={
        'subdomain': 'lumina-main',
        'subscription_plan': plan,
        'is_active': True
    }
)
print(f"Hospital: {hospital.name} (subdomain: {hospital.subdomain})")

# Assign hospital to superuser
superuser = User.objects.filter(role='superadmin').first()
if superuser:
    superuser.hospital = hospital
    superuser.save()
    print(f"✓ Superuser {superuser.username} assigned to {hospital.name}")
else:
    print("No superuser found!")
