from datetime import date

from django.db.models import Q

from .models import SystemNotification

_LEVEL_RANK = {"warning": 1, "urgent": 2, "expired": 3}


def _expiry_level(days):
    if days < 0:
        return "expired"
    if days <= 7:
        return "urgent"
    return "warning"


def notifications(request):
    if not request.user.is_authenticated or getattr(request.user, "is_superadmin", False):
        return {}

    hospital = getattr(request.user, "hospital", None)

    # Subscription expiry alert — suppressed if the user already dismissed this level
    expiry_alert = None
    if hospital and hospital.subscription_end_date:
        days = (hospital.subscription_end_date - date.today()).days
        if days <= 30:
            level = _expiry_level(days)
            dismissed = request.session.get("expiry_dismissed", "")
            # Show if not dismissed, or if current level is more severe than dismissed level
            if not dismissed or _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(dismissed, 0):
                expiry_alert = {
                    "days": days,
                    "expired": days < 0,
                    "urgent": days <= 7,
                    "level": level,
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
