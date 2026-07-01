from django.urls import path
from . import views

urlpatterns = [
    path("", views.homecare_dashboard, name="homecare_dashboard"),
    path("nurses/", views.nurse_list, name="homecare_nurse_list"),
    path("nurses/register/", views.register_nurse, name="homecare_register_nurse"),
    path("nurses/<int:nurse_id>/", views.nurse_detail, name="homecare_nurse_detail"),
    path("clients/", views.client_list, name="homecare_client_list"),
    path("clients/register/", views.register_client, name="homecare_register_client"),
    path("clients/<int:client_id>/", views.client_detail, name="homecare_client_detail"),
    path("placements/", views.placement_list, name="homecare_placement_list"),
    path("placements/create/", views.placement_create, name="homecare_placement_create"),
    path("placements/<int:placement_id>/", views.placement_detail, name="homecare_placement_detail"),
    path("placements/<int:placement_id>/receipt/", views.record_receipt, name="homecare_record_receipt"),
    path("contracts/", views.contract_list, name="homecare_contract_list"),
    path("contracts/<int:contract_id>/print/", views.contract_print, name="homecare_contract_print"),
    path("receipts/", views.receipt_list, name="homecare_receipt_list"),
    path("receipts/<int:receipt_id>/print/", views.receipt_print, name="homecare_receipt_print"),
]
