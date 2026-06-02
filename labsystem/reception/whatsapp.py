# Twilio-based sending is on hold. The receipt is delivered via a wa.me deep-link instead.
# To re-enable Twilio sending, uncomment the send_whatsapp_receipt function below and set
# TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_FROM environment variables.

import re
import urllib.parse


def _digits_only(raw: str) -> str:
    """Strip everything except digits and normalize to international format.
    Uganda local numbers starting with 0 are converted to 256XXXXXXXXX.
    """
    digits = re.sub(r"[^\d]", "", raw)
    # Local format: 07XXXXXXXX or 03XXXXXXXX (10 digits starting with 0) → 256XXXXXXXXX
    if len(digits) == 10 and digits.startswith("0"):
        digits = "256" + digits[1:]
    return digits


def build_receipt_message(payment, visit) -> str:
    hospital_name = visit.hospital.name if visit.hospital else "Lumina Medical"
    paid_at = payment.paid_at.strftime("%Y-%m-%d %H:%M") if payment.paid_at else "-"

    service_lines = []
    for vs in visit.visit_services.select_related("service").all():
        service_lines.append(f"  • {vs.service.name}: UGX {vs.price_at_time:,}")
    services_block = "\n".join(service_lines) if service_lines else "  (no services)"

    return (
        f"*{hospital_name} — Receipt*\n"
        f"Receipt #: {payment.receipt_number}\n"
        f"Date: {paid_at}\n"
        f"Patient: {visit.patient.name}\n\n"
        f"*Services:*\n{services_block}\n\n"
        f"Total:    UGX {visit.total_amount:,}\n"
        f"Paid:     UGX {payment.amount_paid:,}\n"
        f"Balance:  UGX {visit.balance_due:,}\n\n"
        f"Mode: {payment.get_mode_display()}\n"
        f"Thank you! Get well soon."
    )


def build_walink(to_number: str, message_body: str) -> str:
    """Return a wa.me URL that opens WhatsApp pre-filled with message_body to to_number."""
    digits = _digits_only(to_number)
    encoded = urllib.parse.quote(message_body)
    return f"https://wa.me/{digits}?text={encoded}"


# ── Twilio background-send (on hold) ─────────────────────────────────────────
# import logging
# import threading
# from django.conf import settings
# logger = logging.getLogger(__name__)
#
# def send_whatsapp_receipt(to_number: str, message_body: str) -> None:
#     def _send():
#         account_sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
#         auth_token  = getattr(settings, "TWILIO_AUTH_TOKEN", "")
#         from_number = getattr(settings, "TWILIO_WHATSAPP_FROM", "")
#         if not all([account_sid, auth_token, from_number]):
#             logger.warning("WhatsApp receipt not sent: Twilio not configured.")
#             return
#         try:
#             from twilio.rest import Client
#             from reception.whatsapp import _digits_only
#             clean_to = f"+{_digits_only(to_number)}"
#             client = Client(account_sid, auth_token)
#             msg = client.messages.create(body=message_body, from_=from_number, to=f"whatsapp:{clean_to}")
#             logger.info("WhatsApp receipt sent to %s (SID: %s)", clean_to, msg.sid)
#         except Exception:
#             logger.exception("WhatsApp receipt send failed to %s", to_number)
#     threading.Thread(target=_send, daemon=True).start()
