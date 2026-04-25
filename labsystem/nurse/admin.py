from django.contrib import admin

from .models import NurseNote


@admin.register(NurseNote)
class NurseNoteAdmin(admin.ModelAdmin):
    list_display = ("visit", "created_by", "created_at")
    search_fields = ("visit__patient__name", "notes", "created_by__username")
    list_filter = ("created_at", "visit__hospital")
