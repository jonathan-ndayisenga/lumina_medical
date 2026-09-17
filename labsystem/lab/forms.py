from django import forms

from .models import LabConsumable


class LabConsumableForm(forms.ModelForm):
    class Meta:
        model = LabConsumable
        fields = ['name', 'unit', 'current_quantity', 'minimum_quantity']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Glass Slides'}),
            'unit': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. slides'}),
            'current_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '0'}),
            'minimum_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '0'}),
        }
        labels = {
            'minimum_quantity': 'Low-stock alert below',
        }
