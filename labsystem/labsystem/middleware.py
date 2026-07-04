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
<head><meta charset="UTF-8"><title>Subscription Expired — Ternah EMR</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column;
       align-items:center;justify-content:center;background:#0B1430;padding:24px}
  .card{text-align:center;max-width:460px;width:100%;padding:44px 36px;background:#fff;
        border-radius:16px;box-shadow:0 8px 40px rgba(0,0,0,.3)}
  .logo-wrap{width:64px;height:64px;background:#0B1430;border-radius:12px;
             display:flex;align-items:center;justify-content:center;margin:0 auto 20px}
  .logo-wrap svg{width:36px;height:36px;fill:#fff}
  .badge{display:inline-block;padding:4px 14px;border-radius:999px;
         background:#fef2f2;color:#991b1b;font-size:0.78rem;font-weight:700;
         letter-spacing:.04em;text-transform:uppercase;margin-bottom:16px}
  h1{font-size:1.35rem;color:#0B1430;font-weight:700;margin-bottom:10px}
  p{font-size:0.93rem;color:#6b7280;line-height:1.6;margin-bottom:28px}
  .wa-btn{display:inline-flex;align-items:center;gap:10px;
          background:#25D366;color:#fff;text-decoration:none;
          padding:13px 24px;border-radius:10px;font-weight:700;font-size:0.95rem;
          transition:background .15s}
  .wa-btn:hover{background:#1ebe5d}
  .wa-btn svg{width:22px;height:22px;fill:#fff;flex-shrink:0}
  .footer{margin-top:24px;font-size:0.78rem;color:#9ca3af}
  .footer a{color:#4F5BD5;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <div class="logo-wrap">
    <svg viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg">
      <rect x="4" y="4" width="13" height="13" rx="2"/>
      <rect x="19" y="4" width="13" height="13" rx="2" opacity=".6"/>
      <rect x="4" y="19" width="13" height="13" rx="2" opacity=".6"/>
      <rect x="19" y="19" width="13" height="13" rx="2" opacity=".35"/>
    </svg>
  </div>
  <span class="badge">Subscription Expired</span>
  <h1>Your subscription has ended</h1>
  <p>Access to your hospital account has been suspended because your subscription period has elapsed.
     Tap the button below to contact us on WhatsApp and we will get you back online right away.</p>
  <a class="wa-btn" href="https://wa.me/256787770007?text=Hello%2C%20I%20would%20like%20to%20renew%20my%20Ternah%20EMR%20subscription." target="_blank" rel="noopener">
    <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413Z"/>
    </svg>
    Chat with us on WhatsApp
  </a>
  <div class="footer">
    Already renewed? <a href="/">Refresh this page</a> &nbsp;·&nbsp;
    <a href="/welcome/">About Ternah Health</a>
  </div>
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
