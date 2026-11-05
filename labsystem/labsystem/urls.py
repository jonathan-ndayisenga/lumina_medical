from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.generic.base import RedirectView

from accounts.login_throttle import ThrottledAdminAuthenticationForm
from labsystem.media_views import serve_media

# /admin/ gets the same brute-force lockout as the app's own sign-in pages.
admin.site.login_form = ThrottledAdminAuthenticationForm

urlpatterns = [
    path(
        'accounts/login/',
        RedirectView.as_view(pattern_name='login', permanent=False),
    ),
    path('admin/', admin.site.urls),
    path(
        'accounts/logout/',
        auth_views.LogoutView.as_view(),
        name='logout',
    ),
    path('', include('accounts.urls')),  # Root: login page (at /)
    path('books/', include('books.urls')),  # Ternah Books: its own login, its own shell
    path('platform/', include('admin_dashboard.urls')),  # superadmin & hospital admin
    path('reception/', include('reception.urls')),
    path('doctor/', include('doctor.urls')),
    path('nurse/', include('nurse.urls')),
    path('eye/', include('ophthalmology.urls')),
    path('lab/', include('lab.urls')),  # Lab moved to /lab/ prefix
    path('finance/', include('finance.urls')),
    path('homecare/', include('homecare.urls')),
    re_path(r'^media/(?P<path>.*)$', serve_media),
]
