import csv
import hashlib
import io
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models

from .document_models import Expense, Payment
from .ledger_models import Account

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%Y/%m/%d")
DATE_HEADERS = ("date", "transaction date", "value date", "posting date", "trans date", "completed on")
AMOUNT_HEADERS = ("amount", "transaction amount", "amount (ugx)")
IN_HEADERS = ("credit", "paid in", "money in", "deposit", "deposits", "received", "credit amount")
OUT_HEADERS = ("debit", "paid out", "money out", "withdrawal", "withdrawals", "sent", "debit amount")
TEXT_HEADERS = ("description", "details", "narration", "particulars", "transaction details", "memo")
REFERENCE_HEADERS = ("reference", "ref", "ref no", "reference number", "transaction id", "external id", "receipt no")
MATCH_WINDOW_DAYS = 7


class StatementLine(models.Model):
    """One row of a bank or mobile money statement, waiting to be tied to what
    the books already say happened. Importing never posts anything to the ledger."""

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="statement_lines")
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="Money in is positive, money out negative.")
    matched_payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name="statement_lines")
    matched_expense = models.ForeignKey(Expense, on_delete=models.SET_NULL, null=True, blank=True, related_name="statement_lines")
    ignored = models.BooleanField(default=False)
    import_key = models.CharField(max_length=64, unique=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.date} {self.amount}"

    @property
    def is_done(self) -> bool:
        return bool(self.matched_payment_id or self.matched_expense_id or self.ignored)

    def suggestions(self):
        """Records for the same amount, through the same account, within a week,
        not already tied to another line. Closest date first."""
        window = (self.date - timedelta(days=MATCH_WINDOW_DAYS), self.date + timedelta(days=MATCH_WINDOW_DAYS))
        if self.amount > 0:
            rows = Payment.objects.filter(
                amount=self.amount, date__range=window, voided_at__isnull=True,
                journal_entry__lines__account=self.account, statement_lines__isnull=True,
            ).select_related("client")
        else:
            rows = [
                expense for expense in Expense.objects.filter(
                    date__range=window, voided_at__isnull=True,
                    journal_entry__lines__account=self.account, statement_lines__isnull=True,
                )
                if expense.total_cost_ugx == -self.amount
            ]
        reference = self.reference.lower()
        return sorted(
            rows,
            key=lambda row: (not (reference and reference == (row.reference or "").lower()), abs((row.date - self.date).days)),
        )


def _clean_money(raw):
    text = (raw or "").replace(",", "").replace("UGX", "").replace("ugx", "").strip()
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    try:
        value = Decimal(text.strip("()"))
    except InvalidOperation:
        raise ValueError(f"'{raw}' is not an amount")
    return -value if negative else value


def _clean_date(raw):
    text = (raw or "").strip().split(" ")[0] if ":" in (raw or "") else (raw or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{raw}' is not a date")


def _pick(row, names):
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return ""


def parse_statement(raw_bytes: bytes) -> tuple:
    """Bank and MoMo exports differ, so columns are found by name.
    Returns (rows, problems): rows ready to import, and a note per row skipped."""
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in reader.fieldnames or []}
    if not headers & set(DATE_HEADERS) or not headers & set(AMOUNT_HEADERS + IN_HEADERS + OUT_HEADERS):
        raise ValidationError("This file needs a date column and an amount column (or money in / money out columns).")

    rows, problems = [], []
    for number, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        try:
            day = _clean_date(_pick(row, DATE_HEADERS))
            amount = _clean_money(_pick(row, AMOUNT_HEADERS))
            if amount is None:
                money_in = _clean_money(_pick(row, IN_HEADERS))
                money_out = _clean_money(_pick(row, OUT_HEADERS))
                amount = money_in if money_in else (-abs(money_out) if money_out else None)
            if not amount:
                raise ValueError("no amount")
        except ValueError as exc:
            problems.append(f"Row {number}: {exc}")
            continue
        rows.append({
            "date": day, "amount": amount,
            "description": _pick(row, TEXT_HEADERS)[:255], "reference": _pick(row, REFERENCE_HEADERS)[:100],
        })
    return rows, problems


def import_statement(account, rows) -> tuple:
    """Safe to re-upload: a row already imported is skipped, while two genuinely
    identical rows in one statement both stay (the key counts occurrences)."""
    seen = Counter()
    created = skipped = 0
    for row in rows:
        base = f"{account.pk}|{row['date']}|{row['amount']}|{row['description']}|{row['reference']}"
        seen[base] += 1
        key = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        _, was_created = StatementLine.objects.get_or_create(
            import_key=key, defaults={**row, "account": account},
        )
        created += was_created
        skipped += not was_created
    return created, skipped
