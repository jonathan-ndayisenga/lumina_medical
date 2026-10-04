from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin

from . import documents
from .forms import QuotationConvertForm, QuotationFieldForm, QuotationForm, QuotationSettingsForm
from .permissions import books_admin_required, books_staff_required
from .quotation_models import Quotation, QuotationField, QuotationFieldValue, QuotationLine, QuotationSettings


def _lines_from_post(post):
    """Item rows arrive as parallel lists (line_title, line_description,
    line_basis, line_amount); fully blank rows are ignored."""
    columns = [post.getlist(name) for name in ("line_title", "line_description", "line_basis", "line_amount")]
    rows, errors = [], []
    for number, (title, description, basis, amount_raw) in enumerate(zip(*columns), start=1):
        title, description, basis = title.strip(), description.strip(), basis.strip()
        amount_text = amount_raw.replace(",", "").strip()
        if not (title or description or basis or amount_text):
            continue
        row = {"title": title, "description": description, "basis": basis, "amount": amount_raw.strip()}
        if not title:
            errors.append(f"Item {number}: enter a description.")
        try:
            amount = Decimal(amount_text)
            if amount <= 0:
                raise InvalidOperation
            row["value"] = amount
        except (InvalidOperation, ValueError):
            errors.append(f"Item {number}: enter an amount greater than zero.")
        rows.append(row)
    if not rows and not errors:
        errors.append("Add at least one item to the quotation.")
    return rows, errors


def _editor(request, quotation=None):
    fields = list(QuotationField.objects.filter(active=True))
    if request.method == "POST":
        form = QuotationForm(request.POST, instance=quotation)
        rows, line_errors = _lines_from_post(request.POST)
        extra_values = {field.pk: request.POST.get(f"xf_{field.pk}", "").strip() for field in fields}
        if form.is_valid() and not line_errors:
            with transaction.atomic():
                quote = form.save(commit=False)
                if not quote.prepared_by_id:
                    quote.prepared_by = request.user
                quote.attention = quote.attention or quote.client.contact_person
                quote.location = quote.location or quote.client.address
                quote.save()
                quote.lines.all().delete()
                QuotationLine.objects.bulk_create([
                    QuotationLine(quotation=quote, title=row["title"], description=row["description"],
                                  basis=row["basis"], amount=row["value"], sort_order=index)
                    for index, row in enumerate(rows)
                ])
                for field in fields:
                    QuotationFieldValue.objects.update_or_create(
                        quotation=quote, field=field, defaults={"value": extra_values[field.pk]},
                    )
                quote.recalculate()
            messages.success(request, f"Quotation {quote.number} saved.")
            return redirect("books:quotation_detail", pk=quote.pk)
        for error in line_errors:
            messages.error(request, error)
    elif quotation:
        form = QuotationForm(instance=quotation)
        rows = [
            {"title": line.title, "description": line.description, "basis": line.basis, "amount": f"{line.amount:.0f}"}
            for line in quotation.lines.all()
        ]
        saved = {fv.field_id: fv.value for fv in quotation.field_values.all()}
        extra_values = {field.pk: saved.get(field.pk, "") for field in fields}
    else:
        defaults = QuotationSettings.load()
        form = QuotationForm(initial={
            "issue_date": date.today(), "currency": "UGX", "intro": defaults.intro,
            "note_left_title": defaults.note_left_title, "note_left_body": defaults.note_left_body,
            "note_right_title": defaults.note_right_title, "note_right_body": defaults.note_right_body,
        })
        rows, extra_values = [], {field.pk: "" for field in fields}
    return render(request, "books/quotation_form.html", {
        "form": form,
        "rows": rows or [{}],
        "extra_fields": [{"field": field, "value": extra_values.get(field.pk, "")} for field in fields],
        "quotation": quotation,
    })


@books_staff_required
def quotation_list(request):
    quotations = Quotation.objects.select_related("client")
    status = request.GET.get("status", "")
    if status:
        quotations = quotations.filter(status=status)
    return render(request, "books/quotation_list.html", {
        "quotations": quotations, "status": status, "statuses": Quotation.STATUS_CHOICES,
    })


@books_staff_required
def quotation_create(request):
    return _editor(request)


@books_staff_required
def quotation_edit(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    if not quotation.is_editable:
        messages.error(request, "Only draft or sent quotations can be edited.")
        return redirect("books:quotation_detail", pk=pk)
    return _editor(request, quotation)


@books_staff_required
def quotation_detail(request, pk):
    quotation = get_object_or_404(Quotation.objects.select_related("client", "invoice"), pk=pk)
    return render(request, "books/quotation_detail.html", {
        "quotation": quotation, "doc": documents.quotation_context(quotation),
    })


@books_staff_required
def quotation_status(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    new_status = request.POST.get("status")
    allowed = {Quotation.STATUS_SENT, Quotation.STATUS_ACCEPTED, Quotation.STATUS_DECLINED}
    if request.method == "POST" and new_status in allowed and quotation.status != Quotation.STATUS_CONVERTED:
        quotation.status = new_status
        quotation.save(update_fields=["status"])
        messages.success(request, f"Quotation {quotation.number} marked {quotation.get_status_display().lower()}.")
    return redirect("books:quotation_detail", pk=pk)


@books_staff_required
def quotation_convert(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    form = QuotationConvertForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            invoice = quotation.convert_to_invoice(form.cleaned_data["revenue_account"], form.cleaned_data["kind"])
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("books:quotation_detail", pk=pk)
        messages.success(request, f"Draft invoice created from {quotation.number}. Review it, then issue it.")
        return redirect("books:invoice_edit", pk=invoice.pk)
    return render(request, "books/quotation_convert.html", {"quotation": quotation, "form": form})


@xframe_options_sameorigin
@books_staff_required
def quotation_pdf(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    return render(request, "books/print/quotation.html", documents.quotation_context(quotation))


@books_admin_required
def quotation_settings(request):
    settings_row = QuotationSettings.load()
    form = QuotationSettingsForm(instance=settings_row)
    field_form = QuotationFieldForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "settings":
            form = QuotationSettingsForm(request.POST, instance=settings_row)
            if form.is_valid():
                form.save()
                messages.success(request, "Quotation settings saved.")
                return redirect("books:quotation_settings")
        elif action == "add_field":
            field_form = QuotationFieldForm(request.POST)
            if field_form.is_valid():
                field_form.save()
                messages.success(request, f"Field '{field_form.instance.label}' added to quotations.")
                return redirect("books:quotation_settings")
        elif action == "toggle_field":
            field = get_object_or_404(QuotationField, pk=request.POST.get("field_id"))
            field.active = not field.active
            field.save(update_fields=["active"])
            return redirect("books:quotation_settings")
    return render(request, "books/quotation_settings.html", {
        "form": form, "field_form": field_form, "fields": QuotationField.objects.all(),
    })
