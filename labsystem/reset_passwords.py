import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

# Sets well-known accounts to a known password: for a local SQLite copy only.
# Run against the real database, it would open those accounts to anyone.
import sys
from django.conf import settings

if not settings.DEBUG or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
    sys.exit("Refusing to run: this resets passwords and is only for a local SQLite database with DEBUG on.")

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
