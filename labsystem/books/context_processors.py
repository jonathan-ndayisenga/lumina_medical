from .permissions import _is_books_admin


def books_admin(request):
    if not request.path.startswith("/books/"):
        return {}
    return {"is_books_admin": _is_books_admin(request.user)}
