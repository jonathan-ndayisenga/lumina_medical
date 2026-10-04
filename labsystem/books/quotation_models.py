from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .document_models import Client, CompanySettings, Invoice, InvoiceLine, _q


class QuotationSettings(models.Model):
    """Quotation defaults. Always exactly one row (like CompanySettings)."""

    prefix = models.CharField(max_length=10, default="TSC", help_text="First part of the number, e.g. TSC in TSC-SACCO-2026-001.")
    validity_days = models.PositiveIntegerField(default=30, help_text="Default days until a quotation expires.")
    intro = models.TextField(
        blank=True,
        default=(
            "Thank you for choosing Ternah. The quotation below covers a complete deployment, "
            "configured to your needs and supported through go-live."
        ),
    )
    note_left_title = models.CharField(max_length=80, blank=True, default="What the price reflects")
    note_left_body = models.TextField(blank=True)
    note_right_title = models.CharField(max_length=80, blank=True, default="Payment & travel")
    note_right_body = models.TextField(blank=True)
    footer_tagline = models.CharField(max_length=80, blank=True, default="Keep it simple.")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "QuotationSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class QuotationField(models.Model):
    """An extra field added in settings; shown in every quotation's details
    box under Quotation No. and Date."""

    TYPE_TEXT = "text"
    TYPE_NUMBER = "number"
    TYPE_DATE = "date"
    TYPE_CHOICES = [(TYPE_TEXT, "Text"), (TYPE_NUMBER, "Number"), (TYPE_DATE, "Date")]

    label = models.CharField(max_length=60)
    field_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_TEXT)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.label


class Quotation(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_CONVERTED = "converted"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SENT, "Sent"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CONVERTED, "Invoiced"),
    ]

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="quotations")
    code = models.CharField(max_length=10, help_text="Middle part of the number, e.g. SACCO in TSC-SACCO-2026-001.")
    number = models.CharField(max_length=40, unique=True, blank=True, null=True, default=None)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    subtitle = models.CharField(max_length=120, blank=True, help_text="Shown under QUOTATION, e.g. SACCO Management System.")
    attention = models.CharField(max_length=120, blank=True, help_text="e.g. The Chairperson / Manager")
    location = models.CharField(max_length=120, blank=True, help_text="e.g. Kajjansi, Uganda")
    issue_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=10, default="UGX")
    intro = models.TextField(blank=True)
    apply_vat = models.BooleanField(default=False)
    highlight_title = models.CharField(max_length=120, blank=True, help_text="Optional green box, e.g. This setup comes with 3 months of FREE hosting.")
    highlight_body = models.CharField(max_length=255, blank=True)
    note_left_title = models.CharField(max_length=80, blank=True)
    note_left_body = models.TextField(blank=True)
    note_right_title = models.CharField(max_length=80, blank=True)
    note_right_body = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    invoice = models.OneToOneField(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="quotation")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]

    def __str__(self):
        return self.number or f"Quotation #{self.pk}"

    @property
    def is_editable(self) -> bool:
        return self.status in (self.STATUS_DRAFT, self.STATUS_SENT)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        if not self.valid_until:
            self.valid_until = self.issue_date + timedelta(days=QuotationSettings.load().validity_days)
        if not self.number:
            from .ledger_models import Sequence
            year = self.issue_date.year
            # One running number per code, e.g. TSC-SACCO-2026-001, TSC-HMS-2026-001.
            prefix = f"{QuotationSettings.load().prefix}-{self.code}"
            self.number = Sequence.next(f"quote:{self.code}"[:30], year, prefix, padding=3)
        super().save(*args, **kwargs)

    def recalculate(self):
        subtotal = sum((line.amount for line in self.lines.all()), Decimal("0"))
        company = CompanySettings.load()
        tax = _q(subtotal * company.vat_rate / Decimal("100")) if self.apply_vat and company.is_vat_registered else Decimal("0")
        self.subtotal = _q(subtotal)
        self.tax_amount = tax
        self.total = _q(subtotal + tax)
        self.save(update_fields=["subtotal", "tax_amount", "total"])

    @transaction.atomic
    def convert_to_invoice(self, revenue_account, kind=Invoice.KIND_ONBOARDING):
        """Copies client and lines into a draft invoice to review and issue."""
        if self.invoice_id:
            raise ValidationError("This quotation has already been turned into an invoice.")
        if self.status == self.STATUS_DECLINED:
            raise ValidationError("A declined quotation cannot be invoiced.")
        invoice = Invoice.objects.create(
            client=self.client, kind=kind, currency=self.currency, apply_vat=self.apply_vat,
            reference=self.number, status=Invoice.STATUS_DRAFT,
        )
        for index, line in enumerate(self.lines.all()):
            InvoiceLine.objects.create(
                invoice=invoice, revenue_account=revenue_account, description=line.title,
                detail=line.description, quantity=Decimal("1"), unit_price=line.amount, sort_order=index,
            )
        self.invoice = invoice
        self.status = self.STATUS_CONVERTED
        self.save(update_fields=["invoice", "status"])
        return invoice


class QuotationLine(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    basis = models.CharField(max_length=80, blank=True, help_text="e.g. One-time, 3 days x 150,000")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]


class QuotationFieldValue(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="field_values")
    field = models.ForeignKey(QuotationField, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("quotation", "field")
