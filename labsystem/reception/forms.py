from decimal import Decimal
import re

from django import forms
from django.utils import timezone

from .models import Patient, Payment, Service, Visit


AGE_UNIT_CHOICES = [
    ("YRS", "Years"),
    ("MTH", "Months"),
]


def split_age(age_text):
    raw = (age_text or "").strip().upper()
    match = re.match(r"^\s*(\d+)\s*([A-Z]+)?\s*$", raw)
    if not match:
        return "", "YRS"
    return match.group(1), (match.group(2) or "YRS")


class PatientForm(forms.ModelForm):
    age = forms.CharField(widget=forms.HiddenInput(), required=False)
    registration_date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    date_of_birth = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        help_text="Optional. If you enter age instead, the system can approximate date of birth.",
    )
    age_value = forms.IntegerField(min_value=0, label="Age", required=False)
    age_unit = forms.ChoiceField(choices=AGE_UNIT_CHOICES, label="Unit", required=False)

    class Meta:
        model = Patient
        fields = [
            "name",
            "registration_date",
            "date_of_birth",
            "age",
            "age_value",
            "age_unit",
            "weight_kg",
            "sex",
            "contact",
            "email",
            "address",
            "next_of_kin",
            "next_of_kin_contact",
            "nin",
            "id_verified",
            "insurance_provider",
            "insurance_policy_number",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "weight_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "Weight (kg)"}),
            "sex": forms.Select(attrs={"class": "form-control"}),
            "contact": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "next_of_kin": forms.TextInput(attrs={"class": "form-control"}),
            "next_of_kin_contact": forms.TextInput(attrs={"class": "form-control"}),
            "nin": forms.TextInput(attrs={"class": "form-control"}),
            "id_verified": forms.CheckboxInput(attrs={"class": "form-checkbox"}),
            "insurance_provider": forms.TextInput(attrs={"class": "form-control"}),
            "insurance_policy_number": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["age_value"].widget.attrs.update({"class": "form-control"})
        self.fields["age_unit"].widget.attrs.update({"class": "form-control"})
        self.fields["date_of_birth"].widget.attrs.update({"class": "form-control"})
        if not self.is_bound and not self.instance.pk and "registration_date" not in self.initial:
            self.initial["registration_date"] = timezone.localdate()
        age_value, age_unit = split_age(self.instance.age if self.instance.pk else self.initial.get("age"))
        if age_value and not self.is_bound:
            self.initial["age_value"] = age_value
            self.initial["age_unit"] = age_unit
        if self.instance.pk and not self.is_bound and self.instance.date_of_birth:
            self.initial["date_of_birth"] = self.instance.date_of_birth

    def clean(self):
        cleaned = super().clean()
        dob = cleaned.get("date_of_birth")
        age_value = cleaned.get("age_value")
        age_unit = cleaned.get("age_unit") or "YRS"

        if not dob and (age_value is None or age_value == ""):
            raise forms.ValidationError("Either Date of Birth or Age is required.")

        today = timezone.localdate()

        if dob:
            # Compute and store age string from DOB (years or months).
            years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if years >= 1:
                cleaned["age"] = f"{years}YRS"
                cleaned["age_value"] = years
                cleaned["age_unit"] = "YRS"
            else:
                months = (today.year - dob.year) * 12 + (today.month - dob.month)
                if today.day < dob.day:
                    months -= 1
                months = max(months, 0)
                cleaned["age"] = f"{months}MTH"
                cleaned["age_value"] = months
                cleaned["age_unit"] = "MTH"
        else:
            # Age -> approximate DOB (Jan 1 for years, 1st of month for months).
            try:
                age_value_int = int(age_value)
            except (TypeError, ValueError):
                age_value_int = None
            if age_value_int is not None:
                if age_unit == "MTH":
                    total_months = age_value_int
                    year = today.year
                    month = today.month - total_months
                    while month <= 0:
                        month += 12
                        year -= 1
                    cleaned["date_of_birth"] = timezone.datetime(year, month, 1).date()
                else:
                    cleaned["date_of_birth"] = timezone.datetime(today.year - age_value_int, 1, 1).date()
                cleaned["age"] = f"{age_value_int}{age_unit}"
        return cleaned


class VisitCreateForm(forms.ModelForm):
    services = forms.ModelMultipleChoiceField(
        queryset=Service.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "hidden", "id": "service-select-hidden"}),
        required=True,
    )
    class Meta:
        model = Visit
        fields = ["notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        hospital = kwargs.pop("hospital", None)
        super().__init__(*args, **kwargs)
        if hospital is not None:
            self.fields["services"].queryset = Service.objects.filter(hospital=hospital, is_active=True)
        self.fields["services"].label_from_instance = lambda service: f"{service.name} - {service.price:.2f}"
        if self.instance.pk and not self.is_bound:
            self.initial["services"] = self.instance.visit_services.values_list("service_id", flat=True)

    def clean_services(self):
        services = self.cleaned_data["services"]
        if not services:
            raise forms.ValidationError("Select at least one service.")
        return services

    def calculate_total(self):
        services = self.cleaned_data.get("services")
        if not services:
            return Decimal("0")
        return sum((service.price for service in services), Decimal("0"))


class QuickDispenseStartForm(forms.Form):
    CLIENT_WALK_IN = "walk_in"
    CLIENT_EXISTING = "existing"

    CLIENT_CHOICES = [
        (CLIENT_WALK_IN, "Walk-in client"),
        (CLIENT_EXISTING, "Existing patient"),
    ]

    client_type = forms.ChoiceField(choices=CLIENT_CHOICES)
    patient = forms.ModelChoiceField(queryset=Patient.objects.none(), required=False)
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Optional note for this dispense visit."}),
    )

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client_type"].widget.attrs.update({"class": "form-control"})
        self.fields["patient"].widget.attrs.update({"class": "form-control"})
        if hospital is not None:
            self.fields["patient"].queryset = Patient.objects.filter(hospital=hospital).order_by("name")

    def clean(self):
        cleaned = super().clean()
        client_type = cleaned.get("client_type")
        patient = cleaned.get("patient")
        if client_type == self.CLIENT_EXISTING and not patient:
            self.add_error("patient", "Choose the patient you want to dispense for.")
        return cleaned


class CompleteVisitForm(forms.Form):
    amount_paid = forms.DecimalField(min_value=Decimal("0"), decimal_places=2, max_digits=10)
    payment_mode = forms.ChoiceField(choices=Payment.MODE_CHOICES)
    bank_account = forms.ModelChoiceField(queryset=None, required=False, label="Bank account")
    mobile_account = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Mobile money account",
    )
    payment_notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}), required=False)

    def __init__(self, *args, remaining_balance=Decimal("0"), hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.remaining_balance = remaining_balance
        self.hospital = hospital
        self.fields["amount_paid"].widget.attrs.update({"class": "form-control", "step": "0.01"})
        self.fields["payment_mode"].widget.attrs.update({"class": "form-control"})
        self.fields["bank_account"].widget.attrs.update({"class": "form-control"})
        self.fields["mobile_account"].widget.attrs.update({"class": "form-control"})

        from admin_dashboard.models import BankAccount, MobileMoneyAccount

        bank_qs = BankAccount.objects.filter(hospital=hospital, is_active=True) if hospital is not None else BankAccount.objects.none()
        mobile_qs = (
            MobileMoneyAccount.objects.filter(hospital=hospital, is_active=True) if hospital is not None else MobileMoneyAccount.objects.none()
        )
        self.fields["bank_account"].queryset = bank_qs
        self.fields["mobile_account"].queryset = mobile_qs

    def clean_amount_paid(self):
        amount_paid = self.cleaned_data["amount_paid"]
        if amount_paid > self.remaining_balance:
            raise forms.ValidationError("Amount paid cannot exceed the remaining balance.")
        return amount_paid

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("payment_mode")
        bank_account = cleaned.get("bank_account")
        mobile_account = cleaned.get("mobile_account")

        # Silent defaults when only one account exists.
        if mode == Payment.MODE_CARD and not bank_account:
            if self.fields["bank_account"].queryset.count() == 1:
                cleaned["bank_account"] = self.fields["bank_account"].queryset.first()
                bank_account = cleaned["bank_account"]
            else:
                self.add_error("bank_account", "Select the bank account where this card payment was deposited.")
        if mode == Payment.MODE_MOBILE_MONEY and not mobile_account:
            if self.fields["mobile_account"].queryset.count() == 1:
                cleaned["mobile_account"] = self.fields["mobile_account"].queryset.first()
                mobile_account = cleaned["mobile_account"]
            else:
                self.add_error("mobile_account", "Select the mobile money account that received this payment.")

        if mode != Payment.MODE_CARD:
            cleaned["bank_account"] = None
        if mode != Payment.MODE_MOBILE_MONEY:
            cleaned["mobile_account"] = None
        return cleaned
