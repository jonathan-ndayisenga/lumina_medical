from django import forms

from reception.models import Triage

from .models import NurseNote


class NurseNoteForm(forms.ModelForm):
    class Meta:
        model = NurseNote
        fields = ["notes"]
        widgets = {
            "notes": forms.Textarea(
                attrs={
                    "rows": 8,
                    "class": "form-control",
                    "placeholder": "Document nursing care, observations, medication administration, or follow-up instructions.",
                }
            ),
        }


class TriageForm(forms.ModelForm):
    class Meta:
        model = Triage
        fields = [
            "weight_kg",
            "bp_systolic",
            "bp_diastolic",
            "pulse",
            "respiratory_rate",
            "temperature_celsius",
            "oxygen_saturation",
            "glucose_mg_dl",
        ]
        widgets = {
            "weight_kg": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "bp_systolic": forms.NumberInput(attrs={"class": "form-control"}),
            "bp_diastolic": forms.NumberInput(attrs={"class": "form-control"}),
            "pulse": forms.NumberInput(attrs={"class": "form-control"}),
            "respiratory_rate": forms.NumberInput(attrs={"class": "form-control"}),
            "temperature_celsius": forms.NumberInput(attrs={"class": "form-control", "step": "0.1"}),
            "oxygen_saturation": forms.NumberInput(attrs={"class": "form-control"}),
            "glucose_mg_dl": forms.NumberInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Minimum required for nurse sign-off.
        self.fields["weight_kg"].required = True
        self.fields["bp_systolic"].required = True
        self.fields["bp_diastolic"].required = True
