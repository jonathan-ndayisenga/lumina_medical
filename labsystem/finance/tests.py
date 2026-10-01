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


class JournalReferenceAndIsolationTests(TestCase):
    """Production bug (Oct 2026): creating a visit crashed with
    TransactionManagementError after a duplicate journal reference."""

    def setUp(self):
        self.hospital = Hospital.objects.create(name="Journal Ref Hospital", subdomain="journal-ref")

    def _entry(self):
        return JournalEntry.objects.create(hospital=self.hospital, description="Test entry")

    def test_reference_after_a_deleted_entry_does_not_reuse_an_existing_number(self):
        first, second, third = self._entry(), self._entry(), self._entry()
        second.delete()
        # The old count-based numbering produced third's reference again here.
        fourth = self._entry()
        self.assertNotEqual(fourth.reference, third.reference)
        self.assertTrue(fourth.reference.endswith("-0004"))

    def test_reference_retries_when_the_number_is_taken(self):
        from unittest.mock import patch

        taken = self._entry()
        original_first = JournalEntry.objects.none().__class__.first
        calls = {"n": 0}

        def stale_first(qs):
            # First lookup sees no entries yet (another request won the race).
            calls["n"] += 1
            return None if calls["n"] == 1 else original_first(qs)

        with patch("django.db.models.query.QuerySet.first", stale_first):
            entry = self._entry()
        self.assertNotEqual(entry.reference, taken.reference)

    def test_a_failed_posting_does_not_break_the_surrounding_transaction(self):
        from django.db import transaction
        from finance.signals import _safe_post

        taken = self._entry()

        def duplicate_reference():
            JournalEntry.objects.create(hospital=self.hospital, description="dup", reference=taken.reference)

        with transaction.atomic():
            _safe_post(duplicate_reference)
            # Before the fix this query raised TransactionManagementError.
            self.assertEqual(JournalEntry.objects.filter(hospital=self.hospital).count(), 1)
