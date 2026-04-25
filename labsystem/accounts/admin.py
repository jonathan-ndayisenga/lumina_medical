from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import LuminaUserChangeForm, LuminaUserCreationForm
from .models import AuditLog, Hospital, HospitalSubscriptionPayment, SubscriptionPlan, User


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price_monthly", "price_yearly", "max_users", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ("name", "subdomain", "subscription_plan", "is_active", "subscription_end_date")
    list_filter = ("is_active", "subscription_plan")
    search_fields = ("name", "subdomain")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = LuminaUserCreationForm
    form = LuminaUserChangeForm
    model = User
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "hospital",
        "is_active",
        "is_staff",
    )
    list_filter = ("role", "hospital", "is_active", "is_staff", "is_superuser")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Lumina Access", {"fields": ("role", "hospital")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Lumina Access", {"fields": ("email", "first_name", "last_name", "role", "hospital")}),
    )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "action", "model_name", "object_id", "user", "hospital")
    list_filter = ("hospital", "action", "model_name")
    search_fields = ("action", "model_name", "object_id", "user__username", "hospital__name")
    readonly_fields = ("timestamp",)


@admin.register(HospitalSubscriptionPayment)
class HospitalSubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("hospital", "amount", "period_start", "period_end", "paid_at")
    list_filter = ("hospital",)
    search_fields = ("hospital__name",)
    readonly_fields = ("paid_at",)

