import re

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import LabReport, TestResult, TestProfile


class LabReportForm(forms.ModelForm):
    AGE_UNIT_CHOICES = (
        ('YRS', 'Years'),
        ('MTH', 'Months'),
    )

    profile = forms.ModelChoiceField(
        queryset=TestProfile.objects.filter(is_active=True).order_by('display_order', 'name'),
        required=False,
        empty_label='Manual Entry',
    )
    patient_age = forms.CharField(required=False, widget=forms.HiddenInput())
    age_value = forms.IntegerField(
        min_value=0,
        required=True,
        widget=forms.NumberInput(attrs={'placeholder': 'Age'}),
    )
    age_unit = forms.ChoiceField(
        choices=AGE_UNIT_CHOICES,
        required=True,
        initial='YRS',
    )

    class Meta:
        model = LabReport
        fields = [
            'profile',
            'patient_name',
            'patient_age',
            'patient_sex',
            'referred_by',
            'sample_date',
            'specimen_type',
            'attendant_name',
            'comments',
        ]
        widgets = {
            'sample_date': forms.DateInput(attrs={'type': 'date'}),
            'comments': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stored_age = ''
        if self.is_bound:
            stored_age = (
                self.data.get(self.add_prefix('patient_age'))
                or self.data.get(self.add_prefix('age_value'))
                or ''
            )
        elif self.instance and self.instance.pk:
            stored_age = self.instance.patient_age or ''

        age_value, age_unit = self._split_age(stored_age)
        if age_value is not None:
            self.fields['age_value'].initial = age_value
        self.fields['age_unit'].initial = age_unit or 'YRS'

        if self.instance and self.instance.pk and self.instance.visit_id:
            for field_name in ('patient_name', 'age_value', 'age_unit', 'patient_sex'):
                self.fields[field_name].disabled = True

    @staticmethod
    def _split_age(value):
        raw = (value or '').strip().upper()
        if not raw:
            return None, 'YRS'

        digits_match = re.search(r'(\d+)', raw)
        age_value = int(digits_match.group(1)) if digits_match else None

        if any(token in raw for token in ('MTH', 'MONTH', 'MON')):
            return age_value, 'MTH'
        if any(token in raw for token in ('YRS', 'YEAR', 'YR')):
            return age_value, 'YRS'
        return age_value, 'YRS'

    def clean(self):
        cleaned_data = super().clean()
        age_value = cleaned_data.get('age_value')
        age_unit = cleaned_data.get('age_unit') or 'YRS'
        if age_value is None:
            return cleaned_data
        cleaned_data['patient_age'] = f'{age_value}{age_unit}'
        return cleaned_data


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
        fields = ['source_profile', 'section_name', 'display_order', 'result_value', 'reference_range', 'unit', 'comment']
        widgets = {
            'source_profile': forms.HiddenInput(),
            'section_name': forms.HiddenInput(),
            'display_order': forms.HiddenInput(),
            'result_value': forms.TextInput(attrs={'placeholder': 'Result', 'class': 'result-field'}),
            'reference_range': forms.TextInput(attrs={'placeholder': 'e.g. 23.5-33.7', 'class': 'range-field'}),
            'unit': forms.TextInput(attrs={'placeholder': 'e.g. g/dL', 'class': 'unit-field'}),
            'comment': forms.TextInput(attrs={'placeholder': 'Comment', 'class': 'comment-field'}),
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
    extra=0,
    can_delete=True,
)
