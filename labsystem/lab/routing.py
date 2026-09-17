"""
Single source of truth for "where does this hospital's Lab Queue link go" —
used by both accounts.NAV_SECTIONS (the home tile) and the sidebar link in
templates/base.html, so the two can never disagree.

The prior template-based engine's own queue/report-authoring screens were
retired once every hospital moved onto the rework; this is now a plain
constant rather than a per-hospital branch, kept as a function so callers
don't need to change if a genuine second engine ever returns.
"""

from django.urls import reverse


def lab_queue_url(hospital) -> str:
    return reverse("queue")
