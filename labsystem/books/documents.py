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
