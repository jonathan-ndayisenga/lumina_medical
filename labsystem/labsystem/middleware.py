from accounts.models import Hospital


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
            if getattr(user, "role", "") == "superadmin":
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
