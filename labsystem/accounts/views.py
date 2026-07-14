from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .models import NotificationRead, SystemNotification


def _hospital_admin_home(user) -> str:
    """Pick the correct landing URL for a hospital_admin based on their subscribed modules."""
    hospital = getattr(user, "hospital", None)
    if hospital is not None:
        codes = set(hospital.active_module_codes)
        # Homecare-only hospital: skip hospital_dashboard (requires hospital_mgmt)
        if "home_care" in codes and "hospital_mgmt" not in codes:
            return reverse("homecare_dashboard")
    return reverse("hospital_dashboard")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class RoleAwareLoginView(LoginView):
    """Always land users on the correct module after login."""

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_superuser or getattr(user, "role", "") == "superadmin":
            return reverse("developer_dashboard")
        if getattr(user, "role", "") == "hospital_admin":
            return _hospital_admin_home(user)

        # Multi-role support via groups (non-breaking: role-based still works).
        if user.groups.filter(name="Reception").exists():
            return reverse("reception_dashboard")
        if user.groups.filter(name="Doctor").exists():
            return reverse("doctor_queue")
        if user.groups.filter(name="Lab").exists():
            return reverse("lab_queue")
        if user.groups.filter(name="Nurse").exists():
            return reverse("nurse_queue")
        if getattr(user, "role", "") == "receptionist":
            return reverse("reception_dashboard")
        if getattr(user, "role", "") == "lab_attendant":
            return reverse("lab_queue")
        if getattr(user, "role", "") == "doctor":
            return reverse("doctor_queue")
        if getattr(user, "role", "") == "nurse":
            return reverse("nurse_queue")
        return reverse("report_list")


@login_required
def app_home(request):
    """Redirect authenticated users to their role-specific dashboard."""

    user = request.user

    if user.is_superuser or getattr(user, "role", "") == "superadmin":
        return redirect("developer_dashboard")

    if getattr(user, "role", "") == "hospital_admin":
        return redirect(_hospital_admin_home(user))

    if getattr(user, "role", "") == "accountant":
        return redirect("finance_dashboard")

    # Multi-role support via groups (non-breaking: role-based still works).
    if user.groups.filter(name="Reception").exists():
        return redirect("reception_dashboard")
    if user.groups.filter(name="Doctor").exists():
        return redirect("doctor_queue")
    if user.groups.filter(name="Lab").exists():
        return redirect("lab_queue")
    if user.groups.filter(name="Nurse").exists():
        return redirect("nurse_queue")

    if getattr(user, "role", "") == "receptionist":
        return redirect("reception_dashboard")
    if getattr(user, "role", "") == "lab_attendant":
        return redirect("lab_queue")
    if getattr(user, "role", "") == "doctor":
        return redirect("doctor_queue")
    if getattr(user, "role", "") == "nurse":
        return redirect("nurse_queue")

    return redirect("report_list")


def landing(request):
    """Public-facing Ternah Health landing page."""
    if request.user.is_authenticated:
        return redirect("app_home")
    return render(request, "landing.html")


@login_required
def notification_list(request):
    hospital = getattr(request.user, "hospital", None)
    notifications = SystemNotification.objects.filter(is_active=True).filter(
        Q(hospital=hospital) | Q(hospital__isnull=True)
    ).order_by("-created_at")
    read_ids = set(
        NotificationRead.objects.filter(user=request.user).values_list("notification_id", flat=True)
    )
    return render(request, "accounts/notification_list.html", {
        "notifications": notifications,
        "read_ids": read_ids,
    })


@login_required
@require_POST
def notification_mark_read(request, pk):
    notification = get_object_or_404(SystemNotification, pk=pk, is_active=True)
    NotificationRead.objects.get_or_create(notification=notification, user=request.user)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    return redirect("notification_list")


@login_required
@require_POST
def notification_mark_all_read(request):
    hospital = getattr(request.user, "hospital", None)
    qs = SystemNotification.objects.filter(is_active=True).filter(
        Q(hospital=hospital) | Q(hospital__isnull=True)
    ).exclude(reads__user=request.user)
    for n in qs:
        NotificationRead.objects.get_or_create(notification=n, user=request.user)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    return redirect("notification_list")


def csrf_failure(request, reason="", template_name="errors/csrf_failure.html"):
    """Show a friendlier CSRF failure page with next-step guidance."""

    return render(
        request,
        template_name,
        {
            "csrf_failure_reason": reason,
        },
        status=403,
    )
