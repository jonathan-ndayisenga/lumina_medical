# Financial ER Diagram (Expense Source Tracking)

This diagram reflects the updated financial relationships where each `Expense` can be tied to the exact source account used to pay it.

```mermaid
erDiagram
    HOSPITAL ||--|| HOSPITAL_ACCOUNT : owns
    HOSPITAL ||--o{ EXPENSE : records
    HOSPITAL ||--o{ SALARY : pays
    HOSPITAL ||--o{ BANK_ACCOUNT : has
    HOSPITAL ||--o{ MOBILE_MONEY_ACCOUNT : has
    HOSPITAL ||--o{ CASH_DRAWER : opens
    HOSPITAL ||--o{ RECONCILIATION_STATEMENT : generates
    HOSPITAL ||--o{ PAYMENT : receives

    VISIT ||--o{ PAYMENT : bills

    BANK_ACCOUNT ||--o{ BANK_TRANSACTION : records
    BANK_ACCOUNT ||--o{ EXPENSE : funds

    MOBILE_MONEY_ACCOUNT ||--o{ EXPENSE : funds

    CASH_DRAWER ||--o{ CASH_TRANSACTION : records
    CASH_DRAWER ||--o{ EXPENSE : funds

    PAYMENT ||--o{ BANK_TRANSACTION : reconciles_with
    PAYMENT ||--o{ CASH_TRANSACTION : reconciles_with

    USER ||--o{ SALARY : employee
    USER ||--o{ RECONCILIATION_STATEMENT : generated_by
```

## Expense Source Rules

- `Expense.source = bank_account` -> `Expense.bank_account` must be set.
- `Expense.source = mobile_money` -> `Expense.mobile_money_account` must be set.
- `Expense.source = cash_drawer` -> `Expense.cash_drawer` must be set.
- Non-selected account fields are cleared so every expense points to one source account only.
