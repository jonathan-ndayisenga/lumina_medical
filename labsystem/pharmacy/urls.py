from django.urls import path

from . import views

urlpatterns = [
    path("", views.queue, name="pharmacy_queue"),
    path("mode/", views.set_mode, name="pharmacy_set_mode"),
    path("walk-in/", views.walk_in, name="pharmacy_walk_in"),
    path("visit/<int:visit_id>/", views.visit, name="pharmacy_visit"),
]
