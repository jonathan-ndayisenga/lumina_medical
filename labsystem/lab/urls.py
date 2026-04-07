from django.urls import path
from . import views

urlpatterns = [
    path('', views.report_list, name='report_list'),
    path('new/', views.report_create, name='report_create'),
    path('templates/', views.template_library, name='template_library'),
    path('<int:pk>/', views.report_detail, name='report_detail'),
    path('<int:pk>/edit/', views.report_edit, name='report_edit'),
    path('<int:pk>/print/', views.report_print, name='report_print'),
    path('<int:pk>/delete/', views.report_delete, name='report_delete'),
    path('api/default-range/', views.default_range, name='default_range'),
]
