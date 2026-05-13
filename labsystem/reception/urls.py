from django.urls import path

from . import views

urlpatterns = [
    path("", views.reception_dashboard, name="reception_dashboard"),
    path("queue/", views.receptionist_queue, name="reception_queue"),
    path("queue/<int:queue_entry_id>/finish/", views.receptionist_queue_finish, name="reception_queue_finish"),
    path("queue/<int:queue_entry_id>/approve-lab/", views.receptionist_queue_approve_lab, name="reception_queue_approve_lab"),
    path("queue/<int:queue_entry_id>/bill/", views.receptionist_queue_bill, name="reception_queue_bill"),
    path("queue/<int:queue_entry_id>/send-to-doctor/", views.receptionist_queue_send_to_doctor, name="reception_queue_send_to_doctor"),
    path("dispense/start/", views.quick_dispense_start, name="quick_dispense_start"),
    path("patients/", views.patient_list, name="patient_list"),
    path("patients/new/", views.patient_create, name="patient_create"),
    path("patients/<int:patient_id>/edit/", views.patient_edit, name="patient_edit"),
    path("patients/<int:patient_id>/delete/", views.patient_delete, name="patient_delete"),
    path("patients/<int:patient_id>/visits/", views.patient_visits, name="patient_visits"),
    path("patients/<int:patient_id>/visit/new/", views.visit_create, name="visit_create"),
    path("visits/<int:visit_id>/edit/", views.visit_edit, name="visit_edit"),
    path("visits/<int:visit_id>/delete/", views.visit_delete, name="visit_delete"),
    path("visits/<int:visit_id>/terminate/", views.visit_terminate, name="visit_terminate"),
    path("visits/<int:visit_id>/report/", views.view_visit_report, name="view_visit_report"),
    path("complete/<int:visit_id>/", views.complete_visit, name="complete_visit"),
    path("complete/<int:visit_id>/prescriptions/<int:prescription_id>/dispense/", views.dispense_prescription, name="reception_dispense_prescription"),
    path("receipt/<int:visit_id>/", views.print_receipt, name="print_receipt"),
    path("receipt/payment/<int:payment_id>/", views.print_payment_receipt, name="print_payment_receipt"),
]
