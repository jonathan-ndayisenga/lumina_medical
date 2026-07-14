from datetime import date

from django.db.models import Q

from .models import DirectMessage, InternalNotification, InternalNotificationRead, NotificationRead, PlatformSettings, SupportToken, SupportTokenMessage, SystemNotification

_LEVEL_RANK = {"warning": 1, "urgent": 2, "expired": 3}


def _expiry_level(days):
    if days < 0:
        return "expired"
    if days <= 3:
        return "urgent"
    return "warning"


def notifications(request):
    if not request.user.is_authenticated or getattr(request.user, "is_superadmin", False):
        return {}

    hospital = getattr(request.user, "hospital", None)

    # ── Subscription expiry alert ────────────────────────────────────────────
    expiry_alert = None
    alert_days = getattr(hospital, "reactivation_alert_days", 7) if hospital else 7
    if hospital and hospital.subscription_end_date and alert_days > 0:
        days = (hospital.subscription_end_date - date.today()).days
        if days <= alert_days:
            level = _expiry_level(days)
            dismissed = request.session.get("expiry_dismissed", "")
            if not dismissed or _LEVEL_RANK.get(level, 0) > _LEVEL_RANK.get(dismissed, 0):
                expiry_alert = {
                    "days": days,
                    "expired": days < 0,
                    "urgent": days <= 3,
                    "level": level,
                }

    # ── Unread system (broadcast) notifications ──────────────────────────────
    sys_unread_qs = SystemNotification.objects.filter(is_active=True).filter(
        Q(hospital=hospital) | Q(hospital__isnull=True)
    ).exclude(reads__user=request.user)
    sys_unread_count = sys_unread_qs.count()

    # ── Unread internal notifications (from hospital admin) ──────────────────
    ps = PlatformSettings.get()

    internal_unread_count = 0
    if hospital and ps.internal_messages_enabled:
        internal_unread_count = InternalNotification.objects.filter(
            hospital=hospital,
            is_active=True,
        ).filter(
            Q(recipient=request.user) | Q(recipient__isnull=True)
        ).exclude(reads__user=request.user).count()

    direct_unread_count = 0
    if ps.direct_messages_enabled:
        direct_unread_count = DirectMessage.objects.filter(
            recipient=request.user,
            is_read=False,
            deleted_by_recipient=False,
        ).count()

    total_unread = sys_unread_count + internal_unread_count + direct_unread_count + (1 if expiry_alert else 0)

    # ── Support token badge (hospital admins: unread provider replies) ────────
    token_unread_count = 0
    if hospital and getattr(request.user, "is_hospital_admin", False):
        token_unread_count = SupportTokenMessage.objects.filter(
            token__hospital=hospital,
            is_from_provider=True,
            read_by_recipient=False,
        ).count()

    # ── Support token badge (super admin: total open tokens) ─────────────────
    superadmin_open_token_count = 0
    if getattr(request.user, "is_superadmin", False) or request.user.is_superuser:
        superadmin_open_token_count = SupportToken.objects.filter(
            status__in=[SupportToken.STATUS_OPEN, SupportToken.STATUS_IN_PROGRESS]
        ).count()

    return {
        "expiry_alert": expiry_alert,
        "unread_notifications": list(sys_unread_qs[:5]),
        "notification_unread_count": total_unread,
        "message_unread_count": total_unread,
        "token_unread_count": token_unread_count,
        "superadmin_open_token_count": superadmin_open_token_count,
    }
