#!/usr/bin/env python
"""Create superuser account for the software owner"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import User

# Check if superuser exists
if User.objects.filter(username='admin').exists():
    print('✅ Superuser "admin" already exists')
    user = User.objects.get(username='admin')
else:
    # Create superuser
    user = User.objects.create_user('admin', 'admin@lumina.local', 'admin123')
    user.role = 'superadmin'
    user.is_staff = True
    user.is_superuser = True
    user.save()
    print('✅ Superuser "admin" created')

print(f'\n📋 Superuser Details:')
print(f'   Username: {user.username}')
print(f'   Email: {user.email}')
print(f'   Role: {user.role}')
print(f'   Is Superuser: {user.is_superuser}')
print(f'   Is Staff: {user.is_staff}')
print(f'\n🔓 Login Credentials:')
print(f'   Username: admin')
print(f'   Password: admin123')
print(f'   Login URL: http://localhost:8000/')
