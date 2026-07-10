from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Account(models.Model):
    TYPE_ASSET = "asset"
    TYPE_LIABILITY = "liability"
    TYPE_EQUITY = "equity"
    TYPE_REVENUE = "revenue"
    TYPE_EXPENSE = "expense"

    TYPE_CHOICES = [
        (TYPE_ASSET, "Asset"),
        (TYPE_LIABILITY, "Liability"),
        (TYPE_EQUITY, "Equity"),
        (TYPE_REVENUE, "Revenue"),
        (TYPE_EXPENSE, "Expense"),
    ]

    SUB_CASH = "cash"
    SUB_BANK = "bank"
    SUB_MOBILE = "mobile_money"
    SUB_RECEIVABLE = "receivable"
    SUB_PAYABLE = "payable"
    SUB_DEPOSIT = "deposit"
    SUB_TAX = "tax"
    SUB_EQUITY = "equity"
    SUB_REVENUE = "revenue"
    SUB_EXPENSE = "expense"

    SUB_TYPE_CHOICES = [
        (SUB_CASH, "Cash"),
        (SUB_BANK, "Bank"),
        (SUB_MOBILE, "Mobile Money"),
        (SUB_RECEIVABLE, "Accounts Receivable"),
        (SUB_PAYABLE, "Accounts Payable"),
        (SUB_DEPOSIT, "Patient Deposits"),
        (SUB_TAX, "Tax Payable"),
        (SUB_EQUITY, "Equity"),
        (SUB_REVENUE, "Revenue"),
        (SUB_EXPENSE, "Operating Expense"),
    ]

    hospital = models.ForeignKey(
        "accounts.Hospital",
        on_delete=models.CASCADE,
        related_name="chart_of_accounts",
    )
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=150)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    sub_type = models.CharField(max_length=20, choices=SUB_TYPE_CHOICES)
    is_system = models.BooleanField(
        default=False,
        help_text="System accounts are auto-created and cannot be deleted.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]
        unique_together = ("hospital", "code")

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def balance(self):
        """Running balance: assets/expenses are debit-normal; liabilities/equity/revenue are credit-normal."""
        agg = self.journal_lines.aggregate(
            d=models.Sum("debit"), c=models.Sum("credit")
        )
        debits = agg["d"] or Decimal("0")
        credits = agg["c"] or Decimal("0")
        if self.account_type in (self.TYPE_ASSET, self.TYPE_EXPENSE):
            return debits - credits
        return credits - debits

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValidationError("System accounts cannot be deleted.")
        super().delete(*args, **kwargs)


class JournalEntry(models.Model):
    SOURCE_VISIT_CHARGE = "visit_charge"
    SOURCE_PAYMENT = "payment"
    SOURCE_EXPENSE = "expense"
    SOURCE_MANUAL = "manual"
    SOURCE_REVERSAL = "reversal"

    SOURCE_CHOICES = [
        (SOURCE_VISIT_CHARGE, "Visit Charge"),
        (SOURCE_PAYMENT, "Payment Receipt"),
        (SOURCE_EXPENSE, "Expense"),
        (SOURCE_MANUAL, "Manual Entry"),
        (SOURCE_REVERSAL, "Reversal"),
    ]

    hospital = models.ForeignKey(
        "accounts.Hospital",
        on_delete=models.CASCADE,
        related_name="journal_entries",
    )
    reference = models.CharField(max_length=50, unique=True, blank=True)
    date = models.DateField(default=timezone.localdate)
    description = models.CharField(max_length=300)
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    source_visit_service = models.ForeignKey(
        "reception.VisitService",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries",
    )
    source_payment = models.ForeignKey(
        "reception.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries",
    )
    source_expense = models.ForeignKey(
        "admin_dashboard.Expense",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="journal_entries",
    )
    is_reversal = models.BooleanField(default=False)
    reversed_entry = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversal_of",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.reference} — {self.description}"

    def save(self, *args, **kwargs):
        if not self.reference:
            stamp = timezone.localdate().strftime("%Y%m%d")
            last = JournalEntry.objects.filter(
                reference__startswith=f"JNL-{stamp}"
            ).count()
            self.reference = f"JNL-{stamp}-{str(last + 1).zfill(4)}"
        super().save(*args, **kwargs)

    def is_balanced(self):
        agg = self.lines.aggregate(d=models.Sum("debit"), c=models.Sum("credit"))
        return (agg["d"] or Decimal("0")) == (agg["c"] or Decimal("0"))

    def total_debit(self):
        return self.lines.aggregate(t=models.Sum("debit"))["t"] or Decimal("0")


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        side = f"DR {self.debit}" if self.debit else f"CR {self.credit}"
        return f"{self.account.code} {side}"

    def clean(self):
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debit and credit amounts cannot be negative.")
        if self.debit > 0 and self.credit > 0:
            raise ValidationError("A journal line cannot have both debit and credit.")
        if self.debit == 0 and self.credit == 0:
            raise ValidationError("A journal line must have either a debit or a credit.")
