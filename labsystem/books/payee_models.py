"""People and companies Ternah pays, and the payment vouchers it gives them.

A voucher is the document; the money itself is an ordinary Expense, so every
payment still shows in spending, budgets, the ledger and the reports. The
voucher has no state of its own beyond who issued it: it is void exactly when
its expense is void."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from .document_models import Client, CompanySettings, Expense
from .ledger_models import Account


class Payee(models.Model):
    KIND_PERSON = "person"
    KIND_COMPANY = "company"
    KIND_CHOICES = [(KIND_PERSON, "Person"), (KIND_COMPANY, "Company")]

    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_PERSON)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    tin = models.CharField(max_length=30, blank=True, verbose_name="TIN")
    address = models.TextField(blank=True)
    momo_number = models.CharField(max_length=30, blank=True, verbose_name="Mobile money number")
    bank_name = models.CharField(max_length=150, blank=True)
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    client = models.OneToOneField(
        Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="payee",
        help_text="If they are also one of our clients, link them so both sides show on one page.",
    )
    default_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="default_for_payees",
        verbose_name="Usual expense category", help_text="Picked for you when you pay them. You can still change it.",
    )
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def live_vouchers(self):
        return self.vouchers.filter(expense__voided_at__isnull=True)

    def total_paid(self, start=None, end=None) -> Decimal:
        qs = self.live_vouchers()
        if start:
            qs = qs.filter(expense__date__gte=start)
        if end:
            qs = qs.filter(expense__date__lte=end)
        return qs.aggregate(total=Sum("expense__amount_ugx"))["total"] or Decimal("0")

    def paid_this_year(self) -> Decimal:
        return self.total_paid(start=timezone.localdate().replace(month=1, day=1))


class PaymentVoucher(models.Model):
    number = models.CharField(max_length=30, unique=True)
    payee = models.ForeignKey(Payee, on_delete=models.PROTECT, related_name="vouchers")
    expense = models.OneToOneField(Expense, on_delete=models.PROTECT, related_name="voucher")
    received_by = models.CharField(
        max_length=150, blank=True,
        help_text="Who actually collected the money, if not the payee themselves (for example their driver).",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_vouchers_issued",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-expense__date", "-id"]

    def __str__(self):
        return self.number

    @property
    def date(self):
        return self.expense.date

    @property
    def amount(self) -> Decimal:
        return self.expense.amount_ugx

    @property
    def is_void(self) -> bool:
        return bool(self.expense.voided_at)

    @classmethod
    @transaction.atomic
    def issue(cls, payee, expense, user=None, received_by=""):
        if expense.voided_at:
            raise ValidationError("A void expense can't get a payment voucher.")
        if cls.objects.filter(expense=expense).exists():
            raise ValidationError("This expense already has a payment voucher.")
        if not payee.active:
            raise ValidationError(f"{payee.name} is marked inactive. Turn them back on before paying them.")
        from .ledger_models import Sequence
        number = Sequence.next("payment_voucher", expense.date.year, CompanySettings.load().voucher_prefix)
        if not expense.supplier:
            expense.supplier = payee.name
            expense.save(update_fields=["supplier"])
        return cls.objects.create(number=number, payee=payee, expense=expense, received_by=received_by, issued_by=user)

    @classmethod
    @transaction.atomic
    def pay(cls, payee, *, user=None, received_by="", attachment=None, **expense_fields):
        """Record the payment as an expense and issue its voucher in one go."""
        if not attachment:
            expense_fields.setdefault("no_receipt_reason", "Paid against a payment voucher.")
        expense = Expense(supplier=payee.name, currency="UGX", fx_rate=Decimal("1"), receipt=attachment or None, **expense_fields)
        expense.save()
        expense.record(user=user)
        return cls.issue(payee, expense, user=user, received_by=received_by)

    def void(self, reason: str, user=None):
        """Cancelling the voucher cancels the payment it records."""
        self.expense.void(reason, user=user)
        return self


def suggested_payee(expense):
    """The payee whose name matches what was typed as the supplier."""
    name = (expense.supplier or "").strip()
    if not name:
        return None
    return Payee.objects.filter(name__iexact=name, active=True).first()
