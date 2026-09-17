from datetime import date

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
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
    Hospital,
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
            return reverse("homecare_dashboard")
    return reverse("hospital_dashboard")


def _lab_queue_home(user) -> str:
    """Which lab engine's queue this user's hospital is actually on —
    single source of truth is lab.routing.lab_queue_url."""
    from lab.routing import lab_queue_url
    return lab_queue_url(getattr(user, "hospital", None))


# Ordered registry of "sections" a user can land in from the Home tile picker.
# Each entry's `check` is a boolean property on User; `url` is either a URL name
# or a callable(user) -> url string for sections whose landing page varies.
NAV_SECTIONS = [
    {
        "key": "hospital_admin",
        "label": "Hospital Management",
        "description": "Dashboard, staff, services, reports",
        "icon": "M4 21V7m0 0l8-4 8 4m-8 14V11m-4 10h8",
        "check": "can_access_hospital_admin",
        "url": _hospital_admin_home,
    },
    {
        "key": "finance",
        "label": "Finance",
        "description": "Financials, expenses, salaries, ledger, receipts",
        "icon": "M12 1v22M17 5H9a4 4 0 0 0 0 8h6a4 4 0 1 1 0 8H6",
        "check": "can_access_finance",
        "url": "financial_report",
    },
    {
        "key": "inventory",
        "label": "Inventory",
        "description": "Stock levels, restock insights",
        "icon": "M20 7 12 3 4 7m16 0v10l-8 4-8-4V7m16 0-8 4m-8-4 8 4m0 0v10",
        "check": "can_access_inventory",
        "url": "manage_inventory",
    },
    {
        "key": "reception",
        "label": "Reception",
        "description": "Registration, queue, patients",
        "icon": "M4 6h16M4 12h16M4 18h10",
        "check": "can_access_reception",
        "url": "reception_dashboard",
    },
    {
        "key": "doctor",
        "label": "Doctor",
        "description": "Doctor queue and consultations",
        "icon": "M12 3v18M3 12h18",
        "check": "can_access_doctor",
        "url": "doctor_queue",
    },
    {
        "key": "nurse",
        "label": "Nursing",
        "description": "Nurse queue and IV care",
        "icon": "M9 12l2 2 4-4M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z",
        "check": "can_access_nurse",
        "url": "nurse_queue",
    },
    {
        "key": "sonographer",
        "label": "Sonographer",
        "description": "Scan queue and reports",
        "icon": "M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2v-4M9 21H5a2 2 0 0 1-2-2v-4m0 0h18",
        "check": "can_access_sonographer",
        "url": "scan_queue",
    },
    {
        "key": "home_care",
        "label": "Home Care",
        "description": "Clients, nurses, placements, contracts",
        "icon": "M3 12l9-9 9 9M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10",
        "check": "can_access_home_care",
        "url": "homecare_dashboard",
    },
    {
        "key": "lab",
        "label": "Laboratory",
        "description": "Lab queue, reports, templates",
        "icon": "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
        "check": "can_access_lab",
        "url": _lab_queue_home,
    },
]


def _section_url(section, user) -> str:
    url = section["url"]
    return url(user) if callable(url) else reverse(url)


def _accessible_sections(user):
    return [s for s in NAV_SECTIONS if getattr(user, s["check"], False)]


@method_decorator(ensure_csrf_cookie, name="dispatch")
class RoleAwareLoginView(LoginView):
    template_name = "registration/login.html"

    def form_valid(self, form):
        user = form.get_user()
        hospital = getattr(user, "hospital", None)
        if (
            not user.is_superadmin
            and hospital is not None
            and hospital.subdomain == settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN
        ):
            form.add_error(None, "This account is for Ternah Books — sign in at the Ternah Books login instead.")
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_success_url(self):
        if self.request.user.is_superadmin:
            return reverse("developer_dashboard")
        return reverse("app_home")


@login_required
def app_home(request):
    user = request.user
    if user.is_superadmin:
        return redirect("developer_dashboard")
    if user.is_owner:
        return redirect("org_dashboard")

    hospital = getattr(user, "hospital", None)
    if hospital is not None and hospital.subdomain == settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN:
        logout(request)
        return redirect("books:login")

    sections = _accessible_sections(user)
    if len(sections) <= 1:
        if sections:
            # Single-module staff never click a tile (enter_nav_section is the
            # only other place this gets set), so set it here too — otherwise
            # the sidebar falls back to its bare "Home" stub with no section
            # links, including the queue badges, for most day-to-day users.
            request.session["nav_section"] = sections[0]["key"]
            return redirect(_section_url(sections[0], user))
        request.session.pop("nav_section", None)
        return render(request, "accounts/home.html", {"tiles": [], "hide_sidebar_nav": True})

    request.session.pop("nav_section", None)
    from reception.workflow import queue_counts_for_hospital
    queue_counts = queue_counts_for_hospital(hospital)
    tiles = [
        {
            "key": s["key"],
            "label": s["label"],
            "description": s["description"],
            "icon": s["icon"],
            "url": reverse("enter_nav_section", args=[s["key"]]),
            # The Lab tile is one door into Phlebotomy + Lab Queue both — its
            # badge needs to reflect work waiting in either, not just the lab
            # queue proper, or a patient sent to Phlebotomy never shows up as
            # "something to do" from Home. The sidebar's own separate
            # Phlebotomy Queue / Lab Queue links keep their individual counts
            # unchanged — this combination is Home-tile-only.
            "queue_count": (
                queue_counts.get(s["key"], 0) + queue_counts.get("phlebotomy", 0)
                if s["key"] == "lab" else queue_counts.get(s["key"], 0)
            ),
        }
        for s in sections
    ]
    return render(request, "accounts/home.html", {"tiles": tiles, "hide_sidebar_nav": True})


@login_required
def enter_nav_section(request, section_key):
    user = request.user
    section = next((s for s in NAV_SECTIONS if s["key"] == section_key), None)
    if not section or not getattr(user, section["check"], False):
        raise PermissionDenied("You do not have access to that section.")
    request.session["nav_section"] = section_key
    return redirect(_section_url(section, user))


# ── Organization / multi-branch Owner views ───────────────────────────────────

def _owner_required(user):
    if not getattr(user, "is_owner", False):
        raise PermissionDenied("This page is available to organization owner accounts only.")


@login_required
def org_dashboard(request):
    """Landing page for an Owner: one tile per branch in their organization,
    plus a tile into the cross-branch revenue comparison."""
    user = request.user
    _owner_required(user)
    request.session.pop("owner_active_hospital_id", None)

    org = user.organization
    hospitals = org.hospitals.order_by("name") if org else Hospital.objects.none()
    tiles = [
        {
            "hospital": h,
            "user_count": h.users.filter(is_active=True).count(),
            "module_count": len(h.active_module_codes),
        }
        for h in hospitals
    ]
    return render(request, "accounts/org_dashboard.html", {
        "organization": org,
        "tiles": tiles,
        "hide_sidebar_nav": True,
    })


@login_required
def org_enter_branch(request, hospital_id):
    """Select a branch as the active hospital for this Owner's session, then
    drop them into that branch's mini dashboard (Users / Modules / Revenue)."""
    user = request.user
    _owner_required(user)
    hospital = get_object_or_404(Hospital, pk=hospital_id)
    if not user.owns_hospital(hospital):
        raise PermissionDenied("That branch is not part of your organization.")
    request.session["owner_active_hospital_id"] = hospital.pk
    return redirect("org_branch_home")


@login_required
def org_branch_home(request):
    """Mini tile picker inside one branch — Users, Modules (read-only), Revenue."""
    user = request.user
    _owner_required(user)
    hospital = getattr(request, "hospital", None)
    if hospital is None or not user.owns_hospital(hospital):
        return redirect("org_dashboard")

    tiles = [
        {
            "key": "users",
            "label": "Users",
            "description": "Staff accounts for this branch",
            "url": reverse("manage_users"),
        },
        {
            "key": "modules",
            "label": "Modules",
            "description": "Modules this branch has active (read-only)",
            "url": reverse("org_branch_modules"),
        },
        {
            "key": "revenue",
            "label": "Revenue Report",
            "description": "Revenue by category, with receipts",
            "url": reverse("finance_revenue"),
        },
    ]
    return render(request, "accounts/org_branch_home.html", {
        "organization": user.organization,
        "hospital": hospital,
        "tiles": tiles,
        "hide_sidebar_nav": True,
    })


@login_required
def org_branch_modules(request):
    """Read-only view of a branch's active modules. Owners cannot toggle
    subscriptions — that stays exclusively superadmin-controlled."""
    user = request.user
    _owner_required(user)
    hospital = getattr(request, "hospital", None)
    if hospital is None or not user.owns_hospital(hospital):
        return redirect("org_dashboard")

    subscriptions = hospital.module_subscriptions.select_related("module").order_by("module__display_order", "module__name")
    return render(request, "accounts/org_branch_modules.html", {
        "organization": user.organization,
        "hospital": hospital,
        "subscriptions": subscriptions,
    })


@login_required
def org_revenue_comparison(request):
    """Cross-branch revenue comparison for an Owner's organization — total
    revenue per branch (and per category) over a chosen date range."""
    from django.db.models import Sum
    from django.utils import timezone as _tz
    from finance.models import Account, JournalLine

    user = request.user
    _owner_required(user)
    org = user.organization
    hospitals = list(org.hospitals.order_by("name")) if org else []

    today = _tz.localdate()
    date_from = request.GET.get("from", today.replace(day=1).isoformat())
    date_to = request.GET.get("to", today.isoformat())

    rows = []
    grand_total = 0
    for h in hospitals:
        total = (
            JournalLine.objects.filter(
                account__hospital=h,
                account__account_type=Account.TYPE_REVENUE,
                entry__date__gte=date_from,
                entry__date__lte=date_to,
                entry__is_reversal=False,
                entry__reversal_of__isnull=True,
            ).aggregate(t=Sum("credit"))["t"] or 0
        )
        rows.append({"hospital": h, "total": total})
        grand_total += total

    return render(request, "accounts/org_revenue_comparison.html", {
        "organization": org,
        "rows": rows,
        "grand_total": grand_total,
        "date_from": date_from,
        "date_to": date_to,
    })


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
