from django.contrib import admin

from .models import Account, JournalEntry, JournalLine


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ("account", "debit", "credit", "description")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "sub_type", "hospital", "is_system", "is_active")
    list_filter = ("account_type", "is_system", "is_active", "hospital")
    search_fields = ("code", "name")
    ordering = ("hospital", "code")


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("reference", "date", "description", "source_type", "hospital", "is_reversal", "created_at")
    list_filter = ("source_type", "is_reversal", "hospital")
    search_fields = ("reference", "description")
    readonly_fields = ("reference", "created_at")
    inlines = [JournalLineInline]
