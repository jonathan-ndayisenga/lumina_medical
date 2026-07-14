from django.urls import path

from .views import (
    RoleAwareLoginView,
    app_home,
    dismiss_expiry_banner,
    landing,
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
)

urlpatterns = [
    path("", RoleAwareLoginView.as_view(), name="login"),
    path("home/", app_home, name="app_home"),
    path("welcome/", landing, name="landing"),
    path("notifications/", notification_list, name="notification_list"),
    path("notifications/<int:pk>/read/", notification_mark_read, name="notification_mark_read"),
    path("notifications/read-all/", notification_mark_all_read, name="notification_mark_all_read"),
    path("notifications/dismiss-expiry/", dismiss_expiry_banner, name="dismiss_expiry_banner"),
]
