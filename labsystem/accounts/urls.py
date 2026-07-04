from django.urls import path

from .views import RoleAwareLoginView, app_home, landing

urlpatterns = [
    path("", RoleAwareLoginView.as_view(), name="login"),
    path("home/", app_home, name="app_home"),
    path("welcome/", landing, name="landing"),
]
