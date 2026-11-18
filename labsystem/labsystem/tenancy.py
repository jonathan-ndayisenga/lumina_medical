"""Keeping each hospital's records to that hospital.

Every list or lookup of hospital data goes through `scope_to_hospital`, which
fails closed: a person with no hospital in effect (an Owner who hasn't picked
a branch yet, an account set up without one) sees nothing, never everyone's.
"""


def is_platform_superadmin(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and (getattr(user, "is_superuser", False) or getattr(user, "role", "") == "superadmin")
    )


def scope_to_hospital(queryset, user, hospital, field="hospital"):
    """Limit `queryset` to `hospital` through `field` (e.g. "visit__hospital").

    Platform superadmins see every hospital, as before. Everyone else sees
    only their hospital's rows, and nothing at all when there is no hospital."""
    if is_platform_superadmin(user):
        return queryset
    if hospital is None:
        return queryset.none()
    return queryset.filter(**{field: hospital})
