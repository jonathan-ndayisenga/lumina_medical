from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.urls import include, path, re_path
from django.views.generic.base import RedirectView
from django.views.static import serve

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
    path('platform/', include('admin_dashboard.urls')),  # superadmin & hospital admin
    path('reception/', include('reception.urls')),
    path('doctor/', include('doctor.urls')),
    path('nurse/', include('nurse.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('lab/', include('lab.urls')),  # Lab moved to /lab/ prefix
    path('finance/', include('finance.urls')),
    path('homecare/', include('homecare.urls')),
    # Serve uploaded media files (hospital logos etc.) in all environments
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
