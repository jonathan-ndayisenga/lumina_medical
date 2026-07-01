from django import forms
from .models import HomeCareClient, HomeCareNurse, HomeCareReceipt, HomeCarePlacement


class HomeCareNurseForm(forms.ModelForm):
    class Meta:
        model = HomeCareNurse
        fields = ("name", "age", "tribe", "religion", "address", "qualification", "nin", "contact", "is_active", "notes")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")


class HomeCareClientForm(forms.ModelForm):
    class Meta:
        model = HomeCareClient
        fields = ("name", "location", "contact", "nin", "notes")
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if hasattr(field.widget, "attrs"):
                field.widget.attrs.setdefault("class", "form-control")


class HomeCarePlacementForm(forms.ModelForm):
    class Meta:
        model = HomeCarePlacement
        fields = ("client", "nurse", "service_type", "rate_period", "nurse_rate", "client_rate", "contract_start", "contract_end", "notes")
        widgets = {
            "contract_start": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "contract_end": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, hospital=None, **kwargs):
        super().__init__(*args, **kwargs)
        if hospital:
            self.fields["client"].queryset = HomeCareClient.objects.filter(hospital=hospital)
            self.fields["nurse"].queryset = HomeCareNurse.objects.filter(hospital=hospital, is_active=True)
        for name, field in self.fields.items():
            if name not in ("contract_start", "contract_end", "notes"):
                field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("contract_start")
        end = cleaned.get("contract_end")
        if start and end and end <= start:
            raise forms.ValidationError("Contract end date must be after the start date.")
        return cleaned


class HomeCareReceiptForm(forms.ModelForm):
    class Meta:
        model = HomeCareReceipt
        fields = ("amount_paid", "period_covered", "paid_at", "notes")
        widgets = {
            "paid_at": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
            "notes": forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in ("paid_at", "notes"):
                field.widget.attrs.setdefault("class", "form-control")
