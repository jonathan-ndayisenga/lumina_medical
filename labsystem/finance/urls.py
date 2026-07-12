from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="finance_dashboard"),
    path("accounts/", views.chart_of_accounts, name="finance_accounts"),
    path("accounts/new/", views.account_create, name="finance_account_create"),
    path("journal/", views.journal_list, name="finance_journal"),
    path("journal/new/", views.journal_entry_create, name="finance_journal_create"),
    path("expenses/", views.expense_journal, name="finance_expenses"),
    path("expenses/<int:expense_id>/delete/", views.expense_delete, name="finance_expense_delete"),
    path("cashbook/", views.cashbook, name="finance_cashbook"),
    path("debtors/", views.debtor_ledger, name="finance_debtors"),
    path("debtors/<int:patient_id>/", views.debtor_patient, name="finance_debtor_patient"),
    path("reports/revenue/", views.revenue_report, name="finance_revenue"),
    path("reports/revenue/print/", views.revenue_report_print, name="finance_revenue_print"),
    path("reports/trial-balance/", views.trial_balance, name="finance_trial_balance"),
    path("reports/profit-loss/", views.profit_and_loss, name="finance_pnl"),
    path("reports/balance-sheet/", views.balance_sheet, name="finance_balance_sheet"),
    path("opening-balances/", views.opening_balances, name="finance_opening_balances"),
]
