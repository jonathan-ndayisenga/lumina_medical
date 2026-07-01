from datetime import date as _date

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponse
from django.utils import timezone

from accounts.models import Hospital


SESSION_ACTIVITY_KEY = "_session_last_activity_ts"

SUBSCRIPTION_EXPIRED_HTML = """
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Subscription Expired</title>
<style>
  body { font-family: sans-serif; display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; background: #f9fafb; }
  .box { text-align: center; max-width: 440px; padding: 40px; background: #fff;
         border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,.1); }
  h1 { font-size: 1.4rem; color: #111827; margin-bottom: 12px; }
  p  { font-size: 0.95rem; color: #6b7280; line-height: 1.5; }
  .badge { display: inline-block; padding: 4px 12px; border-radius: 999px;
           background: #fef2f2; color: #991b1b; font-size: 0.8rem; font-weight: 600;
           margin-bottom: 18px; }
</style>
</head>
<body>
<div class="box">
  <span class="badge">Subscription Expired</span>
  <h1>Your subscription has ended</h1>
  <p>Access to this hospital account has been suspended because the subscription period has elapsed.
     Please contact Lumina Medical Services to renew.</p>
</div>
</body>
</html>
"""


def _hospital_subscription_expired(hospital) -> bool:
    """Return True if the hospital has a subscription_end_date that has already passed."""
    end = getattr(hospital, "subscription_end_date", None)
    return end is not None and end < _date.today()


class HospitalMiddleware:
    """
    Attach the current hospital context to the request and enforce subscription expiry.

    - Superadmins bypass all checks (they manage the platform globally).
    - If a hospital's subscription_end_date is in the past, all non-superadmin
      requests receive a 402-style subscription-expired page.
    - The management command `deactivate_expired_hospitals` is the canonical
      daily cleanup job; this middleware is the real-time safety net for the
      window before that command runs.
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
                hospital = user.hospital
                request.hospital = hospital
                if not getattr(hospital, "is_active", True):
                    return HttpResponse(SUBSCRIPTION_EXPIRED_HTML, status=402)
                if _hospital_subscription_expired(hospital):
                    # Deactivate inline so future requests hit the is_active check directly
                    hospital.is_active = False
                    hospital.save(update_fields=["is_active"])
                    return HttpResponse(SUBSCRIPTION_EXPIRED_HTML, status=402)
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
