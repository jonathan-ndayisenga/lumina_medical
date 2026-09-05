from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


class Sequence(models.Model):
    """Gapless document numbering (invoice/receipt/...), locked per (key, year)."""

    key = models.CharField(max_length=30)
    year = models.PositiveIntegerField()
    prefix = models.CharField(max_length=20)
    last_value = models.PositiveIntegerField(default=0)
    padding = models.PositiveSmallIntegerField(default=4)

    class Meta:
        unique_together = ("key", "year")

    def __str__(self):
        return f"{self.key}/{self.year} -> {self.last_value}"

    @classmethod
    def next(cls, key: str, year: int, prefix: str, padding: int = 4) -> str:
        """Atomically claim the next number in this sequence. Row-locked —
        counting existing documents is not safe under concurrency."""
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(
                key=key,
                year=year,
                defaults={"prefix": prefix, "padding": padding, "last_value": 0},
            )
            seq.last_value += 1
            seq.prefix = prefix
            seq.padding = padding
            seq.save(update_fields=["last_value", "prefix", "padding"])
            return f"{prefix}-{year}-{str(seq.last_value).zfill(padding)}"


class FinancialYear(models.Model):
    """Uganda's tax year: 1 July -> 30 June."""

    label = models.CharField(max_length=20, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.label

    @classmethod
    def for_date(cls, date):
        return cls.objects.filter(start_date__lte=date, end_date__gte=date).first()

    @classmethod
    def current(cls):
        from django.utils import timezone
        return cls.for_date(timezone.localdate())


class Account(models.Model):
    """One row per chart-of-accounts ledger account."""

    TYPE_ASSET = "asset"
    TYPE_LIABILITY = "liability"
    TYPE_EQUITY = "equity"
    TYPE_INCOME = "income"
    TYPE_EXPENSE = "expense"
    TYPE_CHOICES = [
        (TYPE_ASSET, "Asset"),
        (TYPE_LIABILITY, "Liability"),
        (TYPE_EQUITY, "Equity"),
        (TYPE_INCOME, "Income"),
        (TYPE_EXPENSE, "Expense"),
    ]

    ROLE_AR = "ar"
    ROLE_AP = "ap"
    ROLE_WHT = "wht"
    ROLE_VAT = "vat"
    ROLE_DEFERRED = "deferred"
    ROLE_CREDITS = "credits"
    ROLE_BANK = "bank"
    ROLE_CASH = "cash"
    ROLE_MOMO_MTN = "momo_mtn"
    ROLE_MOMO_AIRTEL = "momo_airtel"
    ROLE_DIRECTOR = "director"
    ROLE_RETAINED = "retained"
    ROLE_FX = "fx"
    ROLE_CHARGES = "charges"
    ROLE_CHOICES = [
        (ROLE_AR, "Accounts receivable"),
        (ROLE_AP, "Accounts payable"),
        (ROLE_WHT, "Withholding tax receivable"),
        (ROLE_VAT, "VAT payable"),
        (ROLE_DEFERRED, "Deferred revenue"),
        (ROLE_CREDITS, "Customer credits"),
        (ROLE_BANK, "Bank"),
        (ROLE_CASH, "Cash"),
        (ROLE_MOMO_MTN, "MoMo MTN"),
        (ROLE_MOMO_AIRTEL, "MoMo Airtel"),
        (ROLE_DIRECTOR, "Director's current account"),
        (ROLE_RETAINED, "Retained earnings"),
        (ROLE_FX, "FX gain/loss"),
        (ROLE_CHARGES, "Bank/MoMo transaction charges"),
    ]

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, blank=True)
    is_deductible = models.BooleanField(default=True)
    is_direct_cost = models.BooleanField(default=False)
    is_payment_account = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def is_debit_normal(self) -> bool:
        return self.type in (self.TYPE_ASSET, self.TYPE_EXPENSE)

    @classmethod
    def by_role(cls, role: str) -> "Account":
        account = cls.objects.filter(role=role, active=True).first()
        if not account:
            raise ValidationError(
                f"No active account is configured with role '{role}'. "
                "Set this role on exactly one account in the chart of accounts."
            )
        return account

    def balance(self, start=None, end=None) -> Decimal:
        lines = self.journal_lines.filter(entry__is_posted=True)
        if start is not None:
            lines = lines.filter(entry__date__gte=start)
        if end is not None:
            lines = lines.filter(entry__date__lte=end)
        agg = lines.aggregate(d=models.Sum("debit"), c=models.Sum("credit"))
        debit = agg["d"] or Decimal("0")
        credit = agg["c"] or Decimal("0")
        return (debit - credit) if self.is_debit_normal else (credit - debit)


class JournalEntry(models.Model):
    """A balanced set of debit/credit lines. Never edited or deleted once posted — only reversed."""

    SOURCE_INVOICE = "invoice"
    SOURCE_PAYMENT = "payment"
    SOURCE_EXPENSE = "expense"
    SOURCE_WHT = "wht"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_INVOICE, "Invoice"),
        (SOURCE_PAYMENT, "Payment"),
        (SOURCE_EXPENSE, "Expense"),
        (SOURCE_WHT, "Withholding credit"),
        (SOURCE_MANUAL, "Manual entry"),
    ]

    date = models.DateField()
    memo = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=50, blank=True)
    source_type = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    source_id = models.PositiveIntegerField(null=True, blank=True)
    financial_year = models.ForeignKey(
        FinancialYear, on_delete=models.PROTECT, null=True, blank=True, related_name="journal_entries"
    )
    is_posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "journal entries"

    def __str__(self):
        return f"{self.date} {self.memo or self.source_type}"

    def add(self, account: Account, debit: Decimal = Decimal("0"), credit: Decimal = Decimal("0"), narration: str = ""):
        if self.is_posted:
            raise ValidationError("Cannot add lines to an already-posted journal entry.")
        debit = Decimal(debit or 0)
        credit = Decimal(credit or 0)
        if debit and credit:
            raise ValidationError("A journal line cannot have both a debit and a credit.")
        if not debit and not credit:
            raise ValidationError("A journal line must have either a debit or a credit.")
        self.save()  # ensure a pk exists for the line's FK
        self.lines.create(account=account, debit=debit, credit=credit, narration=narration)
        return self

    def total_debit(self) -> Decimal:
        return self.lines.aggregate(t=models.Sum("debit"))["t"] or Decimal("0")

    def total_credit(self) -> Decimal:
        return self.lines.aggregate(t=models.Sum("credit"))["t"] or Decimal("0")

    @transaction.atomic
    def post(self, user=None):
        if self.is_posted:
            raise ValidationError("This journal entry has already been posted.")
        total_debit = self.total_debit()
        total_credit = self.total_credit()
        if total_debit != total_credit or total_debit <= 0:
            raise ValidationError(
                f"Journal entry does not balance (debits={total_debit}, credits={total_credit})."
            )
        financial_year = FinancialYear.for_date(self.date)
        if financial_year and financial_year.is_closed:
            raise ValidationError(f"Financial year {financial_year.label} is closed to new postings.")
        from django.utils import timezone
        self.financial_year = financial_year
        self.is_posted = True
        self.posted_at = timezone.now()
        if user is not None:
            self.created_by = user
        self.save(update_fields=["financial_year", "is_posted", "posted_at", "created_by"])
        return self

    @transaction.atomic
    def reverse(self, date, memo: str = "", user=None) -> "JournalEntry":
        if not self.is_posted:
            raise ValidationError("Cannot reverse a journal entry that was never posted.")
        if hasattr(self, "reversed_by") and self.reversed_by_id:
            raise ValidationError("This journal entry has already been reversed.")
        mirror = JournalEntry.objects.create(
            date=date,
            memo=memo or f"Reversal of: {self.memo}",
            reference=self.reference,
            source_type=self.source_type,
            source_id=self.source_id,
            reverses=self,
        )
        for line in self.lines.all():
            mirror.add(line.account, debit=line.credit, credit=line.debit, narration=line.narration)
        mirror.post(user)
        return mirror


class JournalLine(models.Model):
    """One debit or credit row within a journal entry."""

    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    narration = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account.code} {side}"

    def clean(self):
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debit and credit amounts cannot be negative.")
        if self.debit and self.credit:
            raise ValidationError("A journal line cannot have both a debit and a credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal line must have either a debit or a credit.")
