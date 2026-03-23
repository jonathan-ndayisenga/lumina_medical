from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import LabReport, TestResult


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
    test_name = forms.CharField(
        label='Test',
        max_length=100,
        required=True,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Type or choose a test',
                'class': 'test-name-field',
                'list': 'test-name-suggestions',
                'autocomplete': 'off',
            }
        ),
    )

    class Meta:
        model = TestResult
        fields = ['result_value', 'reference_range', 'unit']
        widgets = {
            'result_value': forms.TextInput(attrs={'placeholder': 'Result', 'class': 'result-field'}),
            'reference_range': forms.TextInput(attrs={'placeholder': 'e.g. 23.5-33.7', 'class': 'range-field'}),
            'unit': forms.TextInput(attrs={'placeholder': 'e.g. g/dL', 'class': 'unit-field'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.test_id:
            self.fields['test_name'].initial = self.instance.test.name


class TestResultInlineFormSet(BaseInlineFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        row_number = (index or 0) + 1
        form.fields['test_name'].error_messages['required'] = f'Row {row_number}: test name is required.'
        form.fields['result_value'].error_messages['required'] = f'Row {row_number}: result value is required.'


TestResultFormSet = inlineformset_factory(
    LabReport,
    TestResult,
    form=TestResultForm,
    formset=TestResultInlineFormSet,
    extra=1,
    can_delete=True,
)
