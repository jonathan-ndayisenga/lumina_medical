import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'labsystem.settings')
django.setup()

from accounts.models import User
users = User.objects.all()
if not users:
    print("No users found in the database.")
else:
    for u in users:
        print(f"Username: {u.username}, Role: {u.role}, Superuser: {u.is_superuser}")
