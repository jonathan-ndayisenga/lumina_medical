from datetime import date

from django.db.models import Q

from .models import SystemNotification


def notifications(request):
    if not request.user.is_authenticated or getattr(request.user, "is_superadmin", False):
        return {}

    hospital = getattr(request.user, "hospital", None)

    # Subscription expiry alert
    expiry_alert = None
    if hospital and hospital.subscription_end_date:
        days = (hospital.subscription_end_date - date.today()).days
        expiry_alert = {
            "days": days,
            "expired": days < 0,
            "urgent": days <= 7,
        }

    # Unread system notifications visible to this user
    qs = SystemNotification.objects.filter(is_active=True).filter(
        Q(hospital=hospital) | Q(hospital__isnull=True)
    ).exclude(reads__user=request.user)

    unread_notifications = list(qs[:10])
    unread_count = qs.count() + (1 if expiry_alert else 0)

    return {
        "expiry_alert": expiry_alert,
        "unread_notifications": unread_notifications,
        "notification_unread_count": unread_count,
    }
