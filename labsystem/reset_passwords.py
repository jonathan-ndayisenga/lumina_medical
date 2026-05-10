import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import User

users_to_reset = [
    'hospital_admin',
    'superadmin',
    'doctor',
    'receptionist',
    'nurse',
    'lab_attendant'
]

password = 'admin123'

for username in users_to_reset:
    try:
        u = User.objects.get(username=username)
        u.set_password(password)
        u.save()
        print(f"Password reset for {username}")
    except User.DoesNotExist:
        print(f"User {username} does not exist")
