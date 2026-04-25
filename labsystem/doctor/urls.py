from django.urls import path

from . import views

urlpatterns = [
    path("", views.doctor_queue, name="doctor_queue"),
    path("visit/<int:visit_id>/consultation/", views.consultation, name="consultation"),
    path("visit/<int:visit_id>/consultation/detail/", views.consultation_detail, name="consultation_detail"),
    path("lab-requests/", views.doctor_lab_requests, name="doctor_lab_requests"),
    path("lab-requests/create/", views.create_lab_request, name="create_lab_request"),
    path("lab-requests/<int:lab_request_id>/", views.view_lab_request, name="view_lab_request"),
    path("api/services/lab/", views.lab_services_api, name="lab_services_api"),
    path("api/add-lab-service/", views.add_lab_service_api, name="add_lab_service_api"),
]
