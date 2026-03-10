from django import forms
from django.forms import inlineformset_factory
from .models import LabReport, TestResult, TestCatalog


class LabReportForm(forms.ModelForm):
    class Meta:
        model = LabReport
        fields = [
            'patient_name',
            'patient_age',
            'patient_sex',
            'sample_date',
            'specimen_type',
            'attendant_name',
            'comments',
        ]
        widgets = {
            'sample_date': forms.DateInput(attrs={'type': 'date'}),
            'comments': forms.Textarea(attrs={'rows': 3}),
        }


class TestResultForm(forms.ModelForm):
    class Meta:
        model = TestResult
        fields = ['test', 'result_value', 'reference_range', 'unit']
        widgets = {
            'test': forms.Select(attrs={'class': 'test-select'}),
            'result_value': forms.TextInput(attrs={'placeholder': 'Result'}),
            'reference_range': forms.TextInput(attrs={'placeholder': 'e.g. 23.5-33.7', 'class': 'range-field'}),
            'unit': forms.TextInput(attrs={'placeholder': 'e.g. g/dL', 'class': 'unit-field'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['test'].queryset = TestCatalog.objects.order_by('display_order', 'name')


TestResultFormSet = inlineformset_factory(
    LabReport,
    TestResult,
    form=TestResultForm,
    extra=1,
    can_delete=True,
)
