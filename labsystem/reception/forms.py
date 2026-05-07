from decimal import Decimal
import re

from django import forms
from django.utils import timezone

from doctor.models import Prescription

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
        required=False,
    )
    adjustment_origin_prescription = forms.ModelChoiceField(
        queryset=Prescription.objects.none(),
        required=False,
        empty_label="Select the original prescription",
    )
    adjustment_days_used = forms.IntegerField(min_value=1, required=False, label="Days Already Used")
    follow_up_parent_visit = forms.ModelChoiceField(
        queryset=Visit.objects.none(),
        required=False,
        empty_label="Select the completed visit being followed up",
        label="Previous Visit",
    )
    adjustment_reason = forms.CharField(
        required=False,
        label="Reason for Adjustment",
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "class": "form-control",
                "placeholder": "Example: Side effects, ineffective response, or change of diagnosis.",
            }
        ),
    )

    class Meta:
        model = Visit
        fields = ["visit_type", "notes"]
        widgets = {
            "visit_type": forms.Select(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        hospital = kwargs.pop("hospital", None)
        patient = kwargs.pop("patient", None)
        super().__init__(*args, **kwargs)
        self.hospital = hospital
        self.patient = patient or getattr(self.instance, "patient", None)
        if hospital is not None:
            self.fields["services"].queryset = Service.objects.filter(hospital=hospital, is_active=True)
        self.fields["services"].label_from_instance = lambda service: f"{service.name} - {service.price:.2f}"
        self.fields["visit_type"].help_text = "Use Adjustment Visit when the doctor is swapping medication already paid for on a previous visit."
        self.fields["follow_up_parent_visit"].widget.attrs.update({"class": "form-control"})
        self.fields["adjustment_origin_prescription"].widget.attrs.update({"class": "form-control"})
        self.fields["adjustment_days_used"].widget.attrs.update({"class": "form-control", "placeholder": "3"})
        follow_up_queryset = Visit.objects.none()
        origin_queryset = Prescription.objects.none()
        if hospital is not None and self.patient is not None:
            follow_up_queryset = (
                Visit.objects.filter(
                    hospital=hospital,
                    patient=self.patient,
                    status=Visit.STATUS_COMPLETED,
                )
                .order_by("-visit_date")
            )
            if self.instance.pk:
                follow_up_queryset = follow_up_queryset.exclude(pk=self.instance.pk)
            origin_queryset = (
                Prescription.objects.filter(
                    visit__hospital=hospital,
                    visit__patient=self.patient,
                    covered_by_previous=False,
                    dispensed=True,
                    visit__status=Visit.STATUS_COMPLETED,
                )
                .select_related("drug", "visit")
                .order_by("-prescribed_at", "-id")
            )
        self.fields["follow_up_parent_visit"].queryset = follow_up_queryset
        self.fields["follow_up_parent_visit"].label_from_instance = (
            lambda visit: f"{visit.visit_date:%Y-%m-%d} - {visit.get_status_display()} - {visit.total_amount}"
        )
        self.fields["adjustment_origin_prescription"].queryset = origin_queryset
        self.fields["adjustment_origin_prescription"].label_from_instance = (
            lambda prescription: (
                f"{prescription.drug.name} - {prescription.duration_days} day(s) "
                f"from {prescription.visit.visit_date:%Y-%m-%d}"
            )
        )
        if self.instance.pk and not self.is_bound:
            self.initial["services"] = self.instance.visit_services.values_list("service_id", flat=True)
            self.initial["follow_up_parent_visit"] = self.instance.parent_visit_id
            self.initial["adjustment_origin_prescription"] = self.instance.adjustment_origin_prescription_id
            self.initial["adjustment_days_used"] = self.instance.adjustment_days_used
            self.initial["adjustment_reason"] = self.instance.adjustment_reason

    def _clean_follow_up_visit(self, cleaned_data):
        parent_visit = cleaned_data.get("follow_up_parent_visit")
        services = cleaned_data.get("services")

        if parent_visit is None:
            self.add_error("follow_up_parent_visit", "Choose the completed visit this follow-up is linked to.")
            return cleaned_data
        if self.patient is not None and parent_visit.patient_id != self.patient.pk:
            self.add_error("follow_up_parent_visit", "The selected follow-up visit does not belong to this patient.")
        if parent_visit.status != Visit.STATUS_COMPLETED:
            self.add_error("follow_up_parent_visit", "Follow-up visits must point to a completed and paid previous visit.")
        if not parent_visit.is_fully_paid:
            self.add_error("follow_up_parent_visit", "The previous visit must be fully paid before a follow-up can be created.")

        if not services:
            self.add_error("services", "Follow-up visits require at least one service selection.")
            return cleaned_data
        has_consultation = any(service.category == Service.CATEGORY_CONSULTATION for service in services)
        if not has_consultation:
            self.add_error("services", "Follow-up visits must include a doctor consultation service.")
        return cleaned_data

    def _clean_adjustment_visit(self, cleaned_data):
        origin = cleaned_data.get("adjustment_origin_prescription")
        days_used = cleaned_data.get("adjustment_days_used")
        reason = (cleaned_data.get("adjustment_reason") or "").strip()
        services = cleaned_data.get("services")

        if origin is None:
            self.add_error("adjustment_origin_prescription", "Choose the prescription being adjusted.")
            return cleaned_data
        if self.patient is not None and origin.visit.patient_id != self.patient.pk:
            self.add_error("adjustment_origin_prescription", "The selected prescription does not belong to this patient.")
        if not origin.dispensed:
            self.add_error("adjustment_origin_prescription", "Only already-dispensed prescriptions can be swapped without new billing.")
        if origin.visit.status != Visit.STATUS_COMPLETED or not origin.visit.is_fully_paid:
            self.add_error("adjustment_origin_prescription", "The original prescription must come from a fully paid and completed visit.")
        if days_used is None:
            self.add_error("adjustment_days_used", "Enter how many days the original medicine was already used.")
            return cleaned_data
        if days_used >= origin.duration_days:
            self.add_error(
                "adjustment_days_used",
                f"Days already used must be less than the original {origin.duration_days}-day prescription.",
            )
            return cleaned_data
        if services:
            self.add_error("services", "Adjustment visits do not allow new billable services. The replacement is covered by the previous payment.")

        cleaned_data["adjustment_reason"] = reason
        cleaned_data["adjustment_remaining_days"] = origin.duration_days - days_used
        return cleaned_data

    def clean_services(self):
        services = self.cleaned_data["services"]
        visit_type = self.cleaned_data.get("visit_type") or getattr(self.instance, "visit_type", Visit.TYPE_NORMAL)
        
        # NORMAL and FOLLOW-UP visits require billable services
        if visit_type in (Visit.TYPE_NORMAL, Visit.TYPE_FOLLOW_UP):
            if not services:
                raise forms.ValidationError(
                    "You must select at least one billable service. This ensures the patient is properly billed and routed to the appropriate department."
                )
        
        # ADJUSTMENT visits must NOT have services (they are already paid for)
        if visit_type == Visit.TYPE_ADJUSTMENT:
            if services:
                raise forms.ValidationError(
                    "Adjustment visits cannot include new services. The replacement is covered by the original payment."
                )
        
        return services

    def clean(self):
        cleaned_data = super().clean()
        visit_type = cleaned_data.get("visit_type") or Visit.TYPE_NORMAL
        if visit_type == Visit.TYPE_ADJUSTMENT:
            cleaned_data = self._clean_adjustment_visit(cleaned_data)
            cleaned_data["follow_up_parent_visit"] = None
        elif visit_type == Visit.TYPE_FOLLOW_UP:
            cleaned_data = self._clean_follow_up_visit(cleaned_data)
            cleaned_data["adjustment_origin_prescription"] = None
            cleaned_data["adjustment_days_used"] = 0
            cleaned_data["adjustment_remaining_days"] = 0
            cleaned_data["adjustment_reason"] = ""
        else:
            cleaned_data["follow_up_parent_visit"] = None
            cleaned_data["adjustment_origin_prescription"] = None
            cleaned_data["adjustment_days_used"] = 0
            cleaned_data["adjustment_remaining_days"] = 0
            cleaned_data["adjustment_reason"] = ""
        return cleaned_data

    def calculate_total(self):
        if self.cleaned_data.get("visit_type") == Visit.TYPE_ADJUSTMENT:
            return Decimal("0")
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
