from django.urls import path
from . import views
from . import views_catalog

urlpatterns = [
    path('', views.report_list, name='report_list'),

    # Lab Management — shared catalog screens.
    path('manage/categories/', views_catalog.category_list, name='lab_category_list'),
    path('manage/categories/new/', views_catalog.category_create, name='lab_category_create'),
    path('manage/categories/<int:pk>/edit/', views_catalog.category_edit, name='lab_category_edit'),
    path('manage/specimens/', views_catalog.specimen_list, name='lab_specimen_list'),
    path('manage/specimens/new/', views_catalog.specimen_create, name='lab_specimen_create'),
    path('manage/specimens/<int:pk>/edit/', views_catalog.specimen_edit, name='lab_specimen_edit'),
    path('manage/specimens/<int:pk>/delete/', views_catalog.specimen_delete, name='lab_specimen_delete'),
    path('manage/result-types/', views_catalog.result_type_list, name='lab_result_type_list'),
    path('manage/tests/', views_catalog.test_list, name='lab_test_list'),
    path('manage/tests/new/', views_catalog.test_create, name='lab_test_create'),
    path('manage/tests/clone/', views_catalog.test_clone_picker, name='lab_test_clone_picker'),
    path('manage/tests/clone/<int:pk>/', views_catalog.test_clone, name='lab_test_clone'),
    path('manage/tests/<int:pk>/edit/', views_catalog.test_edit, name='lab_test_edit'),

    path('queue/', views.queue, name='queue'),
    path('reviewing-results/', views.reviewing_results, name='reviewing_results'),
    path('orders/<int:order_id>/sample/', views.pick_sample, name='pick_sample'),
    path('orders/<int:order_id>/enter/', views.enter_result, name='enter_result'),
    path('phlebotomy/queue/', views.phlebotomy_queue, name='phlebotomy_queue'),
    path('phlebotomy/<int:queue_entry_id>/intake/', views.phlebotomy_intake, name='phlebotomy_intake'),
    path('visits/<int:visit_id>/report/', views.visit_report, name='visit_report'),

    path('<int:pk>/', views.report_detail, name='report_detail'),
    path('<int:pk>/print/', views.report_print, name='report_print'),
    path('<int:pk>/delete/', views.report_delete, name='report_delete'),
    path('<int:report_id>/send-to-doctor/', views.send_lab_result_to_doctor, name='send_lab_result_to_doctor'),
    path('<int:report_id>/route/', views.route_lab_report, name='route_lab_report'),
    path('patient/<int:report_id>/', views.patient_reports, name='patient_reports'),
    path('patient-next/<int:patient_id>/', views.patient_reports_next, name='patient_reports_next'),
    path('settings/', views.lab_settings, name='lab_settings'),
]
