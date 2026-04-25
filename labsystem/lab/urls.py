from django.urls import path
from . import views

urlpatterns = [
    path('', views.report_list, name='report_list'),
    path('queue/', views.lab_queue, name='lab_queue'),
    path('new/', views.report_create, name='report_create'),
    path('request/<int:lab_request_id>/start/', views.report_create_from_lab_request, name='report_create_from_lab_request'),
    path('queue/<int:queue_entry_id>/start/', views.perform_lab_test, name='perform_lab_test'),
    path('templates/', views.template_library, name='template_library'),
    path('<int:pk>/', views.report_detail, name='report_detail'),
    path('<int:pk>/edit/', views.report_edit, name='report_edit'),
    path('<int:pk>/print/', views.report_print, name='report_print'),
    path('<int:pk>/delete/', views.report_delete, name='report_delete'),
    path('<int:report_id>/send-to-doctor/', views.send_lab_result_to_doctor, name='send_lab_result_to_doctor'),
    path('api/default-range/', views.default_range, name='default_range'),
]
