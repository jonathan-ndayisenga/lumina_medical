from django.contrib import admin

from .models import Patient, Payment, QueueEntry, Service, Visit, VisitService


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("name", "hospital", "age", "sex", "contact", "created_at")
    list_filter = ("hospital", "sex")
    search_fields = ("name", "contact")


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("patient", "hospital", "status", "total_amount", "visit_date", "created_by")
    list_filter = ("hospital", "status")
    search_fields = ("patient__name", "hospital__name")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "hospital", "category", "price", "is_active")
    list_filter = ("hospital", "category", "is_active")
    search_fields = ("name", "hospital__name")


@admin.register(VisitService)
class VisitServiceAdmin(admin.ModelAdmin):
    list_display = ("visit", "service", "price_at_time", "created_at")
    list_filter = ("service__hospital", "service__category")
    search_fields = ("visit__patient__name", "service__name")


@admin.register(QueueEntry)
class QueueEntryAdmin(admin.ModelAdmin):
    list_display = ("visit", "hospital", "queue_type", "processed", "created_at", "processed_at")
    list_filter = ("hospital", "queue_type", "processed")
    search_fields = ("visit__patient__name",)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("visit", "amount", "mode", "status", "paid_at", "recorded_by")
    list_filter = ("status", "mode", "visit__hospital")
    search_fields = ("visit__patient__name",)

