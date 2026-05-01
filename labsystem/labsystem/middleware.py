from django.conf import settings
from django.contrib.auth import logout
from django.utils import timezone

from accounts.models import Hospital


SESSION_ACTIVITY_KEY = "_session_last_activity_ts"


class HospitalMiddleware:
    """
    Attach the current hospital context to the request.

    This is intentionally lightweight for the first foundation slice:
    - superadmins stay global,
    - authenticated hospital-linked users use their assigned hospital,
    - optional subdomain detection is available as a fallback.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.hospital = None

        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            if getattr(user, "is_superuser", False) or getattr(user, "role", "") == "superadmin":
                return self.get_response(request)
            if getattr(user, "hospital_id", None):
                request.hospital = user.hospital
                return self.get_response(request)

        host = request.get_host().split(":", 1)[0]
        if "." in host and host not in {"127.0.0.1", "localhost"}:
            subdomain = host.split(".", 1)[0]
            request.hospital = Hospital.objects.filter(
                subdomain__iexact=subdomain,
                is_active=True,
            ).first()

        return self.get_response(request)


class SessionIdleTimeoutMiddleware:
    """Log authenticated users out after a configurable period of inactivity."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timeout_seconds = getattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS", None)
        if timeout_seconds and getattr(request, "user", None) and request.user.is_authenticated:
            now_ts = int(timezone.now().timestamp())
            last_activity = request.session.get(SESSION_ACTIVITY_KEY)
            if last_activity is not None and now_ts - int(last_activity) > int(timeout_seconds):
                logout(request)
            else:
                request.session[SESSION_ACTIVITY_KEY] = now_ts

        response = self.get_response(request)

        if getattr(request, "user", None) and request.user.is_authenticated and timeout_seconds:
            request.session[SESSION_ACTIVITY_KEY] = int(timezone.now().timestamp())

        return response
