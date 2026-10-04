"""Builds plain-dict contexts for the three document types. Templates never
touch model instances directly, only this contract — so swapping the render
step (currently browser print via window.print(), see views.py) for
WeasyPrint-generated PDFs later is a rendering-layer change only."""

from . import reports
from .document_models import CompanySettings


def _company_block():
    settings_row = CompanySettings.load()
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
        "logo": settings_row.logo,
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


def invoice_context(invoice) -> dict:
    settings_row = CompanySettings.load()
    return {
        "company": _company_block(),
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
        "balance": invoice.balance,
        "is_settled": invoice.is_settled,
        "installments": [
            {"sequence": i.sequence, "due_date": i.due_date, "amount": i.amount}
            for i in invoice.installments.all()
        ],
        "notes": invoice.notes,
        "footer_notes": settings_row.invoice_footer_notes,
    }


def receipt_context(payment) -> dict:
    settings_row = CompanySettings.load()
    return {
        "company": _company_block(),
        "client": _client_block(payment.client),
        "receipt_number": payment.receipt_number,
        "date": payment.date,
        "amount": payment.amount,
        "method": payment.get_method_display(),
        "reference": payment.reference,
        "allocations": [
            {
                "invoice_number": a.invoice.number,
                "amount": a.amount,
                "invoice_balance_after": a.invoice.balance,
            }
            for a in payment.allocations.select_related("invoice").all()
        ],
        "unallocated": payment.unallocated,
        "footer_notes": settings_row.receipt_footer_notes,
    }


def statement_context(client, start=None, end=None) -> dict:
    statement = reports.client_statement(client, start, end)
    return {
        "company": _company_block(),
        "client": _client_block(client),
        "start": start,
        "end": end,
        "events": statement["events"],
        "closing_balance": statement["closing_balance"],
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
    company = _company_block()
    settings_row = CompanySettings.load()
    preparer = quotation.prepared_by
    # Header lettering like the PDF: "TERNAH" big, "SOFTWARE COMPANY LTD" small.
    brand_name = settings_row.trading_name or settings_row.legal_name
    brand_sub = settings_row.legal_name.replace(settings_row.trading_name, "", 1).strip() if settings_row.trading_name else ""
    return {
        "company": company,
        "brand_name": brand_name,
        "brand_sub": brand_sub or settings_row.tagline,
        "reg_no": settings_row.company_reg_no,
        "logo_url": settings_row.logo.url if settings_row.logo else "",
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
