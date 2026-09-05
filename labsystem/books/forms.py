from django import forms
from django.forms import inlineformset_factory

from .document_models import (
    Client,
    CompanySettings,
    Expense,
    Invoice,
    InvoiceLine,
    Payment,
)
from .ledger_models import Account


WIDGET_CLASS = "form-control"


def _style(fields):
    for field in fields.values():
        existing = field.widget.attrs.get("class", "")
        field.widget.attrs["class"] = f"{existing} {WIDGET_CLASS}".strip()


class CompanySettingsForm(forms.ModelForm):
    class Meta:
        model = CompanySettings
        exclude = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "name", "trading_name", "tin", "is_withholding_agent",
            "address", "phone", "email", "contact_person", "notes", "active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["client", "kind", "issue_date", "due_date", "reference", "notes", "currency", "apply_vat"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(active=True)
        _style(self.fields)


class InvoiceLineForm(forms.ModelForm):
    class Meta:
        model = InvoiceLine
        fields = ["product", "revenue_account", "description", "detail", "quantity", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revenue_account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_INCOME)
        _style(self.fields)


InvoiceLineFormSet = inlineformset_factory(
    Invoice, InvoiceLine, form=InvoiceLineForm, extra=1, can_delete=True,
)


class ScheduleForm(forms.Form):
    months = forms.IntegerField(min_value=1, required=False, help_text="Leave blank for no instalment schedule.")
    first_due = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class PaymentForm(forms.ModelForm):
    invoice = forms.ModelChoiceField(
        queryset=Invoice.objects.none(),
        required=False,
        help_text="Leave blank to auto-allocate to the oldest open invoice(s) first.",
    )

    class Meta:
        model = Payment
        fields = ["client", "date", "amount", "method", "deposit_account", "reference", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(active=True)
        self.fields["deposit_account"].queryset = Account.objects.filter(active=True, is_payment_account=True)
        self.fields["deposit_account"].required = False
        if client is not None:
            self.fields["client"].initial = client
            self.fields["invoice"].queryset = client.invoices.filter(status=Invoice.STATUS_OPEN)
        _style(self.fields)


class ExpenseForm(forms.ModelForm):
    confirm_duplicate = forms.BooleanField(
        required=False, label="Yes, record this anyway — it really is a separate payment.",
    )

    class Meta:
        model = Expense
        fields = [
            "date", "supplier", "description", "account", "currency", "amount", "fx_rate",
            "method", "paid_from", "paid_personally", "reference", "transaction_charge",
            "receipt", "no_receipt_reason", "period_start", "period_end",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "period_start": forms.DateInput(attrs={"type": "date"}),
            "period_end": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_EXPENSE)
        self.fields["paid_from"].queryset = Account.objects.filter(active=True, is_payment_account=True)
        self.fields["paid_from"].required = False
        _style(self.fields)

    def clean(self):
        cleaned = super().clean()
        receipt = cleaned.get("receipt")
        no_receipt_reason = cleaned.get("no_receipt_reason")
        if not receipt and not (self.instance.receipt if self.instance.pk else None) and not no_receipt_reason:
            self.add_error("no_receipt_reason", "Attach a receipt, or explain why none is available.")
        currency = cleaned.get("currency")
        fx_rate = cleaned.get("fx_rate")
        if currency and currency != "UGX" and fx_rate == 1:
            self.add_error("fx_rate", "Set an explicit FX rate for a non-UGX expense.")
        return cleaned


class ManualJournalLineForm(forms.Form):
    account = forms.ModelChoiceField(queryset=Account.objects.filter(active=True))
    debit = forms.DecimalField(max_digits=14, decimal_places=2, required=False, min_value=0)
    credit = forms.DecimalField(max_digits=14, decimal_places=2, required=False, min_value=0)
    narration = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


ManualJournalLineFormSet = forms.formset_factory(ManualJournalLineForm, extra=4)


class ManualEntryForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    memo = forms.CharField(max_length=255)
    reference = forms.CharField(max_length=50, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)
