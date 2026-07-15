from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .models import (
    DirectMessage,
    InternalNotification,
    InternalNotificationRead,
    NotificationRead,
    PlatformSettings,
    SystemNotification,
)


def _hospital_admin_home(user) -> str:
    """Pick the correct landing URL for a hospital_admin based on their subscribed modules."""
    hospital = getattr(user, "hospital", None)
    if hospital is not None:
        codes = set(hospital.active_module_codes)
        # Homecare-only hospital: skip hospital_dashboard (requires hospital_mgmt)
        if "home_care" in codes and "hospital_mgmt" not in codes:
            from homecare.models import HomecarePatient  # noqa: F401 — just checking module exists
            return reverse("homecare_dashboard")
    return reverse("hospital_dashboard")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class RoleAwareLoginView(LoginView):
    template_name = "registration/login.html"

    def get_success_url(self):
        user = self.request.user
        if user.is_superadmin:
            return reverse("developer_dashboard")
        role = getattr(user, "role", None)
        if role == user.ROLE_HOSPITAL_ADMIN:
            return _hospital_admin_home(user)
        if role == user.ROLE_RECEPTIONIST:
            return reverse("reception_queue")
        if role == user.ROLE_LAB_ATTENDANT:
            return reverse("lab_queue")
        if role == user.ROLE_DOCTOR:
            return reverse("doctor_queue")
        if role == user.ROLE_NURSE:
            return reverse("nurse_queue")
        if role == user.ROLE_SONOGRAPHER:
            return reverse("scan_queue")
        return reverse("app_home")


@login_required
def app_home(request):
    user = request.user
    if user.is_superadmin:
        return redirect("developer_dashboard")
    role = getattr(user, "role", None)
    if role == user.ROLE_HOSPITAL_ADMIN:
        return redirect(_hospital_admin_home(user))
    if role == user.ROLE_RECEPTIONIST:
        return redirect("reception_queue")
    if role == user.ROLE_LAB_ATTENDANT:
        return redirect("lab_queue")
    if role == user.ROLE_DOCTOR:
        return redirect("doctor_queue")
    if role == user.ROLE_NURSE:
        return redirect("nurse_queue")
    if role == user.ROLE_SONOGRAPHER:
        return redirect("scan_queue")
    return render(request, "accounts/home.html")


def landing(request):
    return render(request, "landing.html")


# ── Messages Inbox ────────────────────────────────────────────────────────────

@login_required
def messages_inbox(request):
    hospital = getattr(request.user, "hospital", None)
    ps = PlatformSettings.get()
    tab = request.GET.get("tab", "broadcast")

    # Expiry alert (reactivation warning)
    expiry_alert = None
    alert_days = getattr(hospital, "reactivation_alert_days", 7) if hospital else 7
    if hospital and hospital.subscription_end_date and alert_days > 0:
        days = (hospital.subscription_end_date - date.today()).days
        if days <= alert_days:
            expiry_alert = {
                "days": days,
                "expired": days < 0,
                "urgent": days <= 3,
            }

    # Broadcast tab — SystemNotifications visible to this user
    sys_qs = SystemNotification.objects.filter(is_active=True).filter(
        Q(hospital=hospital) | Q(hospital__isnull=True)
    ).order_by("-created_at")
    sys_read_ids = set(
        NotificationRead.objects.filter(user=request.user)
        .values_list("notification_id", flat=True)
    )

    # Internal tab — InternalNotifications from hospital admin
    int_qs = InternalNotification.objects.none()
    int_read_ids = set()
    if hospital and ps.internal_messages_enabled:
        int_qs = InternalNotification.objects.filter(
            hospital=hospital,
            is_active=True,
        ).filter(
            Q(recipient=request.user) | Q(recipient__isnull=True)
        ).order_by("-created_at")
        int_read_ids = set(
            InternalNotificationRead.objects.filter(user=request.user)
            .values_list("notification_id", flat=True)
        )

    # Private direct messages (received)
    priv_qs = DirectMessage.objects.none()
    if ps.direct_messages_enabled:
        priv_qs = DirectMessage.objects.filter(
            recipient=request.user,
            deleted_by_recipient=False,
        ).select_related("sender").order_by("-created_at")

    # Paginate whichever tab is active
    if tab == "internal":
        paginator = Paginator(int_qs, 10)
    elif tab == "private":
        paginator = Paginator(priv_qs, 10)
    else:
        paginator = Paginator(sys_qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "accounts/messages_inbox.html", {
        "tab": tab,
        "page_obj": page_obj,
        "sys_read_ids": sys_read_ids,
        "int_read_ids": int_read_ids,
        "expiry_alert": expiry_alert,
        "sys_unread_count": sys_qs.exclude(reads__user=request.user).count(),
        "int_unread_count": int_qs.exclude(reads__user=request.user).count() if hospital else 0,
        "priv_unread_count": priv_qs.filter(is_read=False).count(),
        "ps": ps,
    })


@login_required
@require_POST
def message_mark_read(request):
    """Mark a system or internal notification as read."""
    kind = request.POST.get("kind", "system")
    pk = request.POST.get("pk")
    hospital = getattr(request.user, "hospital", None)

    if kind == "internal" and hospital:
        notif = get_object_or_404(
            InternalNotification, pk=pk, hospital=hospital, is_active=True
        )
        notif_filter = Q(recipient=request.user) | Q(recipient__isnull=True)
        if InternalNotification.objects.filter(pk=pk).filter(notif_filter).exists():
            InternalNotificationRead.objects.get_or_create(
                notification=notif, user=request.user
            )
    else:
        notif = get_object_or_404(SystemNotification, pk=pk, is_active=True)
        NotificationRead.objects.get_or_create(notification=notif, user=request.user)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    tab = "internal" if kind == "internal" else "broadcast"
    return redirect(f"{reverse('messages_inbox')}?tab={tab}")


@login_required
@require_POST
def message_mark_all_read(request):
    """Mark all messages on the current tab as read."""
    kind = request.POST.get("kind", "system")
    hospital = getattr(request.user, "hospital", None)

    if kind == "internal" and hospital:
        qs = InternalNotification.objects.filter(
            hospital=hospital, is_active=True
        ).filter(
            Q(recipient=request.user) | Q(recipient__isnull=True)
        ).exclude(reads__user=request.user)
        for n in qs:
            InternalNotificationRead.objects.get_or_create(notification=n, user=request.user)
    else:
        qs = SystemNotification.objects.filter(is_active=True).filter(
            Q(hospital=hospital) | Q(hospital__isnull=True)
        ).exclude(reads__user=request.user)
        for n in qs:
            NotificationRead.objects.get_or_create(notification=n, user=request.user)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    tab = "internal" if kind == "internal" else "broadcast"
    return redirect(f"{reverse('messages_inbox')}?tab={tab}")


@login_required
@require_POST
def dismiss_expiry_banner(request):
    """Store which urgency level the user dismissed so the banner goes away."""
    level = request.POST.get("level", "")
    if level in ("warning", "urgent", "expired"):
        request.session["expiry_dismissed"] = level
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    return redirect(next_url)


# ── Direct Messages ───────────────────────────────────────────────────────────

@login_required
def direct_message_compose(request):
    """Compose and send a private message to another user in the same hospital."""
    from django.http import HttpResponseForbidden as _403
    if not PlatformSettings.get().direct_messages_enabled:
        return _403("Private messaging is currently disabled.")
    hospital = getattr(request.user, "hospital", None)
    if not hospital:
        return redirect(reverse("messages_inbox") + "?tab=private")

    staff_qs = hospital.users.filter(is_active=True).exclude(pk=request.user.pk).order_by("first_name", "last_name")

    if request.method == "POST":
        recipient_id = request.POST.get("recipient_id", "").strip()
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()

        if not recipient_id or not body:
            error = "Recipient and message body are required."
            return render(request, "accounts/direct_message_compose.html", {
                "staff": staff_qs,
                "error": error,
                "subject": subject,
                "body": body,
            })

        recipient = hospital.users.filter(pk=recipient_id, is_active=True).exclude(pk=request.user.pk).first()
        if not recipient:
            error = "Recipient not found."
            return render(request, "accounts/direct_message_compose.html", {
                "staff": staff_qs,
                "error": error,
            })

        DirectMessage.objects.create(
            hospital=hospital,
            sender=request.user,
            recipient=recipient,
            subject=subject,
            body=body,
        )
        return redirect(reverse("messages_inbox") + "?tab=private&sent=1")

    # Pre-select recipient if passed via query string
    preselect = request.GET.get("to", "")
    return render(request, "accounts/direct_message_compose.html", {
        "staff": staff_qs,
        "preselect": preselect,
    })


@login_required
def direct_message_detail(request, pk):
    """View a received direct message and mark it as read."""
    dm = get_object_or_404(
        DirectMessage,
        pk=pk,
        recipient=request.user,
        deleted_by_recipient=False,
    )
    if not dm.is_read:
        dm.is_read = True
        dm.save(update_fields=["is_read"])

    return render(request, "accounts/direct_message_detail.html", {"dm": dm})


@login_required
@require_POST
def direct_message_delete(request, pk):
    """Soft-delete a direct message for the current user."""
    dm = get_object_or_404(DirectMessage, pk=pk)
    if dm.recipient_id == request.user.pk:
        dm.deleted_by_recipient = True
        dm.save(update_fields=["deleted_by_recipient"])
    elif dm.sender_id == request.user.pk:
        dm.deleted_by_sender = True
        dm.save(update_fields=["deleted_by_sender"])
    return redirect(reverse("messages_inbox") + "?tab=private")


# Legacy aliases — kept so any existing links don't 404
@login_required
def notification_list(request):
    return redirect(reverse("messages_inbox") + "?tab=broadcast")


@login_required
@require_POST
def notification_mark_read(request, pk):
    from django.http import HttpResponse
    NotificationRead.objects.get_or_create(
        notification=get_object_or_404(SystemNotification, pk=pk, is_active=True),
        user=request.user,
    )
    return redirect("messages_inbox")


@login_required
@require_POST
def notification_mark_all_read(request):
    hospital = getattr(request.user, "hospital", None)
    qs = SystemNotification.objects.filter(is_active=True).filter(
        Q(hospital=hospital) | Q(hospital__isnull=True)
    ).exclude(reads__user=request.user)
    for n in qs:
        NotificationRead.objects.get_or_create(notification=n, user=request.user)
    return redirect("messages_inbox")


def csrf_failure(request, reason="", template_name="errors/csrf_failure.html"):
    return render(request, template_name, {"csrf_failure_reason": reason}, status=403)
