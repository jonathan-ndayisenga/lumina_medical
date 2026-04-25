from django import forms
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.models import Hospital, User
from reception.models import Service

from .models import (
    BankAccount,
    BankTransaction,
    CashDrawer,
    CashTransaction,
    Expense,
    InventoryItem,
    MobileMoneyAccount,
    MobileMoneyTransaction,
    ReconciliationStatement,
    Salary,
)

UserModel = get_user_model()


class HospitalForm(forms.ModelForm):
    admin_username = forms.CharField(max_length=150, required=True)
    admin_password = forms.CharField(widget=forms.PasswordInput, required=True)
    admin_password_confirm = forms.CharField(widget=forms.PasswordInput, required=True)

    class Meta:
        model = Hospital
        fields = (
            "name",
            "subdomain",
            "location",
            "box_number",
            "phone_number",
            "email",
            "logo",
            "subscription_plan",
        )
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Hospital name", "class": "form-control"}),
            "subdomain": forms.TextInput(attrs={"placeholder": "hospital-code", "class": "form-control"}),
            "location": forms.TextInput(attrs={"placeholder": "Hospital location", "class": "form-control"}),
            "box_number": forms.TextInput(attrs={"placeholder": "PO Box", "class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"placeholder": "+256...", "class": "form-control"}),
            "email": forms.EmailInput(attrs={"placeholder": "hospital@example.com", "class": "form-control"}),
            "logo": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "subscription_plan": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, require_admin_credentials=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_admin_credentials = require_admin_credentials
        for field_name in ("location", "box_number", "phone_number", "email"):
            self.fields[field_name].required = True
        if not require_admin_credentials:
            for field_name in ("admin_username", "admin_password", "admin_password_confirm"):
                self.fields.pop(field_name, None)
            return
        for field_name in ("admin_username", "admin_password", "admin_password_confirm"):
            self.fields[field_name].required = True
            self.fields[field_name].widget.attrs.setdefault("class", "form-control")
        self.fields["logo"].help_text = "Optional. PNG or JPG works well."

    def clean_subdomain(self):
        subdomain = self.cleaned_data.get("subdomain", "").lower().strip()
        if not subdomain:
            raise forms.ValidationError("Subdomain is required.")
        if " " in subdomain:
            raise forms.ValidationError("Subdomain cannot contain spaces. Use hyphens instead.")
        existing = Hospital.objects.filter(subdomain=subdomain)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("This subdomain is already in use.")
        return subdomain

    def clean_admin_username(self):
        username = self.cleaned_data.get("admin_username", "").strip()
        if not self.require_admin_credentials and not username:
            return username
        if not username:
            raise forms.ValidationError("Admin username is required.")
        if UserModel.objects.filter(username=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("admin_password")
        confirm = cleaned_data.get("admin_password_confirm")

        if self.require_admin_credentials:
            if password and confirm and password != confirm:
                raise forms.ValidationError("Passwords do not match.")
            if password:
                try:
                    validate_password(password)
                except ValidationError as exc:
                    self.add_error("admin_password", exc)
            elif not self.errors.get("admin_password"):
                self.add_error("admin_password", "Admin password is required.")
            if not confirm and not self.errors.get("admin_password_confirm"):
                self.add_error("admin_password_confirm", "Please confirm the password.")

        return cleaned_data


class HospitalStaffUserForm(UserCreationForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Optional: assign this user to multiple modules (Reception, Lab, Doctor, Nurse).",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active", "groups")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.filter(name__in=["Reception", "Lab", "Doctor", "Nurse"]).order_by("name")
        self.fields["role"].choices = [
            (User.ROLE_HOSPITAL_ADMIN, "Hospital Admin"),
            (User.ROLE_RECEPTIONIST, "Receptionist"),
            (User.ROLE_LAB_ATTENDANT, "Lab Attendant"),
            (User.ROLE_DOCTOR, "Doctor"),
            (User.ROLE_NURSE, "Nurse"),
        ]
        for name, field in self.fields.items():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")


class HospitalStaffUserUpdateForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Optional: assign this user to multiple modules (Reception, Lab, Doctor, Nurse).",
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active", "groups")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = Group.objects.filter(name__in=["Reception", "Lab", "Doctor", "Nurse"]).order_by("name")
        self.fields["role"].choices = [
            (User.ROLE_HOSPITAL_ADMIN, "Hospital Admin"),
            (User.ROLE_RECEPTIONIST, "Receptionist"),
            (User.ROLE_LAB_ATTENDANT, "Lab Attendant"),
            (User.ROLE_DOCTOR, "Doctor"),
            (User.ROLE_NURSE, "Nurse"),
        ]
        for field in self.fields.values():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")


class HospitalServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ("name", "category", "price", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = (
            "description",
            "category",
            "amount",
            "source",
            "bank_account",
            "mobile_money_account",
            "cash_drawer",
            "notes",
        )

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hospital = hospital
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["notes"].widget = forms.Textarea(attrs={"class": "form-control", "rows": 3})
        if hospital is not None:
            self.fields["bank_account"].queryset = BankAccount.objects.filter(hospital=hospital, is_active=True)
            self.fields["mobile_money_account"].queryset = MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True)
            self.fields["cash_drawer"].queryset = CashDrawer.objects.filter(hospital=hospital).order_by("-date", "-id")
        else:
            self.fields["bank_account"].queryset = BankAccount.objects.none()
            self.fields["mobile_money_account"].queryset = MobileMoneyAccount.objects.none()
            self.fields["cash_drawer"].queryset = CashDrawer.objects.none()
        self.fields["bank_account"].required = False
        self.fields["mobile_money_account"].required = False
        self.fields["cash_drawer"].required = False
        self.fields["bank_account"].help_text = "Pick the exact bank account used."
        self.fields["mobile_money_account"].help_text = "Pick the exact mobile money number used."
        self.fields["cash_drawer"].help_text = "Pick the cash drawer used for payout."

    def clean(self):
        cleaned_data = super().clean()
        source = cleaned_data.get("source")
        bank_account = cleaned_data.get("bank_account")
        mobile_money_account = cleaned_data.get("mobile_money_account")
        cash_drawer = cleaned_data.get("cash_drawer")

        if source == Expense.SOURCE_BANK_ACCOUNT and not bank_account:
            self.add_error("bank_account", "Select the bank account used.")
        if source == Expense.SOURCE_MOBILE_MONEY and not mobile_money_account:
            self.add_error("mobile_money_account", "Select the mobile money account used.")
        # Cash drawer is optional; if omitted we will auto-assign the daily cash statement row on save.
        if source == Expense.SOURCE_CASH_DRAWER and not cash_drawer:
            cleaned_data["cash_drawer"] = None

        if source != Expense.SOURCE_BANK_ACCOUNT:
            cleaned_data["bank_account"] = None
        if source != Expense.SOURCE_MOBILE_MONEY:
            cleaned_data["mobile_money_account"] = None
        if source != Expense.SOURCE_CASH_DRAWER:
            cleaned_data["cash_drawer"] = None

        return cleaned_data



class SalaryForm(forms.ModelForm):
    class Meta:
        model = Salary
        fields = ("employee", "month", "amount", "paid", "notes")
        widgets = {
            "month": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hospital = hospital
        self.fields["employee"].queryset = User.objects.filter(hospital=hospital) if hospital else User.objects.none()
        for name, field in self.fields.items():
            if name == "month":
                continue
            field.widget.attrs.setdefault("class", "form-control")


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ("name", "quantity", "unit_price", "low_stock_threshold")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ("bank_name", "account_name", "account_number", "opening_balance", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class MobileMoneyAccountForm(forms.ModelForm):
    class Meta:
        model = MobileMoneyAccount
        fields = ("provider", "number", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class MobileMoneyTransactionForm(forms.ModelForm):
    class Meta:
        model = MobileMoneyTransaction
        fields = ("transaction_date", "description", "amount", "transaction_type", "reference", "reconciled_with")
        widgets = {
            "transaction_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hospital = hospital
        self.fields["transaction_date"].initial = self.initial.get("transaction_date") or timezone.localdate()
        from reception.models import Payment

        payment_queryset = Payment.objects.none()
        if hospital is not None:
            payment_queryset = (
                Payment.objects.filter(
                    visit__hospital=hospital,
                    mode=Payment.MODE_MOBILE_MONEY,
                )
                .select_related("visit__patient")
                .order_by("-paid_at", "-id")
            )
        self.fields["reconciled_with"].queryset = payment_queryset
        self.fields["reconciled_with"].required = False
        self.fields["reconciled_with"].label_from_instance = (
            lambda payment: f"{payment.visit.patient.name} - {payment.amount_paid} ({payment.get_mode_display()})"
        )
        for name, field in self.fields.items():
            if name == "transaction_date":
                continue
            field.widget.attrs.setdefault("class", "form-control")


class BankTransactionForm(forms.ModelForm):
    class Meta:
        model = BankTransaction
        fields = ("transaction_date", "description", "amount", "transaction_type", "reference", "reconciled_with")
        widgets = {
            "transaction_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hospital = hospital
        self.fields["transaction_date"].initial = self.initial.get("transaction_date") or timezone.localdate()
        from reception.models import Payment

        payment_queryset = Payment.objects.none()
        if hospital is not None:
            payment_queryset = Payment.objects.filter(visit__hospital=hospital).select_related("visit__patient")
        self.fields["reconciled_with"].queryset = payment_queryset
        self.fields["reconciled_with"].required = False
        self.fields["reconciled_with"].label_from_instance = (
            lambda payment: f"{payment.visit.patient.name} - {payment.amount_paid} ({payment.get_mode_display()})"
        )
        for name, field in self.fields.items():
            if name == "transaction_date":
                continue
            field.widget.attrs.setdefault("class", "form-control")


class OpenCashDrawerForm(forms.Form):
    opening_balance = forms.DecimalField(decimal_places=2, max_digits=12, min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["opening_balance"].widget.attrs.setdefault("class", "form-control")


class CashTransactionForm(forms.ModelForm):
    class Meta:
        model = CashTransaction
        fields = ("amount", "transaction_type", "description")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class CloseCashDrawerForm(forms.Form):
    closing_balance = forms.DecimalField(decimal_places=2, max_digits=12, min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["closing_balance"].widget.attrs.setdefault("class", "form-control")


class BankReconciliationForm(forms.Form):
    bank_account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bank_account"].queryset = BankAccount.objects.filter(hospital=hospital, is_active=True) if hospital else BankAccount.objects.none()
        self.fields["bank_account"].widget.attrs.setdefault("class", "form-control")
        today = timezone.localdate()
        self.fields["period_end"].initial = today
        self.fields["period_start"].initial = today.replace(day=1)


class MobileMoneyStatementForm(forms.Form):
    mobile_money_account = forms.ModelChoiceField(queryset=MobileMoneyAccount.objects.none(), label="Mobile Money Account")
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mobile_money_account"].queryset = (
            MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True) if hospital else MobileMoneyAccount.objects.none()
        )
        self.fields["mobile_money_account"].widget.attrs.setdefault("class", "form-control")
        today = timezone.localdate()
        self.fields["period_end"].initial = today
        self.fields["period_start"].initial = today.replace(day=1)


class ThreeWayReconciliationForm(forms.Form):
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    period_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["period_end"].initial = today
        self.fields["period_start"].initial = today.replace(day=1)
