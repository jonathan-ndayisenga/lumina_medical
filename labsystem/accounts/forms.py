from django import forms
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import Hospital, HospitalSubscriptionPayment, SubscriptionPlan, User


class LuminaUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name", "role", "hospital")


class LuminaUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "role",
            "hospital",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )


# Superadmin Forms


class SubscriptionPlanForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = ("name", "price_monthly", "price_yearly", "max_users", "max_storage_mb", "description", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g., Professional Plan"}),
            "price_monthly": forms.NumberInput(attrs={"placeholder": "0.00", "step": "0.01"}),
            "price_yearly": forms.NumberInput(attrs={"placeholder": "0.00", "step": "0.01"}),
            "max_users": forms.NumberInput(attrs={"placeholder": "10"}),
            "max_storage_mb": forms.NumberInput(attrs={"placeholder": "100"}),
            "description": forms.Textarea(attrs={"placeholder": "Plan features and details", "rows": 4}),
            "is_active": forms.CheckboxInput(),
        }


class HospitalSubscriptionPaymentForm(forms.ModelForm):
    class Meta:
        model = HospitalSubscriptionPayment
        fields = ("hospital", "amount", "period_start", "period_end", "notes")
        widgets = {
            "hospital": forms.Select(),
            "amount": forms.NumberInput(attrs={"placeholder": "0.00", "step": "0.01"}),
            "period_start": forms.DateInput(attrs={"type": "date"}),
            "period_end": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"placeholder": "Payment notes", "rows": 3}),
        }
