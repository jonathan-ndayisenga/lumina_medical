#!/usr/bin/env python
import django
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import User

# Get the devadmin user (or any first user)
user = User.objects.first()
if user:
    print(f'Found user: {user.username}')
    print(f'Current role: {user.role}')
    print(f'Current is_superuser: {user.is_superuser}')
    
    # Set role to superadmin (the save method will automatically set is_superuser=True)
    if user.role != 'superadmin':
        user.role = 'superadmin'
        user.save()
        print(f'✓ Role updated to superadmin')
        print(f'✓ is_superuser now: {user.is_superuser}')
    else:
        print(f'✓ Role is already superadmin')
else:
    print('No users found! You need to create a user first.')

