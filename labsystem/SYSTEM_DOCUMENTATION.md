# Ternah Health System (Hospital EMR) — System Documentation

Last Updated: 2026-07-12
Django Version: 6.0.3
Primary DB: PostgreSQL (production via DATABASE_URL) / SQLite (development)
Deployment: DigitalOcean App Platform

This document describes the current state of the codebase — models, modules, routing, workflows, and recent feature additions.

---

## Table of Contents

1. System Overview
2. Authentication, Authorization, and Multi-Tenancy
3. Core Data Models (ER Summary)
4. Module Breakdown
5. URL Routing Map
6. End-to-End Workflows
7. Finance Module
8. Home Care Module
9. Pharmacy — Inventory Categories & Dispensing Math
10. Prescription Notes
11. User Management
12. Deployment Notes

---

## 1. System Overview

Multi-tenant Hospital EMR. Every record is scoped to a `Hospital` FK — no data crosses between hospitals. The central object is the `Visit`, connecting reception, nursing, doctor, lab, pharmacy, and billing.

Project structure:

```
labsystem/
  accounts/         auth + multi-tenant Hospital/User models
  admin_dashboard/  hospital admin: inventory, users, services, expenses, salaries, financials
  reception/        patients, visits, billing (receipts), queue routing, pharmacy dispensing
  nurse/            triage, nursing notes, IV/IM dispensing queue, sonographer queue
  doctor/           consultation, prescribing, lab requests
  lab/              lab queue, test results, lab reports
  finance/          double-entry accounting ledger (Chart of Accounts, Journal Entries, reports)
  homecare/         home care placement management (nurses, clients, contracts, receipts)
  templates/        shared templates (base + shared print partials)
  static/           static assets
```

---

## 2. Authentication, Authorization, and Multi-Tenancy

### 2.1 Multi-tenancy

- Tenant root model: `accounts.Hospital`
- Tenant middleware: `labsystem.middleware.HospitalMiddleware`
  - Sets `request.hospital` by subdomain (production) or by `request.user.hospital` (local/dev).

### 2.2 Authentication

- Custom user model: `accounts.User` (extends `AbstractUser`)
- Login view: `accounts.views.RoleAwareLoginView`
- Default router after login: `accounts.views.app_home`

### 2.3 Authorization model

Hybrid: single primary role (stored on `User.role`) + optional Django Group membership for multi-module access.

Roles:
- `superadmin` / `developer` — platform-level (DigitalOcean console only)
- `hospital_admin` — manages users, inventory, services, expenses, salaries
- `doctor` — consultation + prescribing
- `nurse` — triage, nursing notes, IV/IM dispensing
- `receptionist` — patient registration, visits, payments
- `lab_attendant` — lab queue + report entry

Module Groups (additive, stored as Django Groups):
- Reception, Doctor, Nurse, Lab

Module access decorators accept: `(role in allowed set) OR (member of module group)`.

Group seeding migration: `accounts/migrations/0003_create_default_module_groups.py`

### 2.4 User Management (Hospital Admin)

All user management at `/platform/hospital/users/`:

| Action | URL | Notes |
|---|---|---|
| Create | POST `/platform/hospital/users/` | pill toggles for module groups |
| Edit | `/platform/hospital/users/<id>/edit/` | |
| Deactivate | `/platform/hospital/users/<id>/deactivate/` | preserves history |
| Reset Password | `/platform/hospital/users/<id>/reset-password/` | 8-char min, strength bar |
| Delete | `/platform/hospital/users/<id>/delete/` | permanent, confirmation screen |

Staff list is paginated — 10 members per page.

---

## 3. Core Data Models (ER Summary)

### 3.1 Tenant + users

- `SubscriptionPlan` 1 → N `Hospital`
- `Hospital` 1 → N `User`

`Hospital` metadata used on printouts: location, box_number, phone_number, email, logo.

### 3.2 Reception / clinical hub

#### Patient (`reception.Patient`)
- hospital (FK), name, registration_date, date_of_birth, age (stored as "22YRS"/"6MTH"), sex, contact, weight_kg
- Optional: email, address, next_of_kin, NIN, insurance_provider, insurance_policy_number
- Validation: either DOB or age required; form syncs DOB↔age.

#### Visit (`reception.Visit`)
- patient (FK), hospital (FK), visit_date, status (in_progress / ready_for_billing / completed / cancelled)
- `total_paid` = SUM(payments.amount_paid) excluding waived
- `balance_due` = total_amount − total_paid
- `is_fully_paid` = balance_due ≤ 0

#### Triage (`reception.Triage`)
- visit (OneToOne) — shared record between doctor and nurse
- Fields: weight_kg, bp_systolic, bp_diastolic, pulse, resp_rate, temp, spo2, glucose
- Required for nurse sign-off: weight_kg + bp_systolic + bp_diastolic

#### Service (`reception.Service`)
- hospital (FK), name (unique per hospital), category, price, is_active
- Categories: consultation, lab, triage, procedure, pharmacy, other, scan
- Optional `test_profile` FK (lab services)

#### VisitService (`reception.VisitService`)
- visit (FK), service (FK), price_at_time, performed (bool), notes

#### QueueEntry (`reception.QueueEntry`)
- hospital (FK), visit (FK), queue_type (lab_reception / lab_doctor / doctor / nurse), reason, processed

#### Payment (`reception.Payment`)
- visit (FK), amount_paid, mode (cash / card / mobile_money / insurance)
- bank_account FK (required for card), mobile_account FK (required for mobile_money)
- `receipt_number` derived from date + PK

### 3.3 Pharmacy (admin_dashboard.InventoryItem)

See Section 9 for category/math detail.

Key fields: name, category, base_unit, units_per_pack, strength_mg_per_unit, concentration_mg_per_ml, current_quantity, selling_price.

### 3.4 Prescription (doctor.Prescription)
- visit (FK), drug (FK → InventoryItem), dosage_mg, frequency, duration_days, notes
- `total_quantity` — computed by `calculate_totals()` based on category math
- `dispensed` — True once stock deducted
- `nursing_managed` — True for IV/IM handled by nurse rather than pharmacy

### 3.5 Finance (finance app)
See Section 7.

### 3.6 Home Care (homecare app)
See Section 8.

---

## 4. Module Breakdown

### 4.1 accounts
- `models.py`: Hospital + User
- `views.py`: login redirect + `app_home` router
- Routing: superadmin → developer dashboard; hospital_admin → hospital dashboard; groups → module dashboard; role fallback.

### 4.2 admin_dashboard

Hospital admin features:
- **Users**: create/edit/deactivate/delete staff; module group pill toggles; 10-per-page pagination; password reset.
- **Services**: manage billable services by category.
- **Inventory**: drug catalogue (8 categories), batch tracking, CSV import, FEFO dispensing.
- **Expenses / Salaries**: record operational costs; salary payment auto-posts to ledger.
- **Financials (legacy)**: bank accounts, mobile money, cash drawer, reconciliation statements. These co-exist with the new `finance` app ledger.
- **Reports**: consultation reports, inventory insights.

Developer (superadmin):
- Manage hospitals, subscription plans, subscription payments, audit logs.

### 4.3 reception
- Register patient (smart age/DOB, grouped optional biodata).
- Create visit with services (no upfront payment).
- Pharmacy dispensing window: dispense pending prescriptions, FEFO batch selection.
- Record payments — partial receipts allowed until balance = 0.

### 4.4 nurse
- Nurse queue: triage capture (weight + BP required) + nursing notes.
- IV/IM dispensing via nursing_managed prescriptions.
- Sonographer queue: scan requests from doctor, scan report entry.
- Routing options: → doctor, → reception billing.

### 4.5 doctor
- Doctor queue → consultation form.
- Prescribing: drug search, category-aware label, live quantity preview, optional notes.
- Lab requests, referrals to nurse/billing.
- Scan requests to sonographer queue.

### 4.6 lab
- Lab queue, lab report entry, send results to doctor queue.
- Test profiles (CBC, Urinalysis, etc.) with templated parameters.

### 4.7 finance *(new — 2026-07)*
Full double-entry ledger. See Section 7.

### 4.8 homecare *(new — 2026-07)*
Home care placement management. See Section 8.

---

## 5. URL Routing Map

Platform (admin_dashboard):

```
/platform/superadmin/                        developer dashboard
/platform/hospital/                          hospital admin dashboard
/platform/hospital/users/                   manage staff (paginated, 10/page)
/platform/hospital/users/<id>/edit/
/platform/hospital/users/<id>/deactivate/
/platform/hospital/users/<id>/reset-password/
/platform/hospital/users/<id>/delete/
/platform/hospital/inventory/               drug catalogue
/platform/hospital/services/
/platform/hospital/expenses/
/platform/hospital/salaries/
/platform/hospital/financials/              legacy financial report
```

Reception:

```
/reception/                                 dashboard
/reception/patients/                        list/search
/reception/patients/new/                    register patient
/reception/patients/<id>/visits/            visit history
/reception/patients/<id>/visit/new/         create visit
/reception/complete/<visit_id>/             record payment
/reception/receipt/payment/<id>/            print receipt
```

Doctor:

```
/doctor/                                    doctor queue
/doctor/visit/<visit_id>/consultation/      consultation form
/doctor/api/add-prescription/              AJAX — add prescription
/doctor/api/remove-prescription/<id>/      AJAX — remove prescription
/doctor/api/add-lab-service/               AJAX — on-the-fly lab service
```

Nurse:

```
/nurse/                                     nurse queue
/nurse/queue/<id>/care/                     triage + nursing note form
```

Lab:

```
/lab/                                       lab reports list
/lab/queue/                                 lab queue
/lab/<report_id>/edit/
/lab/<report_id>/send-to-doctor/
```

Finance:

```
/finance/                                   finance dashboard
/finance/journal/                           journal entries (filterable)
/finance/cashbook/
/finance/debtors/
/finance/expenses/
/finance/opening-balances/
/finance/reports/revenue/
/finance/reports/trial-balance/
/finance/reports/profit-loss/
/finance/reports/balance-sheet/
```

Home Care:

```
/homecare/                                  homecare dashboard
/homecare/nurses/                           nurse list
/homecare/nurses/register/
/homecare/nurses/<id>/
/homecare/nurses/<id>/delete/
/homecare/clients/                          client list
/homecare/clients/register/
/homecare/clients/<id>/
/homecare/clients/<id>/delete/
/homecare/placements/
/homecare/placements/create/
/homecare/placements/<id>/
/homecare/placements/<id>/terminate/
/homecare/placements/<id>/receipt/
/homecare/contracts/
/homecare/contracts/<id>/print/
/homecare/receipts/
/homecare/receipts/<id>/print/
```

---

## 6. End-to-End Workflows

### 6.1 Standard Visit Flow

1. Reception registers patient → creates visit with services.
2. If triage service selected → nurse queue entry created.
3. Nurse captures vitals → routes to doctor.
4. Doctor consults → prescribes drugs, requests labs.
5. Lab completes report → sends results back to doctor.
6. Pharmacy (reception) dispenses prescriptions (FEFO batch selection).
7. Reception records payment → visit complete when balance = 0.

Finance signals fire automatically at each billing step (see Section 7).

### 6.2 Prescription Dispensing Path

- Pharmacy (reception): pending prescriptions on visit page → "Dispense Now" → FEFO batch deducted → `dispensed=True`.
- Nurse (IV/IM): `nursing_managed=True` prescriptions appear in nurse queue instead.

---

## 7. Finance Module

Added 2026-07. Full double-entry accounting for each hospital tenant.

### 7.1 Setup

Run once per hospital:

```bash
python manage.py setup_finance
```

This seeds 24 Chart of Accounts entries and backfills all historical VisitServices, Payments, and Expenses as journal entries.

### 7.2 Key Models (`finance/models.py`)

- **Account**: 5 types (Asset, Liability, Equity, Revenue, Expense), sub_type, balance computed from journal lines.
- **JournalEntry**: date, description, source_type, source FKs, `is_reversal`, `reversed_entry` FK. Auto-reference: JNL-YYYYMMDD-NNNN.
- **JournalLine**: entry FK, account FK, debit, credit. `clean()` enforces exactly one per line.

### 7.3 Auto-Posting (signals in `finance/signals.py`)

All posting wrapped in `_safe_post()` — ledger errors never block clinical workflow.

| Event | Debit | Credit |
|---|---|---|
| Visit service added | Accounts Receivable | Category Revenue |
| Payment received | Cash / Bank / Mobile | Accounts Receivable |
| Expense recorded | Expense Account | Cash / Bank / Mobile |
| Salary paid (paid=True) | Staff Salaries (5001) | Bank |
| Any above deleted/edited | Reversal posted (Dr↔Cr swap) | — |

### 7.4 Reversals

A reversal is a mirror-image journal entry — every Debit becomes a Credit and vice versa. The two entries cancel mathematically, but the original and reversal both remain in the audit trail. The system **never edits or deletes** journal entries.

Reversal triggers:
1. Doctor removes a prescription → VisitService deleted → reversal fires.
2. Prescription regimen edited → old VisitService deleted (reversal) + new one posted.
3. Any visit service removed (lab, procedure, consultation fee).
4. Payment voided or waived → cash receipt reversed, A/R balance restored.
5. Expense edited or deleted → old entry reversed, new one posted at corrected amount.

### 7.5 Journal Entry Filters

`GET /finance/journal/` accepts:

```
date_from=2026-07-01   # ISO date, inclusive
date_to=2026-07-10     # ISO date, inclusive
source_type=payment    # visit_charge | payment | expense | manual | reversal
```

Quick-link buttons on page: Today, This Month, This Year. Returns at most 100 entries — narrow the date range to go deeper.

### 7.6 Reports

| Report | URL |
|---|---|
| Finance Dashboard | /finance/ |
| Cashbook | /finance/cashbook/ |
| Debtor Ledger | /finance/debtors/ |
| Revenue Report | /finance/reports/revenue/ |
| Trial Balance | /finance/reports/trial-balance/ |
| Profit & Loss | /finance/reports/profit-loss/ |
| Balance Sheet | /finance/reports/balance-sheet/ |
| Expense Journal | /finance/expenses/ |
| Opening Balances | /finance/opening-balances/ |

---

## 8. Home Care Module

Manages the deployment of home care nurses to private clients.

### 8.1 Models (`homecare/models.py`)

**HomeCareNurse** — nurse registry: name, age, tribe, religion, address, qualification, NIN, contact, notes, is_active.

**HomeCareClient** — client registry: name, location, contact, NIN, notes.

**HomeCarePlacement** — active assignment linking nurse ↔ client:
- service_type: `live_in` (24hr) or `live_out` (10hr)
- rate_period: per day / per week / per month
- nurse_rate (amount paid to nurse), client_rate (amount charged to client)
- contract_start, contract_end, status (active / completed / terminated)
- `margin` = client_rate − nurse_rate
- `total_billed` = SUM of receipts for this placement
- `balance_due` = client_rate − total_billed (floored at 0)

**HomeCareContract** — auto-numbered printable contract (one per placement). Number format: `{INITIALS}{YYYYMMDD}-{NNNN}`. Stores a `terms_snapshot` at generation time — frozen even if rates are later edited.

**HomeCareReceipt** — payment records per placement. Auto-numbered `{INITIALS}{YYYYMMDD}-{NNNN}`. Records: amount_paid, period_covered (e.g. "July 2026"), paid_at.

### 8.2 Workflow

1. Register nurse → register client → create placement (set rates, service type, contract dates).
2. Generate contract (printable PDF-style page).
3. Record receipts as client payments come in.
4. Terminate placement when service ends.

---

## 9. Pharmacy — Inventory Categories & Dispensing Math

### 9.1 Category constants (`admin_dashboard/models.py`)

| Key | Description |
|---|---|
| `drug` | Tablet / capsule |
| `syrup` | Liquid bottle |
| `iv_fluid` | IV bag (Normal Saline, Ringer's, Dextrose, etc.) |
| `iv_med` | IV powder-vial medication (Ceftriaxone, Ampicillin, etc.) |
| `im` | IM injection vial |
| `tube` | Cream / ointment / tube |
| `reagent` | Lab reagent (non-prescribable) |
| `sundry` | Other supplies (non-prescribable) |

`iv` was split into `iv_fluid` + `iv_med` in migration `0016_split_iv_category`. All pre-existing `iv` items were migrated to `iv_fluid`.

### 9.2 Dispensing math (`doctor/models.py :: calculate_totals()`)

| Category | Dose unit | Formula | Dispense unit |
|---|---|---|---|
| drug | mg | ⌈(dose ÷ strength) × freq × days⌉ | tablet |
| syrup | ml | ⌈(dose × freq × days) ÷ ml_per_bottle⌉ | bottle |
| iv_fluid | ml | ⌈(dose × freq × days) ÷ ml_per_bag⌉ | bag |
| iv_med | mg/vial | ⌈(dose ÷ strength_per_vial) × freq × days⌉ | vial |
| im | ml | ⌈(dose × freq × days) ÷ ml_per_vial⌉ | vial |
| tube | application | ⌈days ÷ days_covered_per_tube⌉ | tube |

**iv_med uses the same math branch as tablets.** `is_liquid` on Prescription excludes `iv_med`, so it falls through to the mg/strength formula. `strength_mg_per_unit` stores mg per vial for iv_med.

### 9.3 Concentration fields in inventory form

- `iv_med`: shows "Concentration (mg per vial)" — maps to `strength_mg_per_unit`.
- `iv_fluid`, `syrup`, `im`: shows "Concentration mg/ml — optional" — maps to `concentration_mg_per_ml`.
- Tablets and tubes: concentration field hidden entirely.

### 9.4 Batch tracking (FEFO)

Each stock receipt creates a `BatchItem`. Stock on hand = sum of batch quantities. Dispense always picks the batch expiring soonest first. Pharmacist can override via dropdown when multiple batches exist.

---

## 10. Prescription Notes

`Prescription.notes` — `TextField(blank=True)`. Set by the doctor at creation time. Read-only downstream.

| View | Behaviour |
|---|---|
| Doctor consultation form | Textarea in add-prescription panel. After AJAX save, card renders immediately with "Notes ▾" toggle if notes are present (notes returned in JSON response). |
| Pharmacy / reception | "Notes ▾" collapsible toggle on each pending prescription card. |
| Nurse view | Inline "Notes: …" below the regimen line. |

The AJAX response from `add_prescription_api` includes `"notes": prescription.notes or ""` so the card builder can render the toggle without a page reload.

---

## 11. User Management

Page: `/platform/hospital/users/` — Hospital Admin role required.

### 11.1 Create form features
- Name, username, email, role (required), active status (CSS toggle switch).
- Module access rendered as clickable pill toggles (blue filled = active, grey = inactive). Underlying field is `CheckboxSelectMultiple`; CSS transforms it into a pill UI.
- Password + confirm fields.

### 11.2 Staff card list
- Paginated 10 per page. Pagination controls appear when total > 10.
- Each card: role-coloured avatar circle (initial), full name, username, email, role badge, group pills.
- Actions per card: **Edit** · **Reset Password** · **Deactivate** (if active) · **Delete** (hidden for self).

### 11.3 Password reset (`/platform/hospital/users/<id>/reset-password/`)
- Two password fields with show/hide eye toggle.
- Live strength bar (5 levels: weak → very strong).
- Live match indicator updates as user types.
- Server validates: not empty, both match, ≥ 8 characters.

### 11.4 Delete user (`/platform/hospital/users/<id>/delete/`)
- Confirmation screen with danger note.
- Cannot delete yourself (blocked both in view and hidden in template).
- Permanent — linked records lose their user FK reference. Prefer Deactivate to preserve history.

---

## 12. Deployment Notes

### 12.1 Platform
DigitalOcean App Platform. Database: managed PostgreSQL.

### 12.2 Running management commands
Use the DigitalOcean App Platform console (App → Console tab):

```bash
# Seed finance chart of accounts + backfill historical data
python manage.py setup_finance

# Run migrations after deployment
python manage.py migrate
```

### 12.3 Migration workflow
When the server auto-generates a migration (e.g. from a `makemigrations` run on the server console), replicate it locally before pushing:

```bash
python manage.py makemigrations <app_name>
git add .
git commit -m "replicate server-generated migration"
git push
```

This keeps local and server migration history in sync and avoids `InconsistentMigrationHistory` errors.

---

## Appendix: Shared Print Templates

- `templates/partials/print_header.html` — hospital name, logo, address
- `templates/partials/print_footer.html`
- `nurse/templates/nurse/scan_report_print.html` — sonographer scan report with hospital header
- `homecare/templates/homecare/contract_print.html` — home care contract printout
- `homecare/templates/homecare/receipt_print.html` — home care receipt
