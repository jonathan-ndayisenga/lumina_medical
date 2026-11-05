"""Credit notes, real PDF downloads, sending to clients, the receipt check, the
client portal and statement matching."""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import delivery, documents, pdf
from .document_models import Client, CompanySettings, CreditNote, Expense, Invoice, Payment
from .forms import CreditNoteForm, StatementUploadForm
from .permissions import books_admin_required, books_staff_required
from .quotation_models import Quotation
from .reconciliation_models import StatementLine, import_statement, parse_statement

MAX_STATEMENT_BYTES = 5 * 1024 * 1024


def _pdf_response(content: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _receipt_context(request, payment):
    context = documents.receipt_context(payment)
    context["verify_url"] = delivery.receipt_verify_url(request, payment)
    return context


def _printable_receipt(pk):
    payment = get_object_or_404(Payment.objects.select_related("client"), pk=pk)
    if not payment.receipt_number or payment.voided_at:
        raise PermissionDenied("This payment does not have a valid receipt to print.")
    return payment


# Downloads


@books_staff_required
def invoice_download(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return _pdf_response(pdf.invoice_pdf(documents.invoice_context(invoice)), f"{invoice.number or f'draft-{invoice.pk}'}.pdf")


@books_staff_required
def receipt_download(request, pk):
    payment = _printable_receipt(pk)
    return _pdf_response(pdf.receipt_pdf(_receipt_context(request, payment)), f"{payment.receipt_number}.pdf")


@books_staff_required
def statement_download(request, pk):
    client = get_object_or_404(Client, pk=pk)
    return _pdf_response(pdf.statement_pdf(documents.statement_context(client)), f"statement-{client.pk}.pdf")


@books_staff_required
def quotation_download(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    return _pdf_response(pdf.quotation_pdf(documents.quotation_context(quotation)), f"{quotation.number}.pdf")


@books_staff_required
def credit_note_download(request, pk):
    note = get_object_or_404(CreditNote, pk=pk)
    return _pdf_response(pdf.credit_note_pdf(documents.credit_note_context(note)), f"{note.number}.pdf")


# Sending


@require_POST
@books_staff_required
def invoice_email(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client"), pk=pk)
    if invoice.status in (Invoice.STATUS_DRAFT, Invoice.STATUS_VOID):
        messages.error(request, "Only an issued invoice can be sent.")
        return redirect("books:invoice_detail", pk=invoice.pk)
    try:
        delivery.send_invoice(invoice)
        messages.success(request, f"Invoice {invoice.number} emailed to {invoice.client.email}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("books:invoice_detail", pk=invoice.pk)


@require_POST
@books_staff_required
def receipt_email(request, pk):
    payment = _printable_receipt(pk)
    try:
        delivery.send_receipt(payment, delivery.receipt_verify_url(request, payment))
        messages.success(request, f"Receipt {payment.receipt_number} emailed to {payment.client.email}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("books:receipt_list")


# Credit notes


@books_staff_required
def credit_note_create(request, invoice_pk):
    invoice = get_object_or_404(Invoice.objects.select_related("client"), pk=invoice_pk)
    form = CreditNoteForm(request.POST or None, invoice=invoice, initial={"date": timezone.localdate(), "amount": invoice.balance})
    if request.method == "POST" and form.is_valid():
        note = form.save(commit=False)
        note.invoice = invoice
        note.client = invoice.client
        try:
            note.record(user=request.user)
            messages.success(request, f"Credit note {note.number} recorded.")
            return redirect("books:invoice_detail", pk=invoice.pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
    return render(request, "books/credit_note_form.html", {"form": form, "invoice": invoice})


@books_staff_required
def credit_note_print(request, pk):
    note = get_object_or_404(CreditNote.objects.select_related("invoice", "client"), pk=pk)
    return render(request, "books/print/credit_note.html", documents.credit_note_context(note))


@books_staff_required
def credit_note_void(request, pk):
    note = get_object_or_404(CreditNote, pk=pk)
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Enter a reason for voiding this credit note.")
        else:
            try:
                note.void(reason, user=request.user)
                messages.success(request, f"Credit note {note.number} voided.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            return redirect("books:invoice_detail", pk=note.invoice_id)
    return render(request, "books/void_confirm.html", {"object_label": note.number, "cancel_url": reverse("books:invoice_detail", args=[note.invoice_id])})


# Client portal switch


@require_POST
@books_admin_required
def client_portal_toggle(request, pk):
    client = get_object_or_404(Client, pk=pk)
    if request.POST.get("action") == "disable":
        client.disable_portal()
        messages.success(request, "Client link turned off. The old link no longer works.")
    else:
        client.enable_portal()
        messages.success(request, "New client link created. Any older link has stopped working.")
    return redirect("books:client_detail", pk=client.pk)


# Public pages: no sign-in, so they show only what the holder of the link should see


def _public_page(view):
    """Never cached, never indexed, and the private address never leaks to
    another site through the Referer header."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["X-Robots-Tag"] = "noindex, nofollow"
        response["Referrer-Policy"] = "no-referrer"
        return response
    return wrapped


def _portal_client(token) -> Client:
    client = Client.objects.filter(portal_token=token, active=True).first() if token else None
    if client is None:
        raise Http404
    return client


@_public_page
def portal_home(request, token):
    client = _portal_client(token)
    return render(request, "books/portal.html", {
        "token": token,
        "client": client,
        "company": documents._company_block(),
        "invoices": client.invoices.exclude(status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_VOID]),
        "payments": client.payments.filter(voided_at__isnull=True).exclude(receipt_number=None),
        "credit_notes": client.credit_notes.filter(voided_at__isnull=True),
    })


@_public_page
def portal_invoice(request, token, pk):
    client = _portal_client(token)
    invoice = get_object_or_404(client.invoices.exclude(status__in=[Invoice.STATUS_DRAFT, Invoice.STATUS_VOID]), pk=pk)
    return _pdf_response(pdf.invoice_pdf(documents.invoice_context(invoice)), f"{invoice.number}.pdf")


@_public_page
def portal_receipt(request, token, pk):
    client = _portal_client(token)
    payment = get_object_or_404(client.payments.filter(voided_at__isnull=True).exclude(receipt_number=None), pk=pk)
    return _pdf_response(pdf.receipt_pdf(_receipt_context(request, payment)), f"{payment.receipt_number}.pdf")


@_public_page
def portal_credit_note(request, token, pk):
    client = _portal_client(token)
    note = get_object_or_404(client.credit_notes.filter(voided_at__isnull=True), pk=pk)
    return _pdf_response(pdf.credit_note_pdf(documents.credit_note_context(note)), f"{note.number}.pdf")


@_public_page
def portal_statement(request, token):
    client = _portal_client(token)
    return _pdf_response(pdf.statement_pdf(documents.statement_context(client)), "statement.pdf")


@_public_page
def receipt_verify(request, token):
    payment = delivery.payment_from_verify_token(token)
    if payment is None or not payment.receipt_number:
        return render(request, "books/receipt_verify.html", {"company": documents._company_block(), "payment": None}, status=404)
    return render(request, "books/receipt_verify.html", {"company": documents._company_block(), "payment": payment})


# Statement matching


def _reconcile_rows(lines):
    return [{"line": line, "suggestions": [] if line.is_done else line.suggestions()[:3]} for line in lines]


@books_staff_required
def reconcile(request):
    form = StatementUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        if upload.size > MAX_STATEMENT_BYTES:
            messages.error(request, "That file is too big. Statements should be under 5 MB.")
            return redirect("books:reconcile")
        try:
            rows, problems = parse_statement(upload.read())
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("books:reconcile")
        created, skipped = import_statement(form.cleaned_data["account"], rows)
        messages.success(request, f"{created} new line{'s' if created != 1 else ''} imported, {skipped} already here.")
        if problems:
            messages.warning(request, f"{len(problems)} row(s) skipped. " + " ".join(problems[:5]))
        return redirect("books:reconcile")
    show = request.GET.get("show", "open")
    lines = StatementLine.objects.select_related("account", "matched_payment__client", "matched_expense")
    open_lines = lines.filter(matched_payment__isnull=True, matched_expense__isnull=True, ignored=False)
    return render(request, "books/reconcile.html", {
        "form": form,
        "show": show,
        "open_count": open_lines.count(),
        "rows": _reconcile_rows(open_lines if show == "open" else lines),
    })


@require_POST
@books_staff_required
def reconcile_action(request, pk):
    line = get_object_or_404(StatementLine, pk=pk)
    action = request.POST.get("action")
    if action == "match":
        # Only a record the line itself suggests: same amount, same account, same week, not taken.
        target = next((row for row in line.suggestions() if str(row.pk) == request.POST.get("target")), None)
        if target is None or line.is_done:
            messages.error(request, "That match is no longer available.")
        elif isinstance(target, Payment):
            line.matched_payment = target
        elif isinstance(target, Expense):
            line.matched_expense = target
    elif action == "ignore":
        line.ignored = True
    elif action == "undo":
        line.matched_payment = line.matched_expense = None
        line.ignored = False
    line.save(update_fields=["matched_payment", "matched_expense", "ignored"])
    return redirect("books:reconcile")
