from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .document_models import Client, Invoice, InvoiceLine, Product
from .ledger_models import Account


class ReminderLog(models.Model):
    """One row per reminder emailed, so a daily job never sends the same one twice."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="reminders")
    stage = models.CharField(max_length=20)
    sent_to = models.EmailField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        unique_together = ("invoice", "stage")

    def __str__(self):
        return f"{self.invoice} {self.stage}"


class RecurringInvoice(models.Model):
    """A monthly charge (e.g. hosting at UGX 350,000/month) that Books turns
    into an issued invoice on each due date."""

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="recurring_invoices")
    description = models.CharField(max_length=255, help_text="Shown on each invoice, e.g. Hosting & maintenance")
    detail = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="recurring_invoices")
    next_run_date = models.DateField(help_text="Date of the next invoice, e.g. the day the free period ends.")
    end_date = models.DateField(null=True, blank=True, help_text="Optional last billing date.")
    active = models.BooleanField(default=True)
    MODE_DRAFT = "draft"
    MODE_ISSUE = "issue"
    MODE_CHOICES = [
        (MODE_DRAFT, "Prepare a draft for me to check and issue"),
        (MODE_ISSUE, "Issue it automatically"),
    ]
    # Plans made before this choice existed keep issuing, as they always did.
    issue_mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=MODE_ISSUE)
    source_invoice = models.ForeignKey(
        Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="plans_made_from",
        help_text="The invoice this plan was copied from.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_run_date", "id"]

    def __str__(self):
        return f"{self.client.name}: {self.description}"

    @property
    def is_due(self) -> bool:
        return self.active and self.next_run_date <= timezone.localdate() and not self.is_finished

    @property
    def is_finished(self) -> bool:
        return bool(self.end_date and self.next_run_date > self.end_date)

    @property
    def monthly_total(self) -> Decimal:
        lines = list(self.lines.all())
        if not lines:
            return self.amount
        return sum((line.quantity * line.unit_price for line in lines), Decimal("0"))

    def _invoice_lines(self) -> list:
        lines = list(self.lines.all())
        if lines:
            return [
                {"product": line.product, "revenue_account": line.revenue_account, "description": line.description,
                 "detail": line.detail, "quantity": line.quantity, "unit_price": line.unit_price}
                for line in lines
            ]
        # Plans set up before plans had their own lines: the one line they describe.
        return [{"product": None, "revenue_account": self.revenue_account, "description": self.description,
                 "detail": self.detail, "quantity": Decimal("1"), "unit_price": self.amount}]

    @transaction.atomic
    def generate_due(self, today=None, user=None):
        """Make every invoice that has fallen due (catching up missed months)
        and move next_run_date forward one month per invoice. In draft mode
        each one waits, unnumbered and unposted, for someone to check and issue it."""
        today = today or timezone.localdate()
        issued = []
        while self.active and self.next_run_date <= today and not self.is_finished:
            source = self.source_invoice
            invoice = Invoice.objects.create(
                client=self.client, kind=Invoice.KIND_SUBSCRIPTION, issue_date=self.next_run_date,
                reference=f"Recurring: {self.description}"[:100], status=Invoice.STATUS_DRAFT,
                currency=source.currency if source else "UGX", apply_vat=source.apply_vat if source else True,
            )
            for index, line in enumerate(self._invoice_lines()):
                InvoiceLine.objects.create(invoice=invoice, sort_order=index, **line)
            if self.issue_mode == self.MODE_ISSUE:
                invoice.issue(user=user)
            issued.append(invoice)
            self.next_run_date = Invoice._add_months(self.next_run_date, 1)
            self.save(update_fields=["next_run_date"])
        return issued

    @classmethod
    def generate_all_due(cls, today=None, user=None):
        issued, errors = [], []
        for plan in cls.objects.filter(active=True, next_run_date__lte=today or timezone.localdate()).select_related("client"):
            try:
                issued += plan.generate_due(today, user)
            except ValidationError as exc:
                errors.append(f"{plan}: {'; '.join(exc.messages)}")
        return issued, errors


class RecurringInvoiceLine(models.Model):
    plan = models.ForeignKey(RecurringInvoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.description


def plan_from_invoice(invoice, next_run_date, end_date=None, issue_mode=RecurringInvoice.MODE_DRAFT) -> RecurringInvoice:
    """A monthly plan that repeats this invoice's lines and prices."""
    lines = list(invoice.lines.all())
    if not lines:
        raise ValidationError("This invoice has no lines to repeat.")
    first = lines[0]
    with transaction.atomic():
        plan = RecurringInvoice.objects.create(
            client=invoice.client,
            description=first.description if len(lines) == 1 else f"{first.description} and {len(lines) - 1} more",
            detail=first.detail if len(lines) == 1 else "",
            amount=invoice.subtotal,
            revenue_account=first.resolved_revenue_account(),
            next_run_date=next_run_date, end_date=end_date, issue_mode=issue_mode, source_invoice=invoice,
        )
        for index, line in enumerate(lines):
            RecurringInvoiceLine.objects.create(
                plan=plan, product=line.product, revenue_account=line.revenue_account, description=line.description,
                detail=line.detail, quantity=line.quantity, unit_price=line.unit_price, sort_order=index,
            )
    return plan


def repeat_invoice(invoice) -> Invoice:
    """A draft copy of an issued invoice, dated today, for one-off repeats."""
    with transaction.atomic():
        copy = Invoice.objects.create(
            client=invoice.client, kind=invoice.kind, issue_date=timezone.localdate(), currency=invoice.currency,
            apply_vat=invoice.apply_vat, notes=invoice.notes, reference=f"Repeat of {invoice.number}"[:100],
            status=Invoice.STATUS_DRAFT,
        )
        for line in invoice.lines.all():
            InvoiceLine.objects.create(
                invoice=copy, product=line.product, revenue_account=line.revenue_account, description=line.description,
                detail=line.detail, quantity=line.quantity, unit_price=line.unit_price, sort_order=line.sort_order,
            )
    return copy


def invoices_in_month(client, day):
    """Issued invoices already sent to this client in the month of `day`."""
    return client.invoices.filter(issue_date__year=day.year, issue_date__month=day.month).exclude(
        status__in=[Invoice.STATUS_VOID, Invoice.STATUS_DRAFT],
    )


class ExpenseBudget(models.Model):
    """Monthly spending limit for one expense account."""

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="budget")
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    def __str__(self):
        return f"{self.account.name}: {self.monthly_amount}/month"
