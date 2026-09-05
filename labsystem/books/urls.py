from django.urls import path

from . import views

app_name = "books"

urlpatterns = [
    path("login/", views.BooksLoginView.as_view(), name="login"),
    path("logout/", views.BooksLogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("settings/", views.company_settings, name="company_settings"),
    path("settings/accounts/", views.account_list, name="account_list"),
    path("settings/accounts/new/", views.account_create, name="account_create"),
    path("settings/accounts/<int:pk>/edit/", views.account_edit, name="account_edit"),
    path("settings/products/", views.product_list, name="product_list"),
    path("settings/products/new/", views.product_create, name="product_create"),
    path("settings/products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("settings/financial-years/", views.financial_year_list, name="financial_year_list"),
    path("settings/financial-years/new/", views.financial_year_create, name="financial_year_create"),
    path("settings/financial-years/<int:pk>/close/", views.financial_year_close, name="financial_year_close"),

    path("clients/", views.client_list, name="client_list"),
    path("clients/new/", views.client_create, name="client_create"),
    path("clients/<int:pk>/", views.client_detail, name="client_detail"),
    path("clients/<int:pk>/statement.pdf", views.client_statement_pdf, name="client_statement_pdf"),
    path("clients/<int:client_pk>/payments/new/", views.payment_create, name="client_payment_create"),

    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/new/", views.invoice_create, name="invoice_create"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/void/", views.invoice_void, name="invoice_void"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),

    path("payments/new/", views.payment_create, name="payment_create"),
    path("payments/<int:pk>/void/", views.payment_void, name="payment_void"),

    path("receipts/", views.receipt_list, name="receipt_list"),
    path("receipts/<int:pk>/pdf/", views.receipt_pdf, name="receipt_pdf"),

    path("expenses/", views.expense_list, name="expense_list"),
    path("expenses/new/", views.expense_create, name="expense_create"),
    path("expenses/<int:pk>/void/", views.expense_void, name="expense_void"),

    path("reports/trial-balance/", views.report_trial_balance, name="report_trial_balance"),
    path("reports/profit-loss/", views.report_profit_loss, name="report_profit_loss"),
    path("reports/balance-sheet/", views.report_balance_sheet, name="report_balance_sheet"),
    path("reports/ageing/", views.report_ageing, name="report_ageing"),
    path("reports/tax-pack/", views.report_tax_pack, name="report_tax_pack"),

    path("journal/", views.journal_list, name="journal_list"),
]
