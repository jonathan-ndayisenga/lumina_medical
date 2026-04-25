from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path(
        'accounts/logout/',
        auth_views.LogoutView.as_view(next_page='login'),
        name='logout',
    ),
    path('', include('accounts.urls')),  # Root: login page (at /)
    path('platform/', include('admin_dashboard.urls')),  # superadmin & hospital admin
    path('reception/', include('reception.urls')),
    path('doctor/', include('doctor.urls')),
    path('nurse/', include('nurse.urls')),
    path('accounts/', include('django.contrib.auth.urls')),
    path('lab/', include('lab.urls')),  # Lab moved to /lab/ prefix
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
