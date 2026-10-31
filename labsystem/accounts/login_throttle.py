"""Brute-force protection for every sign-in form (hospital, Books, /admin/).

Two counters guard each attempt:
- per username: 5 failures in 15 minutes locks that username for 15 minutes;
- per IP address: 20 failures in 15 minutes locks that address for 15 minutes.

Locks are checked *before* the password is hashed. Password hashing is
deliberately slow, so without this a burst of guesses would pin the droplet's
CPU; a locked attempt now costs one indexed database lookup.
"""

import logging
import math
from datetime import timedelta

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import LoginThrottle

logger = logging.getLogger("accounts.security")

WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)
MAX_USER_FAILURES = 5
MAX_IP_FAILURES = 20


def client_ip(request):
    """The caller's address. DigitalOcean App Platform sets DO-Connecting-IP
    itself; X-Forwarded-For's first entry is client-controlled, so it's never
    trusted."""
    if request is None:
        return ""
    return (request.META.get("HTTP_DO_CONNECTING_IP") or request.META.get("REMOTE_ADDR") or "").strip()[:64]


def throttle_keys(request, username):
    keys = []
    if username:
        keys.append((f"user:{username.strip().lower()[:150]}", MAX_USER_FAILURES))
    ip = client_ip(request)
    if ip:
        keys.append((f"ip:{ip}", MAX_IP_FAILURES))
    return keys


def locked_seconds_remaining(request, username):
    now = timezone.now()
    keys = [key for key, _ in throttle_keys(request, username)]
    latest = (
        LoginThrottle.objects.filter(key__in=keys, locked_until__gt=now)
        .order_by("-locked_until")
        .values_list("locked_until", flat=True)
        .first()
    )
    return (latest - now).total_seconds() if latest else 0


def record_failure(request, username):
    now = timezone.now()
    for key, limit in throttle_keys(request, username):
        with transaction.atomic():
            row = LoginThrottle.objects.select_for_update().filter(key=key).first()
            if row is None:
                try:
                    with transaction.atomic():
                        LoginThrottle.objects.create(key=key, failures=1, window_started_at=now)
                    continue
                except IntegrityError:
                    row = LoginThrottle.objects.select_for_update().get(key=key)
            if now - row.window_started_at > WINDOW:
                row.failures = 1
                row.window_started_at = now
                row.locked_until = None
                row.save(update_fields=["failures", "window_started_at", "locked_until"])
                continue
            LoginThrottle.objects.filter(pk=row.pk).update(failures=F("failures") + 1)
            row.refresh_from_db(fields=["failures"])
            if row.failures >= limit and not (row.locked_until and row.locked_until > now):
                row.locked_until = now + LOCKOUT
                row.save(update_fields=["locked_until"])
                logger.warning("Sign-in locked for %s after %s failed attempts.", key, row.failures)


def record_success(request, username):
    # Only the username counter is cleared: signing in to one account must not
    # reset an attacker's address-wide count.
    if username:
        LoginThrottle.objects.filter(key=f"user:{username.strip().lower()[:150]}").delete()


class ThrottledAuthenticationMixin:
    lockout_message = "Too many failed sign-in attempts. Please wait {minutes} minute{plural} and try again."

    def clean(self):
        username = self.cleaned_data.get("username")
        remaining = locked_seconds_remaining(self.request, username)
        if remaining:
            minutes = max(1, math.ceil(remaining / 60))
            raise forms.ValidationError(
                self.lockout_message.format(minutes=minutes, plural="" if minutes == 1 else "s"),
                code="locked",
            )
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            record_failure(self.request, username)
            raise
        record_success(self.request, username)
        return cleaned


class ThrottledAuthenticationForm(ThrottledAuthenticationMixin, AuthenticationForm):
    pass


class ThrottledAdminAuthenticationForm(ThrottledAuthenticationMixin, AdminAuthenticationForm):
    pass
