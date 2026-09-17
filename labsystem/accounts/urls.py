from django.urls import path

from .views import (
    RoleAwareLoginView,
    app_home,
    direct_message_compose,
    direct_message_delete,
    direct_message_detail,
    dismiss_expiry_banner,
    enter_nav_section,
    landing,
    message_mark_all_read,
    message_mark_read,
    messages_inbox,
    # legacy aliases
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
    org_branch_home,
    org_branch_modules,
    org_dashboard,
    org_enter_branch,
    org_revenue_comparison,
)

urlpatterns = [
    path("", RoleAwareLoginView.as_view(), name="login"),
    path("home/", app_home, name="app_home"),
    path("home/section/<str:section_key>/", enter_nav_section, name="enter_nav_section"),
    path("welcome/", landing, name="landing"),

    # ── Organization / multi-branch Owner views ─────────────────────────────────
    path("org/", org_dashboard, name="org_dashboard"),
    path("org/branch/<int:hospital_id>/enter/", org_enter_branch, name="org_enter_branch"),
    path("org/branch/", org_branch_home, name="org_branch_home"),
    path("org/branch/modules/", org_branch_modules, name="org_branch_modules"),
    path("org/revenue/", org_revenue_comparison, name="org_revenue_comparison"),

    # ── Unified Messages Inbox ────────────────────────────────────────────────
    path("messages/", messages_inbox, name="messages_inbox"),
    path("messages/mark-read/", message_mark_read, name="message_mark_read"),
    path("messages/mark-all-read/", message_mark_all_read, name="message_mark_all_read"),

    # ── Direct Messages ───────────────────────────────────────────────────────
    path("messages/compose/", direct_message_compose, name="direct_message_compose"),
    path("messages/<int:pk>/", direct_message_detail, name="direct_message_detail"),
    path("messages/<int:pk>/delete/", direct_message_delete, name="direct_message_delete"),

    # ── Expiry banner dismiss ─────────────────────────────────────────────────
    path("notifications/dismiss-expiry/", dismiss_expiry_banner, name="dismiss_expiry_banner"),

    # ── Legacy notification URLs (redirect to inbox) ──────────────────────────
    path("notifications/", notification_list, name="notification_list"),
    path("notifications/<int:pk>/read/", notification_mark_read, name="notification_mark_read"),
    path("notifications/read-all/", notification_mark_all_read, name="notification_mark_all_read"),
]
