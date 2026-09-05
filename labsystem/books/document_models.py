import hashlib
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .ledger_models import Account, JournalEntry


TWO_PLACES = Decimal("0.01")


def _q(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class CompanySettings(models.Model):
    """The letterhead. Always exactly one row."""

    legal_name = models.CharField(max_length=200, blank=True)
    trading_name = models.CharField(max_length=200, blank=True)
    tagline = models.CharField(max_length=200, blank=True)
    tin = models.CharField(max_length=30, blank=True)
    vrn = models.CharField(max_length=30, blank=True, verbose_name="VAT registration number")
    company_reg_no = models.CharField(max_length=30, blank=True)

    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)

    logo = models.ImageField(upload_to="books/logo/", blank=True, null=True)
    brand_colour = models.CharField(max_length=7, blank=True, help_text="Hex colour, e.g. #2f4cff")

    bank_name = models.CharField(max_length=150, blank=True)
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)

    momo_mtn_number = models.CharField(max_length=30, blank=True)
    momo_mtn_name = models.CharField(max_length=150, blank=True)
    momo_airtel_number = models.CharField(max_length=30, blank=True)
    momo_airtel_name = models.CharField(max_length=150, blank=True)

    invoice_prefix = models.CharField(max_length=10, default="INV")
    receipt_prefix = models.CharField(max_length=10, default="RCT")
    credit_note_prefix = models.CharField(max_length=10, default="CN")

    default_payment_terms_days = models.PositiveIntegerField(default=14)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.00"))

    invoice_footer_notes = models.TextField(blank=True)
    receipt_footer_notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Company settings cannot be deleted.")

    @classmethod
    def load(cls) -> "CompanySettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_vat_registered(self) -> bool:
        return bool(self.vrn)

    @property
    def invoice_heading(self) -> str:
        return "TAX INVOICE" if self.is_vat_registered else "INVOICE"

    def missing_fields_for_invoicing(self) -> list:
        missing = []
        if not self.legal_name:
            missing.append("Legal name")
        if not self.tin:
            missing.append("TIN")
        if not self.phone:
            missing.append("Phone")
        if not self.email:
            missing.append("Email")
        if not self.address:
            missing.append("Address")
        has_bank = bool(self.bank_name and self.bank_account_number)
        has_momo = bool(self.momo_mtn_number or self.momo_airtel_number)
        if not (has_bank or has_momo):
            missing.append("Bank details or mobile money details")
        return missing


class Client(models.Model):
    """A business paying Ternah."""

    hospital = models.OneToOneField(
        "accounts.Hospital",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="books_client",
        help_text=(
            "Link to the tenant row if this client runs one of our products. "
            "Blank for website builds and one-off project clients that aren't tenants."
        ),
    )
    name = models.CharField(max_length=200)
    trading_name = models.CharField(max_length=200, blank=True)
    tin = models.CharField(max_length=30, blank=True)
    is_withholding_agent = models.BooleanField(
        default=False,
        help_text="URA-designated agents withhold 6% and remit it directly to URA on your behalf.",
    )
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    payment_terms_days = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_invoiced(self) -> Decimal:
        agg = self.invoices.exclude(status="draft").aggregate(t=models.Sum("total"))
        return agg["t"] or Decimal("0")

    @property
    def total_received(self) -> Decimal:
        agg = self.payments.aggregate(t=models.Sum("amount"))
        return agg["t"] or Decimal("0")

    @property
    def balance(self) -> Decimal:
        return sum((inv.balance for inv in self.invoices.exclude(status="draft")), Decimal("0"))

    @property
    def unallocated_credit(self) -> Decimal:
        return sum((p.unallocated for p in self.payments.all()), Decimal("0"))

    def average_days_to_pay(self):
        durations = []
        for allocation in PaymentAllocation.objects.filter(invoice__client=self).select_related("payment", "invoice"):
            days = (allocation.payment.date - allocation.invoice.issue_date).days
            durations.append(days)
        if not durations:
            return None
        return sum(durations) / len(durations)

    def oldest_open_days(self):
        today = timezone.localdate()
        oldest = None
        for invoice in self.invoices.filter(status="open"):
            if invoice.balance <= 0:
                continue
            days = (today - invoice.issue_date).days
            if oldest is None or days > oldest:
                oldest = days
        return oldest


class Product(models.Model):
    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=50, unique=True)
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="products")
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.revenue_account_id and self.revenue_account.type != Account.TYPE_INCOME:
            raise ValidationError("A product's revenue account must be an income account.")


class Invoice(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_OPEN = "open"
    STATUS_PAID = "paid"
    STATUS_VOID = "void"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_OPEN, "Open"),
        (STATUS_PAID, "Paid"),
        (STATUS_VOID, "Void"),
    ]

    KIND_ONBOARDING = "onboarding"
    KIND_SUBSCRIPTION = "subscription"
    KIND_PROJECT = "project"
    KIND_RETAINER = "retainer"
    KIND_OTHER = "other"
    KIND_CHOICES = [
        (KIND_ONBOARDING, "Onboarding"),
        (KIND_SUBSCRIPTION, "Subscription"),
        (KIND_PROJECT, "Project"),
        (KIND_RETAINER, "Retainer"),
        (KIND_OTHER, "Other"),
    ]

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_OTHER)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    currency = models.CharField(max_length=10, default="UGX")
    apply_vat = models.BooleanField(default=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]

    def __str__(self):
        return self.number or f"Draft #{self.pk}"

    def recalculate(self, save=True):
        subtotal = sum((line.amount for line in self.lines.all()), Decimal("0"))
        settings_row = CompanySettings.load()
        tax = Decimal("0")
        if self.apply_vat and settings_row.is_vat_registered:
            tax = _q(subtotal * settings_row.vat_rate / Decimal("100"))
        self.subtotal = _q(subtotal)
        self.tax_amount = tax
        self.total = _q(subtotal + tax)
        if save:
            self.save(update_fields=["subtotal", "tax_amount", "total"])

    @property
    def amount_paid(self) -> Decimal:
        agg = self.allocations.filter(payment__voided_at__isnull=True).aggregate(t=models.Sum("amount"))
        return agg["t"] or Decimal("0")

    @property
    def wht_credited(self) -> Decimal:
        agg = self.withholding_credits.aggregate(t=models.Sum("amount"))
        return agg["t"] or Decimal("0")

    @property
    def balance(self) -> Decimal:
        if self.status == self.STATUS_VOID:
            return Decimal("0")
        return self.total - self.amount_paid - self.wht_credited

    @property
    def is_settled(self) -> bool:
        return self.balance <= 0

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.status == self.STATUS_OPEN
            and self.due_date
            and self.due_date < timezone.localdate()
            and self.balance > 0
        )

    @property
    def expected_to_date(self) -> Decimal:
        today = timezone.localdate()
        agg = self.installments.filter(due_date__lte=today).aggregate(t=models.Sum("amount"))
        return agg["t"] or Decimal("0")

    @property
    def arrears(self) -> Decimal:
        gap = self.expected_to_date - self.amount_paid
        return gap if gap > 0 else Decimal("0")

    @staticmethod
    def _add_months(date, months: int):
        month_index = date.month - 1 + months
        year = date.year + month_index // 12
        month = month_index % 12 + 1
        import calendar
        day = min(date.day, calendar.monthrange(year, month)[1])
        return date.replace(year=year, month=month, day=day)

    def build_schedule(self, months: int, first_due, start_amount=None):
        self.installments.all().delete()
        total = self.total if self.total else start_amount or Decimal("0")
        base = _q(total / months)
        remainder = _q(total - base * (months - 1))
        for i in range(months):
            amount = base if i < months - 1 else remainder
            Installment.objects.create(
                invoice=self,
                sequence=i + 1,
                due_date=self._add_months(first_due, i),
                amount=amount,
            )

    @transaction.atomic
    def issue(self, user=None):
        if self.status != self.STATUS_DRAFT:
            raise ValidationError("Only a draft invoice can be issued.")
        if not self.lines.exists():
            raise ValidationError("An invoice needs at least one line before it can be issued.")
        self.recalculate(save=False)
        if self.total <= 0:
            raise ValidationError("An invoice total must be greater than zero.")
        if not self.number:
            year = self.issue_date.year
            from .ledger_models import Sequence
            settings_row = CompanySettings.load()
            self.number = Sequence.next("invoice", year, settings_row.invoice_prefix)
        if not self.due_date:
            terms = self.client.payment_terms_days or CompanySettings.load().default_payment_terms_days
            from datetime import timedelta
            self.due_date = self.issue_date + timedelta(days=terms)
        self.status = self.STATUS_OPEN
        self.save()
        from . import posting
        posting.post_invoice(self, user)
        return self

    @transaction.atomic
    def void(self, reason: str, user=None):
        if self.status == self.STATUS_VOID:
            raise ValidationError("This invoice is already void.")
        if self.status == self.STATUS_DRAFT:
            self.status = self.STATUS_VOID
            self.void_reason = reason
            self.voided_at = timezone.now()
            self.save(update_fields=["status", "void_reason", "voided_at"])
            return self
        if self.allocations.filter(payment__voided_at__isnull=True).exists():
            raise ValidationError(
                "This invoice has payments allocated against it. Reallocate or void the "
                "payment first, or issue a credit note instead."
            )
        if self.journal_entry_id:
            self.journal_entry.reverse(timezone.localdate(), f"Void invoice {self.number}: {reason}", user)
        self.status = self.STATUS_VOID
        self.void_reason = reason
        self.voided_at = timezone.now()
        self.save(update_fields=["status", "void_reason", "voided_at"])
        return self

    def refresh_status(self):
        if self.status in (self.STATUS_DRAFT, self.STATUS_VOID):
            return
        self.status = self.STATUS_PAID if self.is_settled else self.STATUS_OPEN
        self.save(update_fields=["status"])


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    description = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.description

    def resolved_revenue_account(self) -> Account:
        if self.revenue_account_id:
            return self.revenue_account
        if self.product_id:
            return self.product.revenue_account
        raise ValidationError("An invoice line needs either a product or an explicit revenue account.")

    def save(self, *args, **kwargs):
        self.amount = _q(self.quantity * self.unit_price)
        super().save(*args, **kwargs)
        if self.invoice.status == Invoice.STATUS_DRAFT:
            self.invoice.recalculate()


class Installment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="installments")
    sequence = models.PositiveIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["sequence"]
        unique_together = ("invoice", "sequence")


class Payment(models.Model):
    METHOD_CASH = "cash"
    METHOD_BANK = "bank"
    METHOD_MOMO_MTN = "momo_mtn"
    METHOD_MOMO_AIRTEL = "momo_airtel"
    METHOD_CHEQUE = "cheque"
    METHOD_OTHER = "other"
    METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_BANK, "Bank"),
        (METHOD_MOMO_MTN, "MoMo (MTN)"),
        (METHOD_MOMO_AIRTEL, "MoMo (Airtel)"),
        (METHOD_CHEQUE, "Cheque"),
        (METHOD_OTHER, "Other"),
    ]
    METHOD_ROLE = {
        METHOD_CASH: Account.ROLE_CASH,
        METHOD_BANK: Account.ROLE_BANK,
        METHOD_MOMO_MTN: Account.ROLE_MOMO_MTN,
        METHOD_MOMO_AIRTEL: Account.ROLE_MOMO_AIRTEL,
        METHOD_CHEQUE: Account.ROLE_BANK,
        METHOD_OTHER: Account.ROLE_CASH,
    }

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="payments")
    receipt_number = models.CharField(max_length=30, blank=True)
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=METHOD_BANK)
    deposit_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name="payments_deposited"
    )
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="payment"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.receipt_number or f"Payment #{self.pk}"

    def resolved_deposit_account(self) -> Account:
        if self.deposit_account_id:
            return self.deposit_account
        return Account.by_role(self.METHOD_ROLE[self.method])

    @property
    def allocated(self) -> Decimal:
        agg = self.allocations.aggregate(t=models.Sum("amount"))
        return agg["t"] or Decimal("0")

    @property
    def unallocated(self) -> Decimal:
        return self.amount - self.allocated

    def _auto_allocate(self):
        remaining = self.amount
        open_invoices = self.client.invoices.filter(status=Invoice.STATUS_OPEN).order_by("issue_date", "id")
        allocations = []
        for invoice in open_invoices:
            if remaining <= 0:
                break
            take = min(remaining, invoice.balance)
            if take > 0:
                allocations.append((invoice, take))
                remaining -= take
        return allocations

    @transaction.atomic
    def record(self, allocations=None, user=None):
        if allocations is None:
            allocations = self._auto_allocate()
        total_allocated = Decimal("0")
        for invoice, amount in allocations:
            if invoice.client_id != self.client_id:
                raise ValidationError(f"Invoice {invoice.number} does not belong to {self.client.name}.")
            if amount > invoice.balance:
                raise ValidationError(
                    f"Cannot allocate {amount} to invoice {invoice.number} — its remaining balance is {invoice.balance}."
                )
            total_allocated += amount
        if total_allocated > self.amount:
            raise ValidationError("Total allocated cannot exceed the payment amount.")

        year = self.date.year
        settings_row = CompanySettings.load()
        if not self.receipt_number:
            from .ledger_models import Sequence
            self.receipt_number = Sequence.next("receipt", year, settings_row.receipt_prefix)
        self.save()

        for invoice, amount in allocations:
            PaymentAllocation.objects.create(payment=self, invoice=invoice, amount=amount)

        from . import posting
        posting.post_payment(self, user)

        for invoice, _ in allocations:
            invoice.refresh_status()
        return self

    @transaction.atomic
    def void(self, reason: str, user=None):
        if self.voided_at:
            raise ValidationError("This payment is already void.")
        affected_invoices = [a.invoice for a in self.allocations.select_related("invoice")]
        if self.journal_entry_id:
            self.journal_entry.reverse(timezone.localdate(), f"Void payment {self.receipt_number}: {reason}", user)
        self.allocations.all().delete()
        self.voided_at = timezone.now()
        self.void_reason = reason
        self.save(update_fields=["voided_at", "void_reason"])
        for invoice in affected_invoices:
            invoice.refresh_status()
        return self


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        unique_together = ("payment", "invoice")


class WithholdingCredit(models.Model):
    """A 6% WHT certificate from a designated withholding-agent client."""

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="withholding_credits")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="withholding_credits")
    payment = models.ForeignKey(
        Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="withholding_credits"
    )
    certificate_number = models.CharField(max_length=50, blank=True)
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    certificate = models.FileField(upload_to="books/wht_certificates/", blank=True, null=True)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="withholding_credit"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"WHT {self.amount} - {self.invoice}"

    @transaction.atomic
    def record(self, user=None):
        from . import posting
        posting.post_wht_credit(self, user)
        self.invoice.refresh_status()
        return self


class Expense(models.Model):
    METHOD_CASH = "cash"
    METHOD_BANK = "bank"
    METHOD_MOMO_MTN = "momo_mtn"
    METHOD_MOMO_AIRTEL = "momo_airtel"
    METHOD_CARD = "card"
    METHOD_OTHER = "other"
    METHOD_CHOICES = [
        (METHOD_CASH, "Cash"),
        (METHOD_BANK, "Bank"),
        (METHOD_MOMO_MTN, "MoMo (MTN)"),
        (METHOD_MOMO_AIRTEL, "MoMo (Airtel)"),
        (METHOD_CARD, "Card"),
        (METHOD_OTHER, "Other"),
    ]
    METHOD_ROLE = {
        METHOD_CASH: Account.ROLE_CASH,
        METHOD_BANK: Account.ROLE_BANK,
        METHOD_MOMO_MTN: Account.ROLE_MOMO_MTN,
        METHOD_MOMO_AIRTEL: Account.ROLE_MOMO_AIRTEL,
        METHOD_CARD: Account.ROLE_BANK,
        METHOD_OTHER: Account.ROLE_CASH,
    }

    date = models.DateField(default=timezone.localdate)
    supplier = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=255)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expenses")
    currency = models.CharField(max_length=10, default="UGX")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    fx_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("1"))
    amount_ugx = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=METHOD_BANK)
    paid_from = models.ForeignKey(
        Account, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses_paid_from"
    )
    paid_personally = models.BooleanField(
        default=False,
        help_text="Paid by the director personally — the company owes this back via the director's current account.",
    )
    reference = models.CharField(max_length=100, blank=True)
    transaction_charge = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    receipt = models.FileField(upload_to="books/receipts/", blank=True, null=True)
    receipt_hash = models.CharField(max_length=64, blank=True)
    no_receipt_reason = models.CharField(max_length=255, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    is_deductible = models.BooleanField(default=True)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="expense"
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.description} ({self.amount_ugx})"

    def clean(self):
        if self.currency != "UGX" and self.fx_rate == Decimal("1"):
            raise ValidationError("Set an explicit FX rate for a non-UGX expense — 1 is not a valid rate.")
        if not self.receipt and not self.no_receipt_reason:
            raise ValidationError("Attach a receipt, or explain why none is available.")

    def find_duplicate(self):
        if not self.receipt_hash:
            return None
        qs = Expense.objects.filter(receipt_hash=self.receipt_hash, voided_at__isnull=True).exclude(pk=self.pk)
        return qs.first()

    @property
    def total_cost_ugx(self) -> Decimal:
        return self.amount_ugx + self.transaction_charge

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if self.receipt and hasattr(self.receipt, "file"):
            self.receipt.seek(0)
            self.receipt_hash = hashlib.sha256(self.receipt.read()).hexdigest()
            self.receipt.seek(0)
        self.amount_ugx = _q(self.amount * self.fx_rate)
        if is_new and self.account_id:
            self.is_deductible = self.account.is_deductible
        super().save(*args, **kwargs)

    def resolved_source_account(self) -> Account:
        if self.paid_personally:
            return Account.by_role(Account.ROLE_DIRECTOR)
        if self.paid_from_id:
            return self.paid_from
        return Account.by_role(self.METHOD_ROLE[self.method])

    @transaction.atomic
    def record(self, user=None):
        from . import posting
        posting.post_expense(self, user)
        return self

    @transaction.atomic
    def void(self, reason: str, user=None):
        if self.voided_at:
            raise ValidationError("This expense is already void.")
        if self.journal_entry_id:
            self.journal_entry.reverse(timezone.localdate(), f"Void expense: {reason}", user)
        self.voided_at = timezone.now()
        self.void_reason = reason
        self.save(update_fields=["voided_at", "void_reason"])
        return self
