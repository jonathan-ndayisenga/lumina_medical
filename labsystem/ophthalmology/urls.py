from django.urls import path

from . import views

urlpatterns = [
    path("visit/<int:visit_id>/base-refraction/", views.base_refraction, name="eye_base_refraction"),
]
