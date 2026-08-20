import re

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import LabConsumable, LabConsumableUsage, LabReport, TestResult, TestProfile


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
            'sample_date': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
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


TestResultFormSet = inlineformset_factory(
    LabReport,
    TestResult,
    form=TestResultForm,
    formset=TestResultInlineFormSet,
    extra=0,
    can_delete=True,
)


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


class BaseLabConsumableUsageFormSet(BaseInlineFormSet):
    def __init__(self, *args, hospital=None, **kwargs):
        self.hospital = hospital
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        qs = (
            LabConsumable.objects.filter(hospital=self.hospital, is_active=True)
            if self.hospital else LabConsumable.objects.none()
        )
        form.fields['consumable'].queryset = qs
        form.fields['consumable'].empty_label = '— Select consumable —'
        return form


LabConsumableUsageFormSet = inlineformset_factory(
    LabReport,
    LabConsumableUsage,
    formset=BaseLabConsumableUsageFormSet,
    fields=['consumable', 'quantity_used', 'notes'],
    widgets={
        'consumable': forms.Select(attrs={'class': 'form-control text-sm'}),
        'quantity_used': forms.NumberInput(attrs={'class': 'form-control text-sm', 'step': '0.5', 'min': '0.5', 'placeholder': 'Qty'}),
        'notes': forms.TextInput(attrs={'class': 'form-control text-sm', 'placeholder': 'Optional note'}),
    },
    extra=1,
    can_delete=True,
)
