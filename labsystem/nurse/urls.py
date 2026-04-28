from django.urls import path

from . import views

urlpatterns = [
    path("", views.nurse_queue, name="nurse_queue"),
    path("queue/<int:queue_entry_id>/care/", views.perform_nursing, name="perform_nursing"),
    path(
        "queue/<int:queue_entry_id>/prescriptions/<int:prescription_id>/dispense/",
        views.dispense_prescription,
        name="dispense_prescription",
    ),
]
