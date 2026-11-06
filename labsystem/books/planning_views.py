from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .document_models import Invoice
from .forms import _style
from .ledger_models import Account
from .permissions import books_admin_required, books_staff_required
from .planning_models import (
    ExpenseBudget, RecurringInvoice, RecurringInvoiceLine, invoices_in_month, plan_from_invoice, repeat_invoice,
)


class RecurringInvoiceForm(forms.ModelForm):
    class Meta:
        model = RecurringInvoice
        fields = ["client", "description", "detail", "amount", "revenue_account", "next_run_date", "end_date", "issue_mode"]
        labels = {"next_run_date": "First / next invoice date", "end_date": "Last invoice date (optional)", "issue_mode": "Each month"}
        widgets = {
            "next_run_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "detail": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .document_models import Client
        self.fields["client"].queryset = Client.objects.filter(active=True)
        self.fields["revenue_account"].queryset = Account.objects.filter(active=True, type=Account.TYPE_INCOME)
        _style(self.fields)


@books_staff_required
def recurring_list(request):
    form = RecurringInvoiceForm(request.POST or None, initial={"issue_mode": RecurringInvoice.MODE_DRAFT})
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        messages.success(request, f"Monthly invoice set up for {plan.client.name}, starting {plan.next_run_date:%d %b %Y}.")
        return redirect("books:recurring_list")
    plans = RecurringInvoice.objects.select_related("client", "revenue_account", "source_invoice").prefetch_related("lines")
    return render(request, "books/recurring_list.html", {
        "form": form, "plans": plans, "due_count": sum(1 for plan in plans if plan.is_due),
    })


@books_staff_required
def recurring_toggle(request, pk):
    plan = get_object_or_404(RecurringInvoice, pk=pk)
    if request.method == "POST":
        plan.active = not plan.active
        plan.save(update_fields=["active"])
        messages.success(request, f"{plan} {'resumed' if plan.active else 'paused'}.")
    return redirect("books:recurring_list")


@books_staff_required
def recurring_generate(request):
    if request.method == "POST":
        issued, errors = RecurringInvoice.generate_all_due(user=request.user)
        if issued:
            ready = [i for i in issued if i.number]
            drafts = [i for i in issued if not i.number]
            if ready:
                messages.success(request, f"Issued {len(ready)} invoice{'s' if len(ready) != 1 else ''}: {', '.join(i.number for i in ready)}.")
            if drafts:
                messages.success(request, f"{len(drafts)} draft invoice{'s' if len(drafts) != 1 else ''} ready for you to check and issue under Invoices > Draft.")
        elif not errors:
            messages.info(request, "No monthly invoices are due today.")
        for error in errors:
            messages.error(request, error)
    return redirect(request.POST.get("next") or "books:recurring_list")


class PlanStartForm(forms.Form):
    next_run_date = forms.DateField(label="First invoice from this plan", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="Last invoice date (optional)", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    issue_mode = forms.ChoiceField(label="Each month", choices=RecurringInvoice.MODE_CHOICES, initial=RecurringInvoice.MODE_DRAFT)
    confirm_month = forms.BooleanField(required=False, label="Yes, start anyway. It's a separate charge for that month.")

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        _style(self.fields)
        self.fields["confirm_month"].widget.attrs.pop("class", None)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("next_run_date")
        if start and cleaned.get("end_date") and cleaned["end_date"] < start:
            self.add_error("end_date", "The last invoice date can't be before the first.")
        if start and start <= self.invoice.issue_date:
            self.add_error("next_run_date", f"This invoice already covers {self.invoice.issue_date:%d %b %Y}. Start after it, or the client is billed twice.")
        if start and not cleaned.get("confirm_month"):
            clash = invoices_in_month(self.invoice.client, start).first()
            if clash:
                self.add_error("next_run_date", f"{self.invoice.client.name} already has {clash.number} dated {clash.issue_date:%d %b %Y} in that month. Pick a later month, or tick the box below if it really is a separate charge.")
        return cleaned


def _issued_invoice(pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client"), pk=pk)
    if invoice.status not in (Invoice.STATUS_OPEN, Invoice.STATUS_PAID):
        raise ValidationError("Only an issued invoice can be repeated.")
    return invoice


@require_POST
@books_staff_required
def invoice_repeat(request, pk):
    try:
        invoice = _issued_invoice(pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("books:invoice_detail", pk=pk)
    copy = repeat_invoice(invoice)
    messages.success(request, f"Draft copy of {invoice.number} made. Check it, then issue it.")
    return redirect("books:invoice_edit", pk=copy.pk)


@books_staff_required
def invoice_make_plan(request, pk):
    try:
        invoice = _issued_invoice(pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("books:invoice_detail", pk=pk)
    latest = invoice.client.invoices.exclude(status__in=[Invoice.STATUS_VOID, Invoice.STATUS_DRAFT]).order_by("-issue_date", "-id").first()
    form = PlanStartForm(request.POST or None, invoice=invoice, initial={"next_run_date": Invoice._add_months(invoice.issue_date, 1)})
    if request.method == "POST" and form.is_valid():
        plan = plan_from_invoice(invoice, form.cleaned_data["next_run_date"], form.cleaned_data["end_date"], form.cleaned_data["issue_mode"])
        messages.success(request, f"Monthly plan made for {plan.client.name}, starting {plan.next_run_date:%d %b %Y}.")
        return redirect("books:recurring_list")
    return render(request, "books/plan_from_invoice.html", {
        "invoice": invoice, "form": form, "latest": latest,
        "existing_plans": invoice.client.recurring_invoices.filter(active=True),
    })


class PlanLineForm(forms.ModelForm):
    class Meta:
        model = RecurringInvoiceLine
        fields = ["description", "quantity", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


class PlanEditForm(forms.ModelForm):
    class Meta:
        model = RecurringInvoice
        fields = ["next_run_date", "end_date", "issue_mode"]
        labels = RecurringInvoiceForm.Meta.labels
        widgets = {"next_run_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self.fields)


PlanLineFormSet = inlineformset_factory(RecurringInvoice, RecurringInvoiceLine, form=PlanLineForm, extra=0, can_delete=False)


@books_staff_required
def recurring_edit(request, pk):
    """Change the price, quantity or dates on a plan. Invoices already made stay as they were."""
    plan = get_object_or_404(RecurringInvoice.objects.select_related("client"), pk=pk)
    form = PlanEditForm(request.POST or None, instance=plan)
    formset = PlanLineFormSet(request.POST or None, instance=plan) if plan.lines.exists() else None
    if request.method == "POST" and form.is_valid() and (formset is None or formset.is_valid()):
        plan = form.save()
        if formset is not None:
            formset.save()
            plan.amount = plan.monthly_total
            plan.save(update_fields=["amount"])
        messages.success(request, f"Plan for {plan.client.name} updated. Invoices already made are unchanged.")
        return redirect("books:recurring_list")
    return render(request, "books/recurring_edit.html", {"plan": plan, "form": form, "formset": formset})


@books_admin_required
def budgets(request):
    accounts = list(Account.objects.filter(active=True, type=Account.TYPE_EXPENSE).order_by("code"))
    existing = {budget.account_id: budget for budget in ExpenseBudget.objects.all()}
    if request.method == "POST":
        bad = []
        for account in accounts:
            raw = request.POST.get(f"budget_{account.pk}", "").replace(",", "").strip()
            try:
                amount = Decimal(raw) if raw else Decimal("0")
                if amount < 0:
                    raise InvalidOperation
            except InvalidOperation:
                bad.append(account.name)
                continue
            ExpenseBudget.objects.update_or_create(account=account, defaults={"monthly_amount": amount})
        if bad:
            messages.error(request, "Enter a number (or leave blank) for: " + ", ".join(bad))
        else:
            messages.success(request, "Monthly budgets saved.")
        return redirect("books:budgets")
    rows = [{"account": account, "amount": existing[account.pk].monthly_amount if account.pk in existing else ""} for account in accounts]
    return render(request, "books/budgets.html", {"rows": rows})
