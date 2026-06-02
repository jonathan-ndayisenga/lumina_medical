from django.urls import path

from . import views

urlpatterns = [
    path("", views.nurse_queue, name="nurse_queue"),
    path("queue/<int:queue_entry_id>/care/", views.perform_nursing, name="perform_nursing"),
    path("queue/<int:queue_entry_id>/prescriptions/<int:prescription_id>/dispense/", views.dispense_prescription, name="dispense_prescription"),
    path("nursing-care/", views.nursing_admissions, name="nursing_admissions"),
    path("nursing-care/start/<int:visit_id>/", views.start_nursing_admission, name="start_nursing_admission"),
    path("nursing-care/<int:admission_id>/", views.nursing_admission_detail, name="nursing_admission_detail"),
    path("nursing-care/<int:admission_id>/discharge/", views.discharge_nursing, name="discharge_nursing"),
    path("nursing-care/dose/<int:care_item_id>/administer/", views.administer_dose, name="administer_dose"),
    path("nursing-care/dose/<int:care_item_id>/stop/", views.stop_care_item, name="stop_care_item"),
]
