from decimal import Decimal

from django import forms
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.models import Hospital, HospitalModuleSubscription, Module, PlatformSettings, SupportToken, User
from lab.models import TestProfile
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
    subscription_months = forms.IntegerField(
        min_value=1,
        max_value=60,
        initial=1,
        required=True,
        label="Subscription Duration (months)",
        help_text="How many months the hospital has paid for. The subscription end date is set automatically from today.",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 1, 3, 6, 12"}),
    )
    modules = forms.ModelMultipleChoiceField(
        queryset=Module.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select which modules this hospital is subscribed to. Core modules (e.g. Reception) are always included.",
    )

    class Meta:
        model = Hospital
        fields = (
            "name",
            "tagline",
            "subdomain",
            "location",
            "city",
            "box_number",
            "phone_number",
            "email",
            "logo",
            "subscription_plan",
            "reactivation_alert_days",
        )
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Hospital name", "class": "form-control"}),
            "tagline": forms.TextInput(attrs={"placeholder": "e.g. Your health, our priority", "class": "form-control"}),
            "subdomain": forms.TextInput(attrs={"placeholder": "hospital-code", "class": "form-control"}),
            "location": forms.TextInput(attrs={"placeholder": "Street / road address", "class": "form-control"}),
            "city": forms.TextInput(attrs={"placeholder": "City e.g. Kampala", "class": "form-control"}),
            "box_number": forms.TextInput(attrs={"placeholder": "e.g. PO BOX: 200132", "class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"placeholder": "+256...", "class": "form-control"}),
            "email": forms.EmailInput(attrs={"placeholder": "hospital@example.com", "class": "form-control"}),
            "logo": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "subscription_plan": forms.Select(attrs={"class": "form-control"}),
            "reactivation_alert_days": forms.NumberInput(attrs={"class": "form-control", "min": "0", "max": "90", "placeholder": "e.g. 7"}),
        }

    def __init__(self, *args, require_admin_credentials=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_admin_credentials = require_admin_credentials
        for field_name in ("location", "box_number", "phone_number"):
            self.fields[field_name].required = True
        self.fields["reactivation_alert_days"].required = False
        self.fields["reactivation_alert_days"].initial = 7

        core_module_ids = list(Module.objects.filter(is_core=True).values_list("pk", flat=True))
        if self.instance.pk:
            self.fields["modules"].initial = list(
                Module.objects.filter(
                    hospital_subscriptions__hospital=self.instance,
                    hospital_subscriptions__is_active=True,
                ).values_list("pk", flat=True)
            ) or core_module_ids
        else:
            self.fields["modules"].initial = core_module_ids

        if not require_admin_credentials:
            for field_name in ("admin_username", "admin_password", "admin_password_confirm"):
                self.fields.pop(field_name, None)
            return
        for field_name in ("admin_username", "admin_password", "admin_password_confirm"):
            self.fields[field_name].required = True
            self.fields[field_name].widget.attrs.setdefault("class", "form-control")
        self.fields["logo"].help_text = "Optional. PNG or JPG works well."

    def save_subscription_end_date(self, hospital):
        """Set hospital.subscription_end_date from the submitted subscription_months.
        Uses 30 days per month — standard SaaS billing approximation."""
        from datetime import date, timedelta
        months = self.cleaned_data.get("subscription_months") or 1
        hospital.subscription_end_date = date.today() + timedelta(days=30 * months)
        hospital.is_active = True
        hospital.save(update_fields=["subscription_end_date", "is_active"])

    def save_module_subscriptions(self, hospital):
        """Sync HospitalModuleSubscription rows to match the selected modules.

        Core modules (Reception) are force-included EXCEPT for Home Care-only
        hospitals. A pure home-care business has no walk-in patients and doesn't
        need Reception.

        The carve-out is detected from NON-CORE selected modules only, because
        the template always submits Reception via a hidden input (is_core=True)
        regardless of user intent. We ignore core modules in this check.
        """
        selected = set(self.cleaned_data.get("modules") or [])

        # Determine intent from non-core selections only
        non_core_selected_codes = {m.code for m in selected if not m.is_core}
        clinical_non_core = {"doctor", "nurse", "lab", "inventory", "finance", "hospital_mgmt", "sonographer"}
        is_homecare_only = (
            "home_care" in non_core_selected_codes
            and not (non_core_selected_codes & clinical_non_core)
        )

        if is_homecare_only:
            # Remove core modules that were auto-submitted — homecare-only hospitals
            # should NOT have Reception or other core modules forced onto them.
            selected = {m for m in selected if not m.is_core}
        else:
            selected |= set(Module.objects.filter(is_core=True))

        selected_ids = {m.pk for m in selected}
        for module in selected:
            HospitalModuleSubscription.objects.update_or_create(
                hospital=hospital,
                module=module,
                defaults={"is_active": True},
            )
        HospitalModuleSubscription.objects.filter(hospital=hospital).exclude(
            module_id__in=selected_ids
        ).update(is_active=False)

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


class ModuleForm(forms.ModelForm):
    """Superadmin form for editing a Module's price and active state."""
    class Meta:
        model = Module
        fields = ("name", "monthly_price", "is_active", "display_order")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")


MODULE_CODE_TO_GROUP_NAME = {
    "reception": "Reception",
    "doctor": "Doctor",
    "nurse": "Nurse",
    "lab": "Lab",
    "inventory": "Inventory",
    "finance": "Finance",
    "sonographer": "Sonographer",
}


def _module_group_queryset(hospital):
    """Only offer groups for modules the hospital has actually subscribed to."""
    if not hospital:
        return Group.objects.filter(name__in=MODULE_CODE_TO_GROUP_NAME.values()).order_by("name")
    allowed_names = [
        MODULE_CODE_TO_GROUP_NAME[code]
        for code in hospital.active_module_codes
        if code in MODULE_CODE_TO_GROUP_NAME
    ]
    return Group.objects.filter(name__in=allowed_names).order_by("name")


class HospitalStaffUserForm(UserCreationForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Optional: assign this user to one or more modules this hospital has subscribed to.",
    )
    custom_role = forms.CharField(
        required=False,
        max_length=50,
        label="Custom role",
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. Physiotherapist, Dentist…",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active", "groups")

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = _module_group_queryset(hospital)
        self.fields["role"].choices = [
            ("", "— select a role —"),
            (User.ROLE_HOSPITAL_ADMIN, "Hospital Admin"),
            (User.ROLE_RECEPTIONIST, "Receptionist"),
            (User.ROLE_LAB_ATTENDANT, "Lab Attendant"),
            (User.ROLE_DOCTOR, "Doctor"),
            (User.ROLE_NURSE, "Nurse"),
            (User.ROLE_SONOGRAPHER, "Sonographer"),
        ]
        self.fields["role"].required = False
        for name, field in self.fields.items():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        custom = (cleaned.get("custom_role") or "").strip()
        role = (cleaned.get("role") or "").strip()
        if custom:
            # Normalise: lowercase, spaces → underscores, max 50 chars
            cleaned["role"] = custom.lower().replace(" ", "_")[:50]
        elif not role:
            self.add_error("role", "Please select a role or enter a custom one below.")
        return cleaned


class HospitalStaffUserUpdateForm(forms.ModelForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Optional: assign this user to one or more modules this hospital has subscribed to.",
    )
    custom_role = forms.CharField(
        required=False,
        max_length=50,
        label="Custom role",
        widget=forms.TextInput(attrs={
            "placeholder": "e.g. Physiotherapist, Dentist…",
            "class": "form-control",
            "autocomplete": "off",
        }),
    )

    STANDARD_ROLES = {
        User.ROLE_HOSPITAL_ADMIN, User.ROLE_RECEPTIONIST, User.ROLE_LAB_ATTENDANT,
        User.ROLE_DOCTOR, User.ROLE_NURSE, User.ROLE_SONOGRAPHER,
    }

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "role", "is_active", "groups")

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = _module_group_queryset(hospital)
        standard_choices = [
            ("", "— select a role —"),
            (User.ROLE_HOSPITAL_ADMIN, "Hospital Admin"),
            (User.ROLE_RECEPTIONIST, "Receptionist"),
            (User.ROLE_LAB_ATTENDANT, "Lab Attendant"),
            (User.ROLE_DOCTOR, "Doctor"),
            (User.ROLE_NURSE, "Nurse"),
            (User.ROLE_SONOGRAPHER, "Sonographer"),
        ]
        # If the instance has a custom role not in standard list, show it as a choice
        # so it doesn't get wiped on the next save and also pre-fill the custom field
        if self.instance and self.instance.pk:
            current_role = self.instance.role or ""
            if current_role and current_role not in self.STANDARD_ROLES:
                standard_choices.append((current_role, current_role.replace("_", " ").title()))
                self.fields["custom_role"].initial = current_role.replace("_", " ").title()
        self.fields["role"].choices = standard_choices
        self.fields["role"].required = False
        for field in self.fields.values():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        custom = (cleaned.get("custom_role") or "").strip()
        role = (cleaned.get("role") or "").strip()
        if custom:
            cleaned["role"] = custom.lower().replace(" ", "_")[:50]
        elif not role:
            self.add_error("role", "Please select a role or enter a custom one below.")
        return cleaned


class HospitalServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ("name", "category", "price", "test_profile", "is_active", "is_per_day")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["test_profile"].queryset = TestProfile.objects.filter(is_active=True).order_by("name")
        self.fields["test_profile"].required = False
        self.fields["test_profile"].empty_label = "— no template —"
        self.fields["test_profile"].help_text = "Lab services only. Links this service to a test template so results are auto-structured."
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = (
            "date",
            "description",
            "category",
            "amount",
            "source",
            "bank_account",
            "mobile_money_account",
            "cash_drawer",
            "notes",
        )
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }

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

        if category in {InventoryItem.CATEGORY_SYRUP, InventoryItem.CATEGORY_IV_FLUID, InventoryItem.CATEGORY_IM}:
            if not units_per_pack:
                self.add_error("units_per_pack", "This medicine form needs the pack volume in ml.")
            if not base_unit:
                cleaned_data["base_unit"] = "ml"
            if not pack_type or pack_type == "unit":
                if category == InventoryItem.CATEGORY_IM:
                    cleaned_data["unit"] = "vial"
                elif category == InventoryItem.CATEGORY_IV_FLUID:
                    cleaned_data["unit"] = "bag"
                else:
                    cleaned_data["unit"] = "bottle"

        if category == InventoryItem.CATEGORY_IV_MED:
            # IV medication (powder vials) — uses same math as tablets; pack = one vial
            if not pack_type or pack_type == "unit":
                cleaned_data["unit"] = "vial"
            if not base_unit:
                cleaned_data["base_unit"] = "mg"

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

    def save(self, commit=True):
        item = super().save(commit=False)
        if commit:
            item.save()
            
            opening_stock = self.cleaned_data.get("current_quantity") or Decimal("0")
            batch_number = (self.cleaned_data.get("opening_batch_number") or "").strip()
            expiry_date = self.cleaned_data.get("opening_expiry_date")
            
            if opening_stock > 0:
                # If we just created the item or changed its stock, sync_batches_to_stock
                # might have created an "INITIAL" batch. We want to use the batch details
                # provided in the form instead.
                
                # Check if an "INITIAL" batch exists that we should rename/update
                initial_batch = item.batches.filter(batch_number="INITIAL").first()
                
                if initial_batch:
                    if batch_number and batch_number != "INITIAL":
                        # Rename INITIAL to the provided batch number
                        # First check if the target batch number already exists
                        existing = item.batches.filter(batch_number=batch_number).first()
                        if existing:
                            # Merge INITIAL into existing
                            existing.quantity += initial_batch.quantity
                            if expiry_date:
                                existing.expiry_date = expiry_date
                            existing.save()
                            initial_batch.delete()
                        else:
                            initial_batch.batch_number = batch_number
                            initial_batch.expiry_date = expiry_date
                            initial_batch.save()
                    else:
                        initial_batch.expiry_date = expiry_date
                        initial_batch.save()
                else:
                    # No INITIAL batch, maybe it was created through another batch name
                    # or there were already batches. add_or_update_batch will handle it correctly.
                    # Wait, if we are EDITING an existing item and changed current_quantity, 
                    # sync_batches_to_stock already adjusted the latest batch.
                    # We don't want to add stock again here.
                    pass

        return item


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


class PlatformSettingsForm(forms.ModelForm):
    class Meta:
        model = PlatformSettings
        fields = (
            "broadcast_enabled",
            "internal_messages_enabled",
            "direct_messages_enabled",
            "message_retention_days",
        )
        widgets = {
            "message_retention_days": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "max": "365", "placeholder": "e.g. 7"}
            ),
        }


class SupportTokenForm(forms.ModelForm):
    class Meta:
        model = SupportToken
        fields = ("subject", "category")
        widgets = {
            "subject": forms.TextInput(attrs={"class": "form-control", "placeholder": "Brief description of your issue"}),
            "category": forms.Select(attrs={"class": "form-control"}),
        }


class SupportTokenReplyForm(forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": "4", "placeholder": "Write your reply…"}),
        label="Reply",
    )


class SupportTokenStatusForm(forms.ModelForm):
    class Meta:
        model = SupportToken
        fields = ("status", "priority")
        widgets = {
            "status": forms.Select(attrs={"class": "form-control"}),
            "priority": forms.Select(attrs={"class": "form-control"}),
        }
