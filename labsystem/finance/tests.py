from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase

from accounts.models import Hospital, HospitalModuleSubscription, Module
from admin_dashboard.models import Expense
from finance.accounts_seed import provision_chart_of_accounts
from finance.models import Account, JournalEntry, JournalLine
from finance.posting import post_expense


def _enable_finance(hospital):
    module, _ = Module.objects.get_or_create(code="finance", defaults={"name": "Finance"})
    HospitalModuleSubscription.objects.get_or_create(
        hospital=hospital, module=module, defaults={"is_active": True}
    )


class ExpenseIdempotencyTests(TestCase):
    """Posting an expense multiple times must not inflate journal balances."""

    def setUp(self):
        self.hospital = Hospital.objects.create(name="Lumina Finance", subdomain="lumina-finance")
        _enable_finance(self.hospital)
        provision_chart_of_accounts(self.hospital)
        self.expense = Expense.objects.create(
            hospital=self.hospital,
            description="Electricity bill",
            category=Expense.CATEGORY_UTILITIES,
            amount=Decimal("50000"),
            source=Expense.SOURCE_CASH_DRAWER,
            date="2026-07-01",
        )

    def _active_expense_debit(self):
        """Sum debit on expense accounts excluding already-reversed entries."""
        expense_accounts = Account.objects.filter(
            hospital=self.hospital, account_type=Account.TYPE_EXPENSE
        ).values_list("id", flat=True)
        return (
            JournalLine.objects.filter(
                account__in=expense_accounts,
                entry__is_reversal=False,
                entry__reversal_of__isnull=True,
            ).aggregate(t=Sum("debit"))["t"]
            or Decimal("0")
        )

    def test_posting_expense_once_creates_single_debit(self):
        post_expense(self.expense)
        self.assertEqual(self._active_expense_debit(), Decimal("50000"))

    def test_reposting_expense_does_not_double_count(self):
        post_expense(self.expense)
        post_expense(self.expense)
        self.assertEqual(
            self._active_expense_debit(),
            Decimal("50000"),
            "Re-posting the same expense should not add a second debit to the ledger",
        )

    def test_reposting_expense_three_times_still_correct(self):
        post_expense(self.expense)
        post_expense(self.expense)
        post_expense(self.expense)
        self.assertEqual(self._active_expense_debit(), Decimal("50000"))

    def test_only_one_active_journal_entry_after_repeated_posts(self):
        post_expense(self.expense)
        post_expense(self.expense)
        active = JournalEntry.objects.filter(
            hospital=self.hospital,
            source_expense=self.expense,
            is_reversal=False,
            reversal_of__isnull=True,
        ).count()
        self.assertEqual(active, 1, "Only one active (un-reversed) journal entry should exist per expense")
