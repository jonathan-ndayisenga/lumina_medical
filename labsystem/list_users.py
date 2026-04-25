#!/usr/bin/env python
import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import User

print("All users in database:")
users = User.objects.all()
for u in users:
    print(f"  - {u.username}: role={u.role}, is_superuser={u.is_superuser}, is_staff={u.is_staff}")

if not users:
    print("  (No users found)")
