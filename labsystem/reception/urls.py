from django.urls import path

from . import views

urlpatterns = [
    path("", views.reception_dashboard, name="reception_dashboard"),
    path("patients/", views.patient_list, name="patient_list"),
    path("patients/new/", views.patient_create, name="patient_create"),
    path("patients/<int:patient_id>/visits/", views.patient_visits, name="patient_visits"),
    path("patients/<int:patient_id>/visit/new/", views.visit_create, name="visit_create"),
    path("visits/<int:visit_id>/edit/", views.visit_edit, name="visit_edit"),
    path("visits/<int:visit_id>/delete/", views.visit_delete, name="visit_delete"),
    path("visits/<int:visit_id>/report/", views.view_visit_report, name="view_visit_report"),
    path("complete/<int:visit_id>/", views.complete_visit, name="complete_visit"),
    path("complete/<int:visit_id>/prescriptions/<int:prescription_id>/dispense/", views.dispense_prescription, name="reception_dispense_prescription"),
    path("receipt/<int:visit_id>/", views.print_receipt, name="print_receipt"),
    path("receipt/payment/<int:payment_id>/", views.print_payment_receipt, name="print_payment_receipt"),
]
