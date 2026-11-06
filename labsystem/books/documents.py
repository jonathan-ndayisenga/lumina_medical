"""Builds plain-dict contexts for the three document types. Templates never
touch model instances directly, only this contract — so swapping the render
step (currently browser print via window.print(), see views.py) for
WeasyPrint-generated PDFs later is a rendering-layer change only."""

from django.utils import timezone

from . import reports
from .document_models import CompanySettings


def _company_block(hospital=None):
    settings_row = CompanySettings.load()
    logo = None
    if hospital and getattr(hospital, "logo", None):
        logo = hospital.logo
    elif settings_row.logo:
        logo = settings_row.logo
    return {
        "legal_name": settings_row.legal_name,
        "trading_name": settings_row.trading_name,
        "tagline": settings_row.tagline,
        "tin": settings_row.tin,
        "vrn": settings_row.vrn,
        "address": settings_row.address,
        "city": settings_row.city,
        "phone": settings_row.phone,
        "email": settings_row.email,
        "logo": logo,
        "bank_name": settings_row.bank_name,
        "bank_account_name": settings_row.bank_account_name,
        "bank_account_number": settings_row.bank_account_number,
        "momo_mtn_number": settings_row.momo_mtn_number,
        "momo_mtn_name": settings_row.momo_mtn_name,
        "momo_airtel_number": settings_row.momo_airtel_number,
        "momo_airtel_name": settings_row.momo_airtel_name,
    }


def _client_block(client):
    return {
        "name": client.name,
        "trading_name": client.trading_name,
        "tin": client.tin,
        "address": client.address,
        "phone": client.phone,
        "email": client.email,
        "contact_person": client.contact_person,
    }


def format_money(value, currency="UGX") -> str:
    if currency == "UGX":
        return f"{float(value):,.0f}"
    return f"{float(value):,.2f}"


def _invoice_stamp(invoice) -> str:
    if invoice.status == invoice.STATUS_VOID:
        return "VOID"
    if invoice.status == invoice.STATUS_DRAFT:
        return "DRAFT"
    if invoice.is_settled:
        return "PAID"
    return "OVERDUE" if invoice.is_overdue else ""


def invoice_context(invoice) -> dict:
    settings_row = CompanySettings.load()
    hospital = getattr(invoice.client, "hospital", None)
    return {
        "company": _company_block(hospital),
        "client": _client_block(invoice.client),
        "heading": settings_row.invoice_heading,
        "number": invoice.number,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "reference": invoice.reference,
        "currency": invoice.currency,
        "lines": [
            {
                "description": line.description,
                "detail": line.detail,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "amount": line.amount,
            }
            for line in invoice.lines.all()
        ],
        "subtotal": invoice.subtotal,
        "tax_label": f"VAT {settings_row.vat_rate}%",
        "tax_amount": invoice.tax_amount,
        "total": invoice.total,
        "amount_paid": invoice.amount_paid,
        "wht_credited": invoice.wht_credited,
        "credited": invoice.credited,
        "stamp": _invoice_stamp(invoice),
        "balance": invoice.balance,
        "is_settled": invoice.is_settled,
        "is_void": invoice.status == invoice.STATUS_VOID,
        "installments": [
            {"sequence": i.sequence, "due_date": i.due_date, "amount": i.amount}
            for i in invoice.installments.all()
        ],
        "notes": invoice.notes,
        "footer_notes": settings_row.invoice_footer_notes,
    }


def receipt_context(payment) -> dict:
    settings_row = CompanySettings.load()
    hospital = getattr(payment.client, "hospital", None)
    return {
        "company": _company_block(hospital),
        "client": _client_block(payment.client),
        "heading": "RECEIPT",
        "number": payment.receipt_number,
        "currency": "UGX",
        "date": payment.date,
        "amount": payment.amount,
        "method": payment.get_method_display(),
        "reference": payment.reference,
        "allocations": [
            {
                "invoice_number": a.invoice.number,
                "kind": a.invoice.get_kind_display(),
                "invoice_total": a.invoice.total,
                "amount": a.amount,
                "invoice_balance_after": a.invoice.balance,
            }
            for a in payment.allocations.select_related("invoice").all()
        ],
        "unallocated": payment.unallocated,
        "client_balance": payment.client.balance,
        "is_settled": payment.client.balance <= 0,
        "footer_notes": settings_row.receipt_footer_notes,
    }


def statement_context(client, start=None, end=None) -> dict:
    statement = reports.client_statement(client, start, end)
    hospital = getattr(client, "hospital", None)
    today = timezone.localdate()
    return {
        "company": _company_block(hospital),
        "client": _client_block(client),
        "heading": "STATEMENT",
        "number": f"{(end or today):%B %Y}",
        "currency": "UGX",
        "today": today,
        "start": start,
        "end": end,
        "events": statement["events"],
        "closing_balance": statement["closing_balance"],
        "is_settled": statement["closing_balance"] <= 0,
    }


def credit_note_context(note) -> dict:
    settings_row = CompanySettings.load()
    return {
        "company": _company_block(),
        "client": _client_block(note.client),
        "heading": "CREDIT NOTE",
        "number": note.number,
        "date": note.date,
        "currency": note.invoice.currency,
        "invoice_number": note.invoice.number,
        "reason": note.reason,
        "amount": note.amount,
        "net_amount": note.net_amount,
        "tax_amount": note.tax_amount,
        "tax_label": f"VAT {settings_row.vat_rate}%",
        "invoice_balance": note.invoice.balance,
        "stamp": "VOID" if note.voided_at else "",
    }


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
         "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    words = []
    if hundreds:
        words.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        tens = _ONES[rest] if rest < 20 else " ".join(w for w in (_TENS[rest // 10], _ONES[rest % 10]) if w)
        words.append(("and " if hundreds else "") + tens)
    return " ".join(words)


def amount_in_words(value, currency="UGX") -> str:
    """3400000 -> 'Three Million, Four Hundred Thousand Uganda Shillings only.'"""
    n = int(round(float(value)))
    if n == 0:
        spoken = "Zero"
    else:
        parts = []
        for size, name in ((10**9, "Billion"), (10**6, "Million"), (10**3, "Thousand"), (1, "")):
            count, n = divmod(n, size)
            if count:
                parts.append(f"{_below_thousand(count)} {name}".strip())
        spoken = ", ".join(parts)
    currency_name = "Uganda Shillings" if currency == "UGX" else currency
    return f"{spoken} {currency_name} only."


def quotation_context(quotation) -> dict:
    from .quotation_models import QuotationField, QuotationSettings

    values = {fv.field_id: fv.value for fv in quotation.field_values.all()}
    company = _company_block(getattr(quotation.client, "hospital", None))
    settings_row = CompanySettings.load()
    preparer = quotation.prepared_by
    # Header lettering like the PDF: "TERNAH" big, "SOFTWARE COMPANY LTD" small.
    brand_name = settings_row.trading_name or settings_row.legal_name
    brand_sub = settings_row.legal_name.replace(settings_row.trading_name, "", 1).strip() if settings_row.trading_name else ""
    return {
        "heading": "QUOTATION",
        "company": company,
        "brand_name": brand_name,
        "brand_sub": brand_sub or settings_row.tagline,
        "reg_no": settings_row.company_reg_no,
        "logo_url": company["logo"].url if company.get("logo") else "",
        "client": _client_block(quotation.client),
        "number": quotation.number,
        "subtitle": quotation.subtitle,
        "attention": quotation.attention,
        "location": quotation.location,
        "issue_date": quotation.issue_date,
        "valid_until": quotation.valid_until,
        "currency": quotation.currency,
        "currency_label": "UGX (Ugandan Shilling)" if quotation.currency == "UGX" else quotation.currency,
        "extra_fields": [
            {"label": field.label, "value": values.get(field.pk, "")}
            for field in QuotationField.objects.filter(active=True)
            if values.get(field.pk)
        ],
        "intro": quotation.intro,
        "lines": [
            {"title": line.title, "description": line.description, "basis": line.basis, "amount": line.amount}
            for line in quotation.lines.all()
        ],
        "subtotal": quotation.subtotal,
        "tax_label": f"VAT {CompanySettings.load().vat_rate}%",
        "tax_amount": quotation.tax_amount,
        "apply_vat": quotation.apply_vat,
        "total": quotation.total,
        "amount_in_words": amount_in_words(quotation.total, quotation.currency),
        "highlight_title": quotation.highlight_title,
        "highlight_body": quotation.highlight_body,
        "note_left_title": quotation.note_left_title,
        "note_left_body": quotation.note_left_body,
        "note_right_title": quotation.note_right_title,
        "note_right_body": quotation.note_right_body,
        "prepared_by_name": (preparer.get_full_name() or preparer.username) if preparer else "",
        "footer_tagline": QuotationSettings.load().footer_tagline,
    }


def _payee_block(payee):
    payment_lines = []
    if payee.momo_number:
        payment_lines.append(f"Mobile money {payee.momo_number}")
    if payee.bank_account_number:
        bank = " · ".join(part for part in (payee.bank_name, payee.bank_account_name, payee.bank_account_number) if part)
        payment_lines.append(f"Bank {bank}")
    return {
        "name": payee.name,
        "trading_name": "",
        "tin": payee.tin,
        "address": payee.address,
        "phone": payee.phone,
        "email": payee.email,
        "contact_person": payee.contact_person,
        "payment_lines": payment_lines,
    }


def voucher_context(voucher) -> dict:
    expense = voucher.expense
    issuer = voucher.issued_by
    return {
        "company": _company_block(),
        "client": _payee_block(voucher.payee),
        "heading": "PAYMENT VOUCHER",
        "number": voucher.number,
        "date": expense.date,
        "currency": "UGX",
        "amount": expense.amount_ugx,
        "amount_in_words": amount_in_words(expense.amount_ugx),
        "description": expense.description,
        "category": expense.account.name,
        "method": expense.get_method_display(),
        "reference": expense.reference,
        "received_by": voucher.received_by,
        "prepared_by": (issuer.get_full_name() or issuer.username) if issuer else "",
        "stamp": "VOID" if expense.voided_at else "PAID",
        "is_void": bool(expense.voided_at),
    }


def payee_statement_context(payee, start=None, end=None) -> dict:
    vouchers = payee.live_vouchers().select_related("expense", "expense__account").order_by("expense__date", "id")
    if start:
        vouchers = vouchers.filter(expense__date__gte=start)
    if end:
        vouchers = vouchers.filter(expense__date__lte=end)
    rows = [
        {"date": v.expense.date, "number": v.number, "description": v.expense.description,
         "method": v.expense.get_method_display(), "amount": v.expense.amount_ugx}
        for v in vouchers
    ]
    today = timezone.localdate()
    return {
        "company": _company_block(),
        "client": _payee_block(payee),
        "heading": "PAYMENTS STATEMENT",
        "number": f"{(end or today):%B %Y}",
        "currency": "UGX",
        "today": today,
        "start": start,
        "end": end,
        "rows": rows,
        "total": sum((row["amount"] for row in rows), 0),
    }
