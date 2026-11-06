from django import forms
from django.forms import inlineformset_factory
from django.utils.text import slugify

from .document_models import (
    Client,
    ClientProduct,
    CompanySettings,
    CreditNote,
    Expense,
    Invoice,
    InvoiceLine,
    Payment,
    Product,
    WithholdingCredit,
)
from .ledger_models import Account, FinancialYear
from .payee_models import Payee


WIDGET_CLASS = "form-control"


def _style(fields):
    for field in fields.values():
        existing = field.widget.attrs.get("class", "")
        field.widget.attrs["class"] = f"{existing} {WIDGET_CLASS}".strip()


class CompanySettingsForm(forms.ModelForm):
    class Meta:
        model = CompanySettings
        exclude = ["logo"]  # Documents always carry the Ternah logo from static files.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = [
            "hospital", "name", "trading_name", "tin", "is_withholding_agent",
            "address", "phone", "email", "contact_person", "notes", "active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Hospital
        self.fields["hospital"].queryset = Hospital.objects.order_by("name")
        self.fields["hospital"].required = False
        self.fields["hospital"].label = "Linked tenant (if this client runs one of our products)"
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

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("DELETE"):
            return cleaned
        if not cleaned.get("product") and not cleaned.get("revenue_account"):
            raise forms.ValidationError("Pick a product, or an explicit revenue account, for this line.")
        if (cleaned.get("quantity") or 0) <= 0 or (cleaned.get("unit_price") or 0) <= 0:
            raise forms.ValidationError("Enter a quantity and a unit price greater than zero for this line.")
        return cleaned


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

    def __init__(self, *args, client=None, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(active=True)
        self.fields["deposit_account"].queryset = Account.objects.filter(active=True, is_payment_account=True)
        self.fields["deposit_account"].required = False
        if invoice is not None:
            client = invoice.client
        if client is not None:
            self.fields["client"].initial = client
            self.fields["invoice"].queryset = client.invoices.filter(status=Invoice.STATUS_OPEN)
        if invoice is not None:
            self.fields["invoice"].initial = invoice
            self.initial.setdefault("amount", invoice.balance)
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


class WithholdingCreditForm(forms.ModelForm):
    class Meta:
        model = WithholdingCredit
        fields = ["certificate_number", "date", "amount", "certificate"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        if invoice is not None:
            self.fields["amount"].help_text = f"Invoice balance: UGX {invoice.balance:,.0f}"
        _style(self.fields)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if self.invoice is not None and amount > self.invoice.balance:
            raise forms.ValidationError(
                f"This exceeds the invoice's remaining balance of UGX {self.invoice.balance:,.0f}."
            )
        return amount


class CreditNoteForm(forms.ModelForm):
    class Meta:
        model = CreditNote
        fields = ["date", "amount", "reason"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        self.fields["amount"].help_text = f"Includes any VAT. Invoice balance: {invoice.currency} {invoice.balance:,.0f}"
        _style(self.fields)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        if amount > self.invoice.balance:
            raise forms.ValidationError(
                f"This is more than the invoice's balance of {self.invoice.balance:,.0f}. If the client has already paid, void or reallocate the payment first."
            )
        return amount


class StatementUploadForm(forms.Form):
    account = forms.ModelChoiceField(queryset=Account.objects.none(), label="Which account is this statement for?")
    file = forms.FileField(label="Statement (CSV)", widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(active=True, is_payment_account=True).order_by("code")
        _style(self.fields)


class AccountForm(forms.ModelForm):
    class Meta:
        model = Account
        fields = ["code", "name", "type", "role", "is_deductible", "is_direct_cost", "is_payment_account", "description", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)

    def clean_role(self):
        role = self.cleaned_data.get("role")
        if role:
            clash = Account.objects.filter(role=role, active=True).exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError(
                    f"{clash.first()} already carries this role — the posting engine needs exactly one "
                    "active account per role. Deactivate that one first, or leave this role blank."
                )
        return role


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "default_price", "revenue_account", "description", "code", "active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revenue_account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_INCOME)
        self.fields["code"].required = False
        self.fields["code"].help_text = "Short name with no spaces. Leave blank to make one from the name."
        _style(self.fields)

    def clean_code(self):
        code = self.cleaned_data.get("code") or slugify(self.cleaned_data.get("name") or "")[:45] or "product"
        taken = Product.objects.exclude(pk=self.instance.pk)
        if self.cleaned_data.get("code"):
            if taken.filter(code=code).exists():
                raise forms.ValidationError("Another product already uses this code.")
            return code
        unique, number = code, 2
        while taken.filter(code=unique).exists():
            unique, number = f"{code}-{number}", number + 1
        return unique


class ClientProductForm(forms.ModelForm):
    price = forms.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=0,
        help_text="Their agreed price in UGX. Leave blank to use the product's usual price.",
    )

    class Meta:
        model = ClientProduct
        fields = ["product", "price", "quantity", "start_date", "notes", "active"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = client
        products = Product.objects.filter(active=True)
        if self.instance.pk:
            products = Product.objects.filter(pk=self.instance.product_id)
        self.fields["product"].queryset = products
        _style(self.fields)

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        if product and cleaned.get("price") is None:
            cleaned["price"] = product.default_price
        if product and not self.instance.pk and ClientProduct.objects.filter(client=self.client, product=product).exists():
            self.add_error("product", f"{self.client.name} already has this product. Edit it in their list instead.")
        return cleaned


class FinancialYearForm(forms.ModelForm):
    class Meta:
        model = FinancialYear
        fields = ["label", "start_date", "end_date"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


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


from .quotation_models import Quotation, QuotationField, QuotationSettings  # noqa: E402


class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = [
            "client", "code", "subtitle", "attention", "location", "issue_date", "valid_until", "currency",
            "apply_vat", "intro", "highlight_title", "highlight_body",
            "note_left_title", "note_left_body", "note_right_title", "note_right_body",
        ]
        labels = {
            "code": "Quotation code", "subtitle": "Title under QUOTATION", "attention": "Attention (Attn:)",
            "apply_vat": "Charge VAT", "intro": "Opening paragraph", "highlight_title": "Highlight box: heading",
            "highlight_body": "Highlight box: detail", "note_left_title": "Left note: heading",
            "note_left_body": "Left note: text", "note_right_title": "Right note: heading",
            "note_right_body": "Right note: text",
        }
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "intro": forms.Textarea(attrs={"rows": 3}),
            "note_left_body": forms.Textarea(attrs={"rows": 3}),
            "note_right_body": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(active=True)
        self.fields["valid_until"].required = False
        self.fields["valid_until"].help_text = "Leave blank to use the default validity from Quotation settings."
        _style(self.fields)


class QuotationSettingsForm(forms.ModelForm):
    class Meta:
        model = QuotationSettings
        exclude = []
        widgets = {
            "intro": forms.Textarea(attrs={"rows": 3}),
            "note_left_body": forms.Textarea(attrs={"rows": 3}),
            "note_right_body": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class QuotationFieldForm(forms.ModelForm):
    class Meta:
        model = QuotationField
        fields = ["label", "field_type", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class QuotationConvertForm(forms.Form):
    revenue_account = forms.ModelChoiceField(
        queryset=Account.objects.none(), help_text="Income account every line of the invoice is posted to.",
    )
    kind = forms.ChoiceField(choices=Invoice.KIND_CHOICES, initial=Invoice.KIND_ONBOARDING)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["revenue_account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_INCOME)
        _style(self.fields)


class PayeeForm(forms.ModelForm):
    class Meta:
        model = Payee
        fields = [
            "name", "kind", "contact_person", "phone", "email", "tin", "address",
            "momo_number", "bank_name", "bank_account_name", "bank_account_number",
            "client", "default_account", "notes", "active",
        ]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = Payee.objects.exclude(pk=self.instance.pk).exclude(client=None).values("client_id")
        self.fields["client"].queryset = Client.objects.filter(active=True).exclude(pk__in=taken)
        self.fields["client"].required = False
        self.fields["client"].label = "Also our client"
        self.fields["default_account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_EXPENSE)
        _style(self.fields)


class PaymentVoucherForm(forms.Form):
    """Paying someone: records the expense and issues the voucher together."""

    payee = forms.ModelChoiceField(queryset=None, label="Pay to")
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    description = forms.CharField(max_length=255, label="What for", help_text="This is printed on the voucher.")
    account = forms.ModelChoiceField(queryset=None, label="Expense category")
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=1, label="Amount paid (UGX)")
    method = forms.ChoiceField(choices=Expense.METHOD_CHOICES, initial=Expense.METHOD_CASH, label="Paid by")
    paid_from = forms.ModelChoiceField(
        queryset=None, required=False, label="Paid from account",
        help_text="Leave blank to use the usual account for that method.",
    )
    reference = forms.CharField(max_length=100, required=False, help_text="Bank or mobile money transaction ID, cheque number…")
    transaction_charge = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False, initial=0,
        help_text="What the bank or network charged us. Not shown on the voucher.",
    )
    received_by = forms.CharField(
        max_length=150, required=False, label="Collected by",
        help_text="Only if someone else picked it up for them.",
    )
    attachment = forms.FileField(
        required=False, label="Their invoice or receipt",
        help_text="Optional. Attach it if they gave you one.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payee"].queryset = Payee.objects.filter(active=True)
        self.fields["account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_EXPENSE)
        self.fields["paid_from"].queryset = Account.objects.filter(active=True, is_payment_account=True)
        _style(self.fields)

    def expense_fields(self) -> dict:
        data = self.cleaned_data
        return {
            "date": data["date"],
            "description": data["description"],
            "account": data["account"],
            "amount": data["amount"],
            "method": data["method"],
            "paid_from": data.get("paid_from"),
            "reference": data.get("reference", ""),
            "transaction_charge": data.get("transaction_charge") or 0,
        }


class VoucherForExpenseForm(forms.Form):
    """Issuing a voucher for a payment already recorded as an expense."""

    payee = forms.ModelChoiceField(queryset=None, label="Paid to")
    received_by = forms.CharField(max_length=150, required=False, label="Collected by", help_text="Only if someone else picked it up for them.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payee"].queryset = Payee.objects.filter(active=True)
        _style(self.fields)
