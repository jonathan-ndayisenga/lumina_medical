"""
Forms for the Lab Management catalog screens (Service Categories, Specimen
Types, Laboratory Services). Kept separate from forms.py, which is about
report/consumable entry, not catalog administration.
"""

from decimal import Decimal

from django import forms
from django.forms import formset_factory

from .models import LabTest, ResultType, ServiceCategory, SpecimenType, ValueType


class ServiceCategoryForm(forms.ModelForm):
    class Meta:
        model = ServiceCategory
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Hematology"}),
            "description": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional"}),
        }


class SpecimenTypeForm(forms.ModelForm):
    class Meta:
        model = SpecimenType
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. CSF"}),
            "description": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional context for lab staff"}),
        }


class LabTestForm(forms.ModelForm):
    price = forms.DecimalField(
        label="Unit Price",
        max_digits=10, decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        help_text="Your hospital's price for this test. Leave blank while you're still building "
                   "the template — it won't be orderable until it has a price.",
    )

    class Meta:
        model = LabTest
        fields = [
            "name", "code", "category", "description", "active_for_ordering",
            "accepted_specimens", "result_type",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Malaria Rapid Test"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. QMC-LAB-MAL"}),
            "category": forms.Select(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Optional context for clinicians and lab staff."}),
            "accepted_specimens": forms.CheckboxSelectMultiple(),
            "result_type": forms.Select(attrs={"class": "form-control"}),
        }
        labels = {
            "active_for_ordering": "Active for ordering",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The dedicated Culture & Sensitivity type is retired in favor of
        # Parameter Panel + Coded (S/I/R) parameters, grouped by drug class —
        # same result, a finished builder instead of an admin-only stub.
        # Don't strand a test that's already this type, if one ever exists.
        is_existing_culture = self.instance.pk and self.instance.result_type == ResultType.CULTURE
        if not is_existing_culture:
            self.fields["result_type"].choices = [
                choice for choice in self.fields["result_type"].choices if choice[0] != ResultType.CULTURE
            ]


# ---------------------------------------------------------------------------
# Result-type rows. Not ModelForms: DEFINED_OPTION rows map 1:1 onto
# DefinedOption, but PARAMETER_PANEL rows are a flattened view over TWO
# models (Parameter + ParameterRange) — one row per range, grouped by
# parameter name on save. See views_catalog.py's _save_* helpers.
# ---------------------------------------------------------------------------

class DefinedOptionRowForm(forms.Form):
    label = forms.CharField(
        required=False, max_length=60,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Positive"}),
    )


DefinedOptionFormSet = formset_factory(DefinedOptionRowForm, extra=1, can_delete=True)


GENDER_CHOICES = [("any", "Both"), ("male", "Male"), ("female", "Female")]


class ParameterRowForm(forms.Form):
    name = forms.CharField(
        required=False, max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Hemoglobin"}),
    )
    group_label = forms.CharField(
        required=False, max_length=80, label="Group (optional)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Cephalosporins"}),
    )
    unit = forms.CharField(
        required=False, max_length=40,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. g/dL"}),
    )
    sex = forms.ChoiceField(
        required=False, choices=GENDER_CHOICES, initial="any", label="Gender",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    age_min = forms.IntegerField(
        required=False, min_value=0, label="Min Age",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "0"}),
    )
    age_max = forms.IntegerField(
        required=False, min_value=0, label="Max Age",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "120"}),
    )
    value_type = forms.ChoiceField(
        required=False, choices=ValueType.choices, initial=ValueType.NUMERIC, label="Value Type",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    ref_range = forms.CharField(
        required=False, max_length=120, label="Reference Range (Label)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 12.0 - 16.0"}),
    )


ParameterRowFormSet = formset_factory(ParameterRowForm, extra=1, can_delete=True)
