from django.conf import settings
from django.http import Http404
from django.views.static import serve

from books.permissions import books_staff_required

# Logos appear on the sign-in page and on printouts, so they stay open.
PUBLIC_PREFIXES = ("hospital_logos/", "organization_logos/")
BOOKS_PREFIX = "books/"


def _serve(request, path):
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    # An uploaded file must never be able to run as a page of this site.
    response["Content-Security-Policy"] = "sandbox"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@books_staff_required
def _serve_books_file(request, path):
    response = _serve(request, path)
    response["Cache-Control"] = "private, no-store"
    return response


def serve_media(request, path):
    """Uploads were served to anyone holding the address. Now each folder says
    who may read it, and anything not listed here isn't served at all."""
    if ".." in path.split("/"):
        raise Http404
    if path.startswith(PUBLIC_PREFIXES):
        return _serve(request, path)
    if path.startswith(BOOKS_PREFIX):
        return _serve_books_file(request, path)
    raise Http404
