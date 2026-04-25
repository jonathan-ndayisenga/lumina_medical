from django import forms

from reception.models import Service

from .models import Consultation, LabRequest


class ConsultationForm(forms.ModelForm):
    # Shared triage fields (stored on reception.Triage, not on Consultation).
    weight_kg = forms.DecimalField(label="Weight (kg)", required=False)
    bp_systolic = forms.IntegerField(label="BP Systolic", required=False)
    bp_diastolic = forms.IntegerField(label="BP Diastolic", required=False)
    pulse = forms.IntegerField(label="Pulse (bpm)", required=False)
    respiratory_rate = forms.IntegerField(label="Respiratory Rate", required=False)
    temperature_celsius = forms.DecimalField(label="Temperature (C)", required=False)
    oxygen_saturation = forms.IntegerField(label="SpO2 (%)", required=False)
    glucose_mg_dl = forms.IntegerField(label="Glucose (mg/dL)", required=False)
    send_to_nurse = forms.BooleanField(required=False)
    send_to_reception = forms.BooleanField(required=False, label="Send to reception for billing")
    # Lab services handled via AJAX - hidden field to collect selected service IDs
    lab_services = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        label="Lab Services"
    )

    class Meta:
        model = Consultation
        fields = ["signs_symptoms", "diagnosis", "treatment", "follow_up_date"]
        widgets = {
            "signs_symptoms": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            "diagnosis": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            "treatment": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            "follow_up_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }

    def __init__(self, *args, hospital=None, triage=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._triage_instance = triage
        for field_name in (
            "weight_kg",
            "bp_systolic",
            "bp_diastolic",
            "pulse",
            "respiratory_rate",
            "temperature_celsius",
            "oxygen_saturation",
            "glucose_mg_dl",
        ):
            self.fields[field_name].widget.attrs.update({"class": "form-control"})

        # Prefer existing Triage values; fall back to legacy Consultation.vitals.
        if self._triage_instance is not None and getattr(self._triage_instance, "pk", None):
            for field_name in (
                "weight_kg",
                "bp_systolic",
                "bp_diastolic",
                "pulse",
                "respiratory_rate",
                "temperature_celsius",
                "oxygen_saturation",
                "glucose_mg_dl",
            ):
                self.initial[field_name] = getattr(self._triage_instance, field_name, None)
        elif self.instance and self.instance.pk:
            vitals = self.instance.vitals or {}
            # Common legacy keys: bp="120/80", pr, rr, temp, glucose, spo2
            bp_value = (vitals.get("bp") or "").strip()
            if bp_value and "/" in bp_value:
                left, right = bp_value.split("/", 1)
                try:
                    self.initial["bp_systolic"] = int(left.strip())
                except ValueError:
                    pass
                try:
                    self.initial["bp_diastolic"] = int(right.strip())
                except ValueError:
                    pass
            self.initial["pulse"] = vitals.get("pr") or vitals.get("pulse") or ""
            self.initial["respiratory_rate"] = vitals.get("rr") or vitals.get("respiratory_rate") or ""
            self.initial["temperature_celsius"] = vitals.get("temp") or vitals.get("temperature_celsius") or ""
            self.initial["glucose_mg_dl"] = vitals.get("glucose") or vitals.get("glucose_mg_dl") or ""
            self.initial["oxygen_saturation"] = vitals.get("spo2") or vitals.get("oxygen_saturation") or ""

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("send_to_nurse") and cleaned_data.get("send_to_reception"):
            raise forms.ValidationError(
                "Choose either nurse follow-up or reception billing, not both at once."
            )
        return cleaned_data

    def cleaned_triage_data(self):
        return {
            "weight_kg": self.cleaned_data.get("weight_kg"),
            "bp_systolic": self.cleaned_data.get("bp_systolic"),
            "bp_diastolic": self.cleaned_data.get("bp_diastolic"),
            "pulse": self.cleaned_data.get("pulse"),
            "respiratory_rate": self.cleaned_data.get("respiratory_rate"),
            "temperature_celsius": self.cleaned_data.get("temperature_celsius"),
            "oxygen_saturation": self.cleaned_data.get("oxygen_saturation"),
            "glucose_mg_dl": self.cleaned_data.get("glucose_mg_dl"),
        }


class LabRequestForm(forms.ModelForm):
    """Form for creating lab requests by doctor or receptionist"""
    
    class Meta:
        model = LabRequest
        fields = ["tests_requested", "clinical_notes", "urgency"]
        widgets = {
            "tests_requested": forms.Textarea(attrs={
                "rows": 4,
                "class": "form-control",
                "placeholder": "e.g., Full Blood Count (CBC), Malaria screening, Blood glucose"
            }),
            "clinical_notes": forms.Textarea(attrs={
                "rows": 3,
                "class": "form-control",
                "placeholder": "Clinical reason for this request, relevant symptoms, etc."
            }),
            "urgency": forms.RadioSelect(choices=LabRequest.URGENCY_CHOICES),
        }
        help_texts = {
            "tests_requested": "List all specific tests or procedures you want performed",
            "clinical_notes": "Provide clinical context to help the lab prioritize and interpret results",
            "urgency": "Routine or Urgent?",
        }
