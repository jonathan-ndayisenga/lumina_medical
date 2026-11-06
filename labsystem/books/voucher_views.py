"""Payees and payment vouchers: paying the people who work for Ternah and
giving them a proper document for it."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from . import delivery, documents, pdf
from .collection_views import _pdf_response, _public_page
from .document_models import Expense
from .forms import PayeeForm, PaymentVoucherForm, VoucherForExpenseForm
from .payee_models import Payee, PaymentVoucher, suggested_payee
from .permissions import books_staff_required


def _dates(request):
    return parse_date(request.GET.get("start") or ""), parse_date(request.GET.get("end") or "")


# Payees


@books_staff_required
def payee_list(request):
    query = request.GET.get("q", "").strip()
    payees = Payee.objects.all()
    if query:
        payees = payees.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(contact_person__icontains=query))
    if request.GET.get("show") != "all":
        payees = payees.filter(active=True)
    year_start = timezone.localdate().replace(month=1, day=1)
    payees = payees.annotate(
        paid_year=Sum("vouchers__expense__amount_ugx", filter=Q(vouchers__expense__voided_at__isnull=True, vouchers__expense__date__gte=year_start)),
    )
    return render(request, "books/payee_list.html", {"payees": payees, "query": query, "show": request.GET.get("show", "")})


def _payee_form_page(request, form, payee=None):
    return render(request, "books/payee_form.html", {"form": form, "payee": payee, "next_expense": request.GET.get("expense", "")})


def _after_payee_saved(request, payee):
    expense_pk = request.GET.get("expense", "")
    if expense_pk.isdigit():
        return redirect(reverse("books:voucher_for_expense", args=[int(expense_pk)]) + f"?payee={payee.pk}")
    return redirect("books:payee_detail", pk=payee.pk)


@books_staff_required
def payee_create(request):
    form = PayeeForm(request.POST or None, initial={"name": request.GET.get("name", "")[:200]})
    if request.method == "POST" and form.is_valid():
        payee = form.save()
        messages.success(request, f"{payee.name} added.")
        return _after_payee_saved(request, payee)
    return _payee_form_page(request, form)


@books_staff_required
def payee_edit(request, pk):
    payee = get_object_or_404(Payee, pk=pk)
    form = PayeeForm(request.POST or None, instance=payee)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("books:payee_detail", pk=payee.pk)
    return _payee_form_page(request, form, payee)


@books_staff_required
def payee_detail(request, pk):
    payee = get_object_or_404(Payee.objects.select_related("client", "default_account"), pk=pk)
    start, end = _dates(request)
    vouchers = payee.vouchers.select_related("expense", "expense__account")
    if start:
        vouchers = vouchers.filter(expense__date__gte=start)
    if end:
        vouchers = vouchers.filter(expense__date__lte=end)
    return render(request, "books/payee_detail.html", {
        "payee": payee,
        "vouchers": vouchers,
        "start": start,
        "end": end,
        "period_total": payee.total_paid(start, end),
        "paid_this_year": payee.paid_this_year(),
    })


@books_staff_required
def payee_statement_download(request, pk):
    payee = get_object_or_404(Payee, pk=pk)
    start, end = _dates(request)
    return _pdf_response(pdf.payee_statement_pdf(documents.payee_statement_context(payee, start, end)), f"payments-{payee.pk}.pdf")


# Vouchers


@books_staff_required
def voucher_list(request):
    query = request.GET.get("q", "").strip()
    start, end = _dates(request)
    vouchers = PaymentVoucher.objects.select_related("payee", "expense", "expense__account")
    if query:
        vouchers = vouchers.filter(Q(number__icontains=query) | Q(payee__name__icontains=query) | Q(expense__description__icontains=query))
    if start:
        vouchers = vouchers.filter(expense__date__gte=start)
    if end:
        vouchers = vouchers.filter(expense__date__lte=end)
    total = vouchers.filter(expense__voided_at__isnull=True).aggregate(t=Sum("expense__amount_ugx"))["t"] or 0
    return render(request, "books/voucher_list.html", {
        "vouchers": vouchers, "total": total, "query": query, "start": start, "end": end,
        "without_voucher": Expense.objects.filter(voided_at__isnull=True, voucher__isnull=True).count(),
    })


@books_staff_required
def voucher_create(request):
    payee = Payee.objects.filter(pk=request.GET.get("payee") or 0, active=True).first()
    initial = {"date": timezone.localdate(), "method": Expense.METHOD_CASH}
    if payee:
        initial["payee"] = payee
        if payee.default_account_id:
            initial["account"] = payee.default_account
    form = PaymentVoucherForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            voucher = PaymentVoucher.pay(
                form.cleaned_data["payee"], user=request.user,
                received_by=form.cleaned_data.get("received_by", ""),
                attachment=form.cleaned_data.get("attachment"),
                **form.expense_fields(),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Payment recorded. Voucher {voucher.number} is ready to print or send.")
            return redirect("books:voucher_detail", pk=voucher.pk)
    defaults = {str(p.pk): p.default_account_id for p in Payee.objects.filter(active=True).exclude(default_account=None)}
    return render(request, "books/voucher_form.html", {"form": form, "account_defaults": defaults})


@books_staff_required
def voucher_for_expense(request, pk):
    expense = get_object_or_404(Expense.objects.select_related("account"), pk=pk)
    if expense.voided_at or hasattr(expense, "voucher"):
        messages.error(request, "This expense is void or already has a voucher.")
        return redirect("books:expense_list")
    chosen = Payee.objects.filter(pk=request.GET.get("payee") or 0, active=True).first() or suggested_payee(expense)
    form = VoucherForExpenseForm(request.POST or None, initial={"payee": chosen})
    if request.method == "POST" and form.is_valid():
        try:
            voucher = PaymentVoucher.issue(form.cleaned_data["payee"], expense, user=request.user, received_by=form.cleaned_data["received_by"])
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, f"Voucher {voucher.number} issued.")
            return redirect("books:voucher_detail", pk=voucher.pk)
    return render(request, "books/voucher_for_expense.html", {"form": form, "expense": expense, "suggested": chosen})


def _voucher(pk):
    return get_object_or_404(PaymentVoucher.objects.select_related("payee", "expense", "expense__account", "issued_by"), pk=pk)


@books_staff_required
def voucher_detail(request, pk):
    voucher = _voucher(pk)
    verify_url = delivery.voucher_verify_url(request, voucher)
    return render(request, "books/voucher_detail.html", {
        "voucher": voucher,
        "email_ready": delivery.email_is_configured() and bool(voucher.payee.email),
        "whatsapp_url": delivery.voucher_whatsapp_url(voucher, verify_url) if voucher.payee.phone and not voucher.is_void else "",
    })


@books_staff_required
def voucher_print(request, pk):
    voucher = _voucher(pk)
    context = documents.voucher_context(voucher)
    context["qr_svg"] = mark_safe(pdf.qr_svg(delivery.voucher_verify_url(request, voucher)))
    return render(request, "books/print/voucher.html", context)


@books_staff_required
def voucher_download(request, pk):
    voucher = _voucher(pk)
    context = documents.voucher_context(voucher)
    context["verify_url"] = delivery.voucher_verify_url(request, voucher)
    return _pdf_response(pdf.voucher_pdf(context), f"{voucher.number}.pdf")


@require_POST
@books_staff_required
def voucher_email(request, pk):
    voucher = _voucher(pk)
    try:
        delivery.send_voucher(voucher, delivery.voucher_verify_url(request, voucher))
        messages.success(request, f"Voucher {voucher.number} emailed to {voucher.payee.email}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("books:voucher_detail", pk=voucher.pk)


@books_staff_required
def voucher_void(request, pk):
    voucher = _voucher(pk)
    if voucher.is_void:
        messages.error(request, "This voucher is already void.")
        return redirect("books:voucher_detail", pk=voucher.pk)
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Enter a reason for voiding this voucher.")
        else:
            voucher.void(reason, user=request.user)
            messages.success(request, f"Voucher {voucher.number} voided. The payment was reversed in the books too.")
            return redirect("books:voucher_detail", pk=voucher.pk)
    return render(request, "books/void_confirm.html", {
        "object_label": f"voucher {voucher.number} ({voucher.payee.name}, UGX {voucher.amount:,.0f})",
        "cancel_url": reverse("books:voucher_detail", args=[voucher.pk]),
    })


@_public_page
def voucher_verify(request, token):
    voucher = delivery.voucher_from_verify_token(token)
    context = {"company": documents._company_block(), "voucher": voucher}
    return render(request, "books/voucher_verify.html", context, status=200 if voucher else 404)
