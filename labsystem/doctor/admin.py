from django.contrib import admin

from .models import Consultation, LabRequest, Notification


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ("visit", "created_by", "follow_up_date", "created_at")
    list_filter = ("visit__hospital", "follow_up_date")
    search_fields = ("visit__patient__name", "diagnosis", "treatment")


@admin.register(LabRequest)
class LabRequestAdmin(admin.ModelAdmin):
    list_display = ("visit", "requested_by_role", "urgency", "status", "created_at")
    list_filter = ("requested_by_role", "urgency", "status", "visit__hospital")
    search_fields = ("visit__patient__name", "tests_requested", "clinical_notes")
    readonly_fields = ("visit", "requested_by", "created_at")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "notification_type", "title", "is_read", "created_at")
    list_filter = ("notification_type", "is_read", "created_at")
    search_fields = ("user__username", "title", "message")
    readonly_fields = ("user", "created_at")
