from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .forms import _style
from .ledger_models import Account
from .permissions import books_admin_required, books_staff_required
from .planning_models import ExpenseBudget, RecurringInvoice


class RecurringInvoiceForm(forms.ModelForm):
    class Meta:
        model = RecurringInvoice
        fields = ["client", "description", "detail", "amount", "revenue_account", "next_run_date", "end_date"]
        labels = {"next_run_date": "First / next invoice date", "end_date": "Last invoice date (optional)"}
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
    form = RecurringInvoiceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        messages.success(request, f"Monthly invoice set up for {plan.client.name}, starting {plan.next_run_date:%d %b %Y}.")
        return redirect("books:recurring_list")
    plans = RecurringInvoice.objects.select_related("client", "revenue_account")
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
            messages.success(request, f"Issued {len(issued)} invoice{'s' if len(issued) != 1 else ''}: {', '.join(i.number for i in issued)}.")
        elif not errors:
            messages.info(request, "No monthly invoices are due today.")
        for error in errors:
            messages.error(request, error)
    return redirect(request.POST.get("next") or "books:recurring_list")


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
