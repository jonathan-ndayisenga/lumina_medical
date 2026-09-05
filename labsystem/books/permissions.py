from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def _is_books_user(user) -> bool:
    """Scoped to one specific hospital tenant, never to the raw is_staff flag —
    that flag is set broadly across every customer hospital's staff (see
    accounts.models.User.save()) and would otherwise leak access to Ternah's
    own internal books to any hospital's receptionist, doctor, nurse, etc."""
    if not user.is_authenticated or not user.is_active:
        return False
    hospital = getattr(user, "hospital", None)
    return bool(hospital and hospital.subdomain == settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN)


def _is_books_admin(user) -> bool:
    """Role-only check, deliberately not `can_access_hospital_admin` — that
    property also requires the hospital_mgmt Module subscription, which would
    re-couple this standalone app to Lumina's per-hospital module system."""
    if not _is_books_user(user):
        return False
    return bool(user.is_superadmin or user.role == user.ROLE_HOSPITAL_ADMIN)


def books_staff_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not _is_books_user(request.user):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url="books:login")
            raise PermissionDenied("You do not have access to Ternah Books.")
        return view_func(request, *args, **kwargs)

    return wrapped


def books_admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not _is_books_user(request.user):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url="books:login")
            raise PermissionDenied("You do not have access to Ternah Books.")
        if not _is_books_admin(request.user):
            raise PermissionDenied("Only a Ternah Books administrator can do that.")
        return view_func(request, *args, **kwargs)

    return wrapped
