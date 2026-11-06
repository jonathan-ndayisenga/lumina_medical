"""Getting documents to clients: email with the PDF attached, WhatsApp links, and
the automatic payment reminders."""
from datetime import timedelta
from smtplib import SMTPException
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.signing import BadSignature, Signer
from django.urls import reverse
from django.utils import timezone

from . import documents, pdf
from .document_models import CompanySettings, Invoice, Payment
from .insights import whatsapp_phone
from .planning_models import ReminderLog

REMINDER_DAYS_BEFORE = 3
# Stage name -> days past the due date it starts. Latest first: one email per run.
OVERDUE_STAGES = (("overdue_30", 30), ("overdue_14", 14), ("overdue_3", 3))
RECEIPT_SIGNER = Signer(salt="books.receipt.verify")
VOUCHER_SIGNER = Signer(salt="books.voucher.verify")


def receipt_verify_token(payment) -> str:
    return RECEIPT_SIGNER.sign(str(payment.pk)).replace(":", "-")


def payment_from_verify_token(token: str):
    pk, separator, signature = token.partition("-")
    try:
        RECEIPT_SIGNER.unsign(f"{pk}:{signature}")
    except BadSignature:
        return None
    return Payment.objects.select_related("client").filter(pk=pk).first()


def receipt_verify_url(request, payment) -> str:
    return request.build_absolute_uri(reverse("books:receipt_verify", args=[receipt_verify_token(payment)]))


def voucher_verify_token(voucher) -> str:
    return VOUCHER_SIGNER.sign(str(voucher.pk)).replace(":", "-")


def voucher_from_verify_token(token: str):
    from .payee_models import PaymentVoucher

    pk, separator, signature = token.partition("-")
    try:
        VOUCHER_SIGNER.unsign(f"{pk}:{signature}")
    except BadSignature:
        return None
    return PaymentVoucher.objects.select_related("payee", "expense").filter(pk=pk).first()


def voucher_verify_url(request, voucher) -> str:
    return request.build_absolute_uri(reverse("books:voucher_verify", args=[voucher_verify_token(voucher)]))


def email_is_configured() -> bool:
    return bool(settings.EMAIL_HOST_CONFIGURED)


def _send(subject: str, body: str, to: str, filename: str, content: bytes):
    if not email_is_configured():
        raise ValidationError("Email isn't set up on this server yet, so nothing was sent.")
    if not to:
        raise ValidationError("There is no email address on file for them.")
    company = CompanySettings.load()
    message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to], reply_to=[company.email] if company.email else None)
    message.attach(filename, content, "application/pdf")
    try:
        message.send(fail_silently=False)
    except (SMTPException, OSError) as exc:
        raise ValidationError(f"The email could not be sent: {exc}")


def _greeting(client) -> str:
    return f"Hello {client.contact_person or client.name},"


def _sign_off() -> str:
    company = CompanySettings.load()
    return f"Thank you,\n{company.trading_name or company.legal_name}"


def send_invoice(invoice):
    context = documents.invoice_context(invoice)
    due = f" It is due on {invoice.due_date:%d %b %Y}." if invoice.due_date else ""
    body = f"{_greeting(invoice.client)}\n\nPlease find invoice {invoice.number} attached, for {invoice.currency} {documents.format_money(invoice.balance, invoice.currency)}.{due}\n\n{_sign_off()}"
    _send(f"Invoice {invoice.number}", body, invoice.client.email, f"{invoice.number}.pdf", pdf.invoice_pdf(context))


def send_receipt(payment, verify_url=""):
    context = documents.receipt_context(payment)
    context["verify_url"] = verify_url
    body = f"{_greeting(payment.client)}\n\nWe have received UGX {payment.amount:,.0f}. Your receipt {payment.receipt_number} is attached.\n\n{_sign_off()}"
    _send(f"Receipt {payment.receipt_number}", body, payment.client.email, f"{payment.receipt_number}.pdf", pdf.receipt_pdf(context))


def send_voucher(voucher, verify_url=""):
    if voucher.is_void:
        raise ValidationError("A void voucher can't be sent.")
    context = documents.voucher_context(voucher)
    context["verify_url"] = verify_url
    body = (
        f"{_greeting(voucher.payee)}\n\nWe have paid you UGX {voucher.amount:,.0f} for {voucher.expense.description}. "
        f"Payment voucher {voucher.number} is attached.\n\n{_sign_off()}"
    )
    _send(f"Payment voucher {voucher.number}", body, voucher.payee.email, f"{voucher.number}.pdf", pdf.voucher_pdf(context))


def voucher_whatsapp_url(voucher, verify_url="") -> str:
    company = CompanySettings.load()
    text = (
        f"{_greeting(voucher.payee)}\n{company.trading_name or company.legal_name} has paid you UGX {voucher.amount:,.0f} "
        f"for {voucher.expense.description}. Payment voucher number {voucher.number}."
    )
    if verify_url:
        text += f"\nConfirm it here: {verify_url}"
    return whatsapp_url(voucher.payee, text)


def whatsapp_url(client, text: str) -> str:
    return f"https://wa.me/{whatsapp_phone(client.phone)}?text={quote(text)}"


def invoice_whatsapp_url(invoice) -> str:
    company = CompanySettings.load()
    return whatsapp_url(invoice.client, (
        f"{_greeting(invoice.client)}\nThis is invoice {invoice.number} from {company.trading_name or company.legal_name} "
        f"for {invoice.currency} {documents.format_money(invoice.balance, invoice.currency)}."
    ))


def receipt_whatsapp_url(payment, verify_url="") -> str:
    company = CompanySettings.load()
    text = f"{_greeting(payment.client)}\n{company.trading_name or company.legal_name} has received UGX {payment.amount:,.0f}. Your receipt number is {payment.receipt_number}."
    if verify_url:
        text += f"\nConfirm it here: {verify_url}"
    return whatsapp_url(payment.client, text)


def reminder_stage(invoice, today):
    """Which reminder this invoice is due today, or None. Late runs catch up to
    the latest stage only, so a client never gets a burst of emails."""
    if invoice.status != Invoice.STATUS_OPEN or not invoice.due_date or invoice.balance <= 0:
        return None
    days_late = (today - invoice.due_date).days
    stage = None
    for name, starts in OVERDUE_STAGES:
        if days_late >= starts:
            stage = name
            break
    if stage is None and days_late == 0:
        stage = "due_today"
    if stage is None and timedelta(0) < invoice.due_date - today <= timedelta(days=REMINDER_DAYS_BEFORE):
        stage = "before_due"
    if stage is None or invoice.reminders.filter(stage=stage).exists():
        return None
    return stage


def _reminder_text(invoice, stage) -> tuple:
    amount = f"{invoice.currency} {documents.format_money(invoice.balance, invoice.currency)}"
    due = f"{invoice.due_date:%d %b %Y}"
    if stage == "before_due":
        subject, line = f"Invoice {invoice.number} is due on {due}", f"A quick reminder that invoice {invoice.number} ({amount}) is due on {due}."
    elif stage == "due_today":
        subject, line = f"Invoice {invoice.number} is due today", f"Invoice {invoice.number} ({amount}) is due today."
    else:
        subject, line = f"Invoice {invoice.number} is overdue", f"Invoice {invoice.number} ({amount}) was due on {due} and is still open."
    return subject, line


def send_due_reminders(today=None, dry_run=False) -> dict:
    today = today or timezone.localdate()
    result = {"sent": [], "skipped": [], "errors": []}
    if not CompanySettings.load().reminders_enabled:
        result["skipped"].append("Automatic reminders are turned off in Settings.")
        return result
    for invoice in Invoice.objects.filter(status=Invoice.STATUS_OPEN).select_related("client"):
        stage = reminder_stage(invoice, today)
        if stage is None:
            continue
        label = f"{invoice.number} ({stage}) to {invoice.client.name}"
        if not invoice.client.email:
            result["skipped"].append(f"{label}: no email address")
            continue
        if dry_run:
            result["sent"].append(label)
            continue
        subject, line = _reminder_text(invoice, stage)
        body = f"{_greeting(invoice.client)}\n\n{line} A copy is attached.\n\nIf you have already paid, please ignore this message.\n\n{_sign_off()}"
        try:
            _send(subject, body, invoice.client.email, f"{invoice.number}.pdf", pdf.invoice_pdf(documents.invoice_context(invoice)))
        except ValidationError as exc:
            result["errors"].append(f"{label}: {'; '.join(exc.messages)}")
            continue
        ReminderLog.objects.create(invoice=invoice, stage=stage, sent_to=invoice.client.email)
        result["sent"].append(label)
    return result
