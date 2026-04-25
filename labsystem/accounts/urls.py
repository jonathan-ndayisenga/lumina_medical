from django.urls import path

from .views import RoleAwareLoginView, app_home

urlpatterns = [
    # Root login page - authenticated users redirect to app_home (role-based router)
    path("", RoleAwareLoginView.as_view(), name="login"),
    path("home/", app_home, name="app_home"),  # Role-based dashboard router
]
