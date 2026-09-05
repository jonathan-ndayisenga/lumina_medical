from datetime import date

from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from . import documents, reports
from .document_models import Client, CompanySettings, Expense, Invoice, Payment, WithholdingCredit
from .forms import (
    ClientForm,
    CompanySettingsForm,
    ExpenseForm,
    InvoiceForm,
    InvoiceLineFormSet,
    ManualEntryForm,
    ManualJournalLineFormSet,
    PaymentForm,
    ScheduleForm,
)
from .ledger_models import Account, FinancialYear, JournalEntry
from .permissions import _is_books_user, books_admin_required, books_staff_required


class BooksLoginView(LoginView):
    template_name = "books/login.html"
    redirect_authenticated_user = False

    def get_success_url(self):
        return reverse("books:dashboard")

    def form_valid(self, form):
        if not _is_books_user(form.get_user()):
            form.add_error(None, "This account does not have access to Ternah Books.")
            return self.form_invalid(form)
        return super().form_valid(form)


class BooksLogoutView(LogoutView):
    next_page = reverse_lazy("books:login")


@books_staff_required
def dashboard(request):
    settings_row = CompanySettings.load()
    today = timezone.localdate()
    financial_year = FinancialYear.current()
    ageing = reports.aged_receivables(today)
    trial = reports.trial_balance()
    pnl = reports.profit_and_loss(financial_year.start_date if financial_year else None, today)

    overdue_invoices = [
        inv for inv in Invoice.objects.filter(status=Invoice.STATUS_OPEN).select_related("client")
        if inv.is_overdue
    ]
    recent_payments = Payment.objects.filter(voided_at__isnull=True).select_related("client").order_by("-date", "-id")[:10]
    missing_receipt_count = Expense.objects.filter(receipt="", voided_at__isnull=True).count()

    context = {
        "settings_row": settings_row,
        "missing_letterhead_fields": settings_row.missing_fields_for_invoicing(),
        "owed_to_us": ageing["grand_total"],
        "financial_year": financial_year,
        "revenue_this_year": pnl["total_income"],
        "spend_this_year": pnl["total_direct_cost"] + pnl["total_overhead"],
        "trial_balance_ok": trial["balanced"],
        "overdue_invoices": overdue_invoices,
        "recent_payments": recent_payments,
        "missing_receipt_count": missing_receipt_count,
    }
    return render(request, "books/dashboard.html", context)


@books_admin_required
def company_settings(request):
    settings_row = CompanySettings.load()
    if request.method == "POST":
        form = CompanySettingsForm(request.POST, request.FILES, instance=settings_row)
        if form.is_valid():
            form.save()
            messages.success(request, "Company settings updated.")
            return redirect("books:company_settings")
    else:
        form = CompanySettingsForm(instance=settings_row)
    return render(request, "books/company_settings.html", {"form": form})


@books_staff_required
def client_list(request):
    clients = list(Client.objects.filter(active=True))
    clients.sort(key=lambda c: c.balance, reverse=True)
    return render(request, "books/client_list.html", {"clients": clients})


@books_staff_required
def client_create(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save()
            messages.success(request, f"{client.name} added.")
            return redirect("books:client_detail", pk=client.pk)
    else:
        form = ClientForm(initial={"active": True})
    return render(request, "books/client_form.html", {"form": form})


@books_staff_required
def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    statement = reports.client_statement(client)
    return render(request, "books/client_detail.html", {"client": client, "statement": statement})


@books_staff_required
def client_statement_pdf(request, pk):
    client = get_object_or_404(Client, pk=pk)
    context = documents.statement_context(client)
    return render(request, "books/print/statement.html", context)


@books_staff_required
def invoice_list(request):
    invoices = Invoice.objects.select_related("client").all()
    status = request.GET.get("status")
    if status:
        invoices = invoices.filter(status=status)
    return render(request, "books/invoice_list.html", {"invoices": invoices, "status": status})


@books_staff_required
def invoice_create(request):
    if request.method == "POST":
        form = InvoiceForm(request.POST)
        invoice = Invoice(status=Invoice.STATUS_DRAFT)
        formset = InvoiceLineFormSet(request.POST, instance=invoice)
        if form.is_valid():
            invoice = form.save(commit=False)
            invoice.status = Invoice.STATUS_DRAFT
            invoice.save()
            formset = InvoiceLineFormSet(request.POST, instance=invoice)
            if formset.is_valid():
                formset.save()
                schedule_form = ScheduleForm(request.POST)
                if schedule_form.is_valid() and schedule_form.cleaned_data.get("months"):
                    invoice.recalculate()
                    invoice.build_schedule(
                        schedule_form.cleaned_data["months"],
                        schedule_form.cleaned_data["first_due"] or invoice.issue_date,
                    )
                try:
                    invoice.issue(user=request.user)
                    messages.success(request, f"Invoice {invoice.number} issued.")
                    return redirect("books:invoice_detail", pk=invoice.pk)
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("books:invoice_detail", pk=invoice.pk)
    else:
        form = InvoiceForm(initial={"issue_date": date.today()})
        formset = InvoiceLineFormSet()
    schedule_form = ScheduleForm()
    return render(request, "books/invoice_form.html", {"form": form, "formset": formset, "schedule_form": schedule_form})


@books_staff_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client", "journal_entry"), pk=pk)
    journal_lines = invoice.journal_entry.lines.select_related("account").all() if invoice.journal_entry_id else []
    return render(request, "books/invoice_detail.html", {"invoice": invoice, "journal_lines": journal_lines})


@books_staff_required
def invoice_void(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Enter a reason for voiding this invoice.")
        else:
            try:
                invoice.void(reason, user=request.user)
                messages.success(request, f"Invoice {invoice.number or invoice.pk} voided.")
                return redirect("books:invoice_detail", pk=invoice.pk)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("books:invoice_detail", pk=invoice.pk)
    return render(request, "books/void_confirm.html", {"object_label": invoice.number or f"Draft #{invoice.pk}", "cancel_url": reverse("books:invoice_detail", args=[invoice.pk])})


@books_staff_required
def invoice_pdf(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    context = documents.invoice_context(invoice)
    return render(request, "books/print/invoice.html", context)


@books_staff_required
def payment_create(request, client_pk=None):
    client = get_object_or_404(Client, pk=client_pk) if client_pk else None
    if request.method == "POST":
        form = PaymentForm(request.POST, client=client)
        if form.is_valid():
            payment = form.save(commit=False)
            invoice = form.cleaned_data.get("invoice")
            allocations = [(invoice, min(payment.amount, invoice.balance))] if invoice else None
            try:
                payment.record(allocations=allocations, user=request.user)
                messages.success(request, f"Payment {payment.receipt_number} recorded.")
                return redirect("books:client_detail", pk=payment.client_id)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
    else:
        form = PaymentForm(initial={"date": date.today()}, client=client)
    return render(request, "books/payment_form.html", {"form": form, "client": client})


@books_staff_required
def payment_void(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Enter a reason for voiding this payment.")
        else:
            try:
                payment.void(reason, user=request.user)
                messages.success(request, f"Payment {payment.receipt_number} voided.")
                return redirect("books:client_detail", pk=payment.client_id)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("books:client_detail", pk=payment.client_id)
    return render(request, "books/void_confirm.html", {"object_label": payment.receipt_number or f"Payment #{payment.pk}", "cancel_url": reverse("books:client_detail", args=[payment.client_id])})


@books_staff_required
def receipt_list(request):
    payments = Payment.objects.exclude(receipt_number="").select_related("client")
    status = request.GET.get("status", "active")
    if status == "active":
        payments = payments.filter(voided_at__isnull=True)
    elif status == "voided":
        payments = payments.filter(voided_at__isnull=False)
    q = request.GET.get("q", "").strip()
    if q:
        payments = payments.filter(
            Q(receipt_number__icontains=q) | Q(client__name__icontains=q) | Q(reference__icontains=q)
        )
    return render(request, "books/receipt_list.html", {"payments": payments, "status": status, "q": q})


@books_staff_required
def receipt_pdf(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    if not payment.receipt_number or payment.voided_at:
        raise PermissionDenied("This payment does not have a valid receipt to print.")
    context = documents.receipt_context(payment)
    return render(request, "books/print/receipt.html", context)


@books_staff_required
def expense_list(request):
    expenses = Expense.objects.select_related("account").all()
    account_id = request.GET.get("account")
    if account_id:
        expenses = expenses.filter(account_id=account_id)
    start = request.GET.get("start")
    end = request.GET.get("end")
    if start:
        expenses = expenses.filter(date__gte=start)
    if end:
        expenses = expenses.filter(date__lte=end)
    active_expenses = expenses.filter(voided_at__isnull=True)
    summary = {
        "total": sum((e.total_cost_ugx for e in active_expenses), 0),
        "deductible": sum((e.total_cost_ugx for e in active_expenses if e.is_deductible), 0),
        "charges": sum((e.transaction_charge for e in active_expenses), 0),
    }
    return render(request, "books/expense_list.html", {
        "expenses": expenses,
        "summary": summary,
        "accounts": Account.objects.filter(active=True, type=Account.TYPE_EXPENSE),
    })


@books_staff_required
def expense_create(request):
    if request.method == "POST":
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            expense = form.save(commit=False)
            duplicate = None
            expense.save()
            duplicate = expense.find_duplicate()
            if duplicate and not form.cleaned_data.get("confirm_duplicate"):
                expense.delete()
                messages.warning(
                    request,
                    f"This receipt looks identical to expense #{duplicate.pk} ({duplicate.description}). "
                    "Tick the confirmation box if this really is a separate charge.",
                )
                return render(request, "books/expense_form.html", {"form": form, "duplicate": duplicate})
            expense.record(user=request.user)
            messages.success(request, "Expense recorded.")
            if "save_and_add_another" in request.POST:
                return redirect("books:expense_create")
            return redirect("books:expense_list")
    else:
        form = ExpenseForm(initial={"date": date.today()})
    return render(request, "books/expense_form.html", {"form": form})


@books_staff_required
def expense_void(request, pk):
    expense = get_object_or_404(Expense, pk=pk)
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Enter a reason for voiding this expense.")
        else:
            expense.void(reason, user=request.user)
            messages.success(request, "Expense voided.")
            return redirect("books:expense_list")
    return render(request, "books/void_confirm.html", {"object_label": expense.description, "cancel_url": reverse("books:expense_list")})


@books_staff_required
def report_trial_balance(request):
    return render(request, "books/report_trial_balance.html", {"report": reports.trial_balance()})


@books_staff_required
def report_profit_loss(request):
    financial_year = FinancialYear.current()
    start = financial_year.start_date if financial_year else None
    return render(request, "books/report_profit_loss.html", {
        "report": reports.profit_and_loss(start, timezone.localdate()),
        "financial_year": financial_year,
    })


@books_staff_required
def report_balance_sheet(request):
    as_at = request.GET.get("as_at") or timezone.localdate()
    return render(request, "books/report_balance_sheet.html", {"report": reports.balance_sheet(as_at), "as_at": as_at})


@books_staff_required
def report_ageing(request):
    return render(request, "books/report_ageing.html", {"report": reports.aged_receivables()})


@books_staff_required
def report_tax_pack(request):
    year_id = request.GET.get("year")
    financial_year = get_object_or_404(FinancialYear, pk=year_id) if year_id else FinancialYear.current()
    if not financial_year:
        messages.error(request, "No financial year is configured yet.")
        return redirect("books:dashboard")
    return render(request, "books/report_tax_pack.html", {
        "report": reports.tax_pack(financial_year),
        "financial_years": FinancialYear.objects.all(),
    })


@books_staff_required
def journal_list(request):
    entries = JournalEntry.objects.filter(is_posted=True).prefetch_related("lines__account")
    source = request.GET.get("source")
    if source:
        entries = entries.filter(source_type=source)

    manual_form = ManualEntryForm()
    manual_formset = ManualJournalLineFormSet()
    if request.method == "POST":
        manual_form = ManualEntryForm(request.POST)
        manual_formset = ManualJournalLineFormSet(request.POST)
        if manual_form.is_valid() and manual_formset.is_valid():
            lines = []
            for line_form in manual_formset:
                cleaned = line_form.cleaned_data
                if not cleaned or not cleaned.get("account"):
                    continue
                debit = cleaned.get("debit") or 0
                credit = cleaned.get("credit") or 0
                if not debit and not credit:
                    continue
                lines.append((cleaned["account"], debit, credit, cleaned.get("narration", "")))
            try:
                from . import posting
                posting.post_manual(
                    manual_form.cleaned_data["date"],
                    manual_form.cleaned_data["memo"],
                    lines,
                    user=request.user,
                    reference=manual_form.cleaned_data.get("reference", ""),
                )
                messages.success(request, "Manual journal entry posted.")
                return redirect("books:journal_list")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))

    return render(request, "books/journal_list.html", {
        "entries": entries,
        "source": source,
        "manual_form": manual_form,
        "manual_formset": manual_formset,
    })
