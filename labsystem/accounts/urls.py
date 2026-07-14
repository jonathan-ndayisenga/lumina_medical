from django.urls import path

from .views import (
    RoleAwareLoginView,
    app_home,
    direct_message_compose,
    direct_message_delete,
    direct_message_detail,
    dismiss_expiry_banner,
    landing,
    message_mark_all_read,
    message_mark_read,
    messages_inbox,
    # legacy aliases
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
)

urlpatterns = [
    path("", RoleAwareLoginView.as_view(), name="login"),
    path("home/", app_home, name="app_home"),
    path("welcome/", landing, name="landing"),

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
