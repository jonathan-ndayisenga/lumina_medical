from decimal import Decimal

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
        for field_name in ("location", "box_number", "phone_number"):
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
    PACK_TYPE_CHOICES = [
        ("tablet", "Tablet"),
        ("capsule", "Capsule"),
        ("bottle", "Bottle"),
        ("strip", "Strip"),
        ("tube", "Tube"),
        ("bag", "Bag"),
        ("vial", "Vial"),
        ("ampoule", "Ampoule"),
        ("box", "Box"),
        ("unit", "Unit"),
    ]
    BASE_UNIT_CHOICES = [
        ("tablet", "Tablet"),
        ("capsule", "Capsule"),
        ("ml", "ml"),
        ("g", "g"),
        ("vial", "Vial"),
        ("ampoule", "Ampoule"),
        ("pair", "Pair"),
        ("piece", "Piece"),
        ("unit", "Unit"),
    ]

    unit = forms.ChoiceField(
        choices=PACK_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Pack Type",
    )
    base_unit = forms.ChoiceField(
        choices=BASE_UNIT_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Base Unit",
    )
    opening_batch_number = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="Opening Batch Number",
    )
    opening_expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        label="Opening Expiry Date",
    )

    class Meta:
        model = InventoryItem
        fields = (
            "name",
            "category",
            "unit",
            "base_unit",
            "units_per_pack",
            "strength_mg_per_unit",
            "current_quantity",
            "unit_cost",
            "selling_price",
            "reorder_level",
            "concentration_mg_per_ml",
            "pack_size_ml",
            "days_covered_per_pack",
            "is_active",
        )
        widgets = {
            "current_quantity": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "unit_cost": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "selling_price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "units_per_pack": forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "class": "form-control"}),
            "strength_mg_per_unit": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "reorder_level": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "concentration_mg_per_ml": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "pack_size_ml": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
            "days_covered_per_pack": forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["name"].label = "Drug / Item Name"
        self.fields["category"].label = "Form / Category"
        self.fields["unit"].label = "Pack Type"
        self.fields["base_unit"].label = "Base Unit"
        self.fields["units_per_pack"].label = "Units per Pack"
        self.fields["current_quantity"].label = "Opening Stock"
        self.fields["unit_cost"].label = "Buying Price (Per Pack / Unit)"
        self.fields["selling_price"].label = "Selling Price (Per Pack / Unit)"
        self.fields["reorder_level"].label = "Minimum Stock Level"
        self.fields["strength_mg_per_unit"].label = "Strength (mg per base unit)"
        self.fields["days_covered_per_pack"].label = "Days Covered Per Pack"
        self.fields["opening_batch_number"].help_text = "Use the supplier/manufacturer batch code so reports can show each lot clearly."
        self.fields["opening_expiry_date"].help_text = "Add the first expiry date if this item is already in stock."
        self.fields["base_unit"].help_text = "Smallest clinical unit used for prescribing and reporting, e.g. tablet, ml, g, vial."
        self.fields["unit"].help_text = "How the stocked item is packaged for sale or storage, e.g. bottle, strip, tube, vial."
        self.fields["units_per_pack"].help_text = "How many base units are inside one stocked pack."
        self.fields["current_quantity"].help_text = "Enter how many packs or units are physically in stock right now."
        self.fields["strength_mg_per_unit"].help_text = "Useful for tablets/capsules so the doctor can prescribe by mg accurately."
        self.fields["days_covered_per_pack"].help_text = "Useful for creams/tubes where one tube covers a number of treatment days."

    def clean(self):
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        units_per_pack = cleaned_data.get("units_per_pack")
        base_unit = (cleaned_data.get("base_unit") or "").strip().lower()
        pack_type = (cleaned_data.get("unit") or "").strip().lower()

        if units_per_pack is not None and units_per_pack <= 0:
            self.add_error("units_per_pack", "Units per pack must be greater than zero.")

        if category in {InventoryItem.CATEGORY_SYRUP, InventoryItem.CATEGORY_IV, InventoryItem.CATEGORY_IM}:
            if not units_per_pack:
                self.add_error("units_per_pack", "This medicine form needs the pack volume in ml.")
            if not base_unit:
                cleaned_data["base_unit"] = "ml"
            if not pack_type or pack_type == "unit":
                if category == InventoryItem.CATEGORY_IM:
                    cleaned_data["unit"] = "vial"
                elif category == InventoryItem.CATEGORY_IV:
                    cleaned_data["unit"] = "bag"
                else:
                    cleaned_data["unit"] = "bottle"

        if category == InventoryItem.CATEGORY_SYRUP and (not pack_type or pack_type == "unit"):
                cleaned_data["unit"] = "bottle"

        if category == InventoryItem.CATEGORY_TUBE:
            if not pack_type or pack_type == "unit":
                cleaned_data["unit"] = "tube"
            if not base_unit:
                cleaned_data["base_unit"] = "g"

        if category == InventoryItem.CATEGORY_DRUG and not base_unit:
            cleaned_data["base_unit"] = "tablet"

        opening_stock = cleaned_data.get("current_quantity") or Decimal("0")
        batch_number = (cleaned_data.get("opening_batch_number") or "").strip()
        expiry_date = cleaned_data.get("opening_expiry_date")
        if opening_stock > 0 and not batch_number:
            self.add_error("opening_batch_number", "Add a batch number so the printable stock report can show this stock clearly.")
        if opening_stock > 0 and not expiry_date:
            self.add_error("opening_expiry_date", "Add an expiry date for the opening stock batch.")

        return cleaned_data


class InventoryRestockForm(forms.Form):
    quantity_received = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "class": "form-control"}),
        label="Quantity Received",
        help_text="How many packs or units you are adding to stock now.",
    )
    unit_cost = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "class": "form-control"}),
        label="Buying Price (optional)",
        help_text="Leave blank to keep the current buying price.",
    )
    batch_number = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="Batch Number",
        help_text="Each restock should be tied to a batch so the stock sheet shows what is on shelf.",
    )
    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        label="Expiry Date",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
        label="Restock Notes",
    )


class InventoryBulkUploadForm(forms.Form):
    file = forms.FileField(
        label="Inventory CSV File",
        help_text="Download the template, fill it in with Excel or Google Sheets, then upload it here as CSV.",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".csv,text/csv"}),
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        filename = (upload.name or "").lower()
        if not filename.endswith(".csv"):
            raise forms.ValidationError("Please upload the inventory file as a CSV.")
        return upload


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
