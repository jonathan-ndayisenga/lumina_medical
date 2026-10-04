from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .document_models import Client, Invoice, InvoiceLine
from .ledger_models import Account


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

    @transaction.atomic
    def generate_due(self, today=None, user=None):
        """Issue every invoice that has fallen due (catching up missed months)
        and move next_run_date forward one month per invoice."""
        today = today or timezone.localdate()
        issued = []
        while self.active and self.next_run_date <= today and not self.is_finished:
            invoice = Invoice.objects.create(
                client=self.client, kind=Invoice.KIND_SUBSCRIPTION, issue_date=self.next_run_date,
                reference=f"Recurring: {self.description}", status=Invoice.STATUS_DRAFT,
            )
            InvoiceLine.objects.create(
                invoice=invoice, revenue_account=self.revenue_account, description=self.description,
                detail=self.detail, quantity=Decimal("1"), unit_price=self.amount,
            )
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


class ExpenseBudget(models.Model):
    """Monthly spending limit for one expense account."""

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="budget")
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    def __str__(self):
        return f"{self.account.name}: {self.monthly_amount}/month"
