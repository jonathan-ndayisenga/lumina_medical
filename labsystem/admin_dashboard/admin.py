from django.contrib import admin

from .models import (
    BankAccount,
    BankTransaction,
    CashDrawer,
    CashTransaction,
    Expense,
    HospitalAccount,
    InventoryItem,
    InventoryTransaction,
    MobileMoneyAccount,
    MobileMoneyTransaction,
    ReconciliationStatement,
    Salary,
)


@admin.register(HospitalAccount)
class HospitalAccountAdmin(admin.ModelAdmin):
    list_display = ("hospital", "balance", "updated_at")
    search_fields = ("hospital__name",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("description", "hospital", "amount", "category", "source", "source_account", "date")
    list_filter = ("hospital", "category", "source", "date")
    search_fields = ("description", "hospital__name")

    def source_account(self, obj):
        return obj.source_account_label


@admin.register(Salary)
class SalaryAdmin(admin.ModelAdmin):
    list_display = ("employee", "hospital", "month", "amount", "paid")
    list_filter = ("hospital", "paid", "month")
    search_fields = ("employee__username", "employee__first_name", "employee__last_name", "hospital__name")


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("name", "hospital", "category", "current_quantity", "unit", "reorder_level", "selling_price", "is_active")
    list_filter = ("hospital", "category", "is_active")
    search_fields = ("name", "hospital__name")


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ("item", "hospital", "transaction_type", "quantity", "visit", "performed_by", "created_at")
    list_filter = ("hospital", "transaction_type", "item__category")
    search_fields = ("item__name", "notes", "visit__patient__name")


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("bank_name", "account_name", "account_number", "hospital", "opening_balance", "is_active")
    list_filter = ("hospital", "bank_name", "is_active")
    search_fields = ("bank_name", "account_name", "account_number", "hospital__name")


@admin.register(MobileMoneyAccount)
class MobileMoneyAccountAdmin(admin.ModelAdmin):
    list_display = ("provider", "number", "hospital", "is_active")
    list_filter = ("hospital", "provider", "is_active")
    search_fields = ("provider", "number", "hospital__name")


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ("bank_account", "transaction_date", "description", "amount", "transaction_type", "is_reconciled")
    list_filter = ("transaction_type", "is_reconciled", "bank_account__hospital")
    search_fields = ("description", "reference", "bank_account__account_name", "bank_account__bank_name")


@admin.register(MobileMoneyTransaction)
class MobileMoneyTransactionAdmin(admin.ModelAdmin):
    list_display = ("mobile_money_account", "transaction_date", "description", "amount", "transaction_type", "is_reconciled")
    list_filter = ("transaction_type", "is_reconciled", "mobile_money_account__hospital")
    search_fields = ("description", "reference", "mobile_money_account__number", "mobile_money_account__provider")


@admin.register(CashDrawer)
class CashDrawerAdmin(admin.ModelAdmin):
    list_display = ("hospital", "date", "opening_balance", "closing_balance", "discrepancy", "closed_at")
    list_filter = ("hospital", "date")


@admin.register(CashTransaction)
class CashTransactionAdmin(admin.ModelAdmin):
    list_display = ("cash_drawer", "transaction_type", "amount", "description", "created_at")
    list_filter = ("transaction_type", "cash_drawer__hospital")
    search_fields = ("description",)


@admin.register(ReconciliationStatement)
class ReconciliationStatementAdmin(admin.ModelAdmin):
    list_display = ("hospital", "statement_type", "period_start", "period_end", "reconciled_balance", "generated_at")
    list_filter = ("hospital", "statement_type")
