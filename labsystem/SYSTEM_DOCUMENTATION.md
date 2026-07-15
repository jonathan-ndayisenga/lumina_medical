# Ternah Health System (Hospital EMR) — System Documentation

Last Updated: 2026-07-15
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
12. Messaging & Notifications
13. Support Tokens
14. Platform Settings
15. Deployment Notes
16. HTMX Roadmap (Planned)

---

## 1. System Overview

Multi-tenant Hospital EMR. Every record is scoped to a `Hospital` FK — no data crosses between hospitals. The central object is the `Visit`, connecting reception, nursing, doctor, lab, pharmacy, and billing.

Project structure:

```
labsystem/
  accounts/         auth + multi-tenant Hospital/User models; messaging models; platform settings
  admin_dashboard/  hospital admin: inventory, users, services, expenses, salaries, financials
                    superadmin: platform settings, support tokens, notifications, billing
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
- Login view: `accounts.views.RoleAwareLoginView` — template: `registration/login.html`
- Default router after login: `accounts.views.app_home`

### 2.3 Authorization model

Hybrid: single primary role (stored on `User.role`) + optional Django Group membership for multi-module access.

Roles:
- `superadmin` / `developer` — platform-level (DigitalOcean console only)
- `hospital_admin` — manages users, inventory, services, expenses, salaries, messaging
- `doctor` — consultation + prescribing
- `nurse` — triage, nursing notes, IV/IM dispensing
- `receptionist` — patient registration, visits, payments
- `lab_attendant` — lab queue + report entry

Module Groups (additive, stored as Django Groups):
- Reception, Doctor, Nurse, Lab

Module access decorators accept: `(role in allowed set) OR (member of module group)`.

Group seeding migration: `accounts/migrations/0003_create_default_module_groups.py`

### 2.4 Context Processor (`accounts/context_processors.py`)

Runs on every authenticated non-superadmin request. Injects into every template:

| Variable | Description |
|---|---|
| `expiry_alert` | Dict with `days`, `expired`, `urgent`, `level` — shown when subscription is within `reactivation_alert_days` of expiry |
| `unread_notifications` | First 5 unread SystemNotifications for this user |
| `notification_unread_count` | Total unread (broadcast + internal + direct + expiry flag) |
| `message_unread_count` | Same as `notification_unread_count` — drives the navbar envelope badge |
| `token_unread_count` | (Hospital admin only) Count of tokens with unread platform replies |
| `superadmin_open_token_count` | (Superadmin only) Count of open/in-progress support tokens |

Superadmin users return an empty dict — their context comes from the developer dashboard directly.

---

## 3. Core Data Models (ER Summary)

### 3.1 Tenant + users

- `SubscriptionPlan` 1 → N `Hospital`
- `Hospital` 1 → N `User`

`Hospital` metadata used on printouts: location, box_number, phone_number, email, logo.

`Hospital.reactivation_alert_days` — configures how many days before subscription expiry the warning banner appears. Defaults to 7. Set to 0 to disable entirely.

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
- On save with `mode=cash`: looks up or auto-creates today's `CashDrawer` for the hospital using `timezone.localdate(self.paid_at)` (Africa/Kampala, not UTC) and mirrors the receipt as a `CashTransaction`.

### 3.3 Pharmacy (admin_dashboard.InventoryItem)

See Section 9 for category/math detail.

Key fields: name, category, base_unit, units_per_pack, strength_mg_per_unit, concentration_mg_per_ml, current_quantity, selling_price.

### 3.4 Prescription (doctor.Prescription)
- visit (FK), drug (FK → InventoryItem), dosage_mg, frequency, duration_days, notes
- `total_quantity` — computed by `calculate_totals()` based on category math
- `dispensed` — True once stock deducted
- `nursing_managed` — True for IV/IM handled by nurse rather than pharmacy

### 3.5 Messaging models (`accounts/models.py`)

See Section 12 for full details.

#### SystemNotification
Platform-wide or hospital-specific broadcast from the superadmin. Users dismiss individually via `NotificationRead`.

#### InternalNotification
Hospital admin → staff internal bulletin. Recipients: all staff (null) or a specific user. Dismissed via `InternalNotificationRead`.

#### DirectMessage
User-to-user private message within a hospital.
- sender (FK → User), recipient (FK → User), hospital (FK)
- subject (optional), body
- `is_read`, `deleted_by_sender`, `deleted_by_recipient`
- Soft-delete: message hidden per side but not removed from DB until both sides delete.

#### PlatformSettings (singleton, `pk=1`)
Platform-wide feature toggles. Access via `PlatformSettings.get()`. See Section 14.

### 3.6 Support Token models (`accounts/models.py`)

See Section 13 for full details.

#### SupportToken
Filed by a hospital admin to the platform provider.
- hospital (FK), submitted_by (FK → User, nullable), subject, category, status, priority
- `is_open` property: True when status is `open` or `in_progress`
- Ordered by `-updated_at`

#### SupportTokenMessage
One message in a token thread.
- token (FK), sender (FK → User, nullable), body
- `is_from_provider` — True for messages sent by the superadmin
- `read_by_recipient` — tracked per side; False until the other party opens the thread

### 3.7 Finance (finance app)
See Section 7.

### 3.8 Home Care (homecare app)
See Section 8.

---

## 4. Module Breakdown

### 4.1 accounts
- `models.py`: Hospital, User, SystemNotification, NotificationRead, InternalNotification, InternalNotificationRead, DirectMessage, PlatformSettings, SupportToken, SupportTokenMessage
- `views.py`: login redirect, `app_home` router, messages inbox, direct message compose/detail/delete
- `context_processors.py`: notification counts, token badge counts
- Routing: superadmin → developer dashboard; hospital_admin → hospital dashboard; groups → module dashboard; role fallback.

### 4.2 admin_dashboard

Hospital admin features:
- **Users**: create/edit/deactivate/delete staff; module group pill toggles; 10-per-page pagination; password reset.
- **Services**: manage billable services by category.
- **Inventory**: drug catalogue (8 categories), batch tracking, CSV import, FEFO dispensing.
- **Expenses / Salaries**: record operational costs; salary payment auto-posts to ledger.
- **Financials (legacy)**: bank accounts, mobile money, cash drawer, reconciliation statements. These co-exist with the new `finance` app ledger.
- **Reports**: consultation reports, inventory insights.
- **Broadcast**: hospital admin sends internal notifications to all staff.
- **Support Tokens**: file complaints, inquiries, or bug reports to the platform provider. Threaded reply view. Status updated by provider.

Developer (superadmin):
- Manage hospitals, subscription plans, subscription payments, audit logs.
- **Platform Settings**: feature toggles for messaging and data retention.
- **Support Tokens**: view all hospital tickets, reply, update status/priority.
- Dashboard notification card: shows tokens awaiting provider reply.

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

### 4.7 finance *(added 2026-07)*
Full double-entry ledger. See Section 7.

### 4.8 homecare *(added 2026-07)*
Home care placement management. See Section 8.

---

## 5. URL Routing Map

Platform (admin_dashboard) — prefix `/platform/`:

```
# Superadmin
/platform/superadmin/                              developer dashboard
/platform/superadmin/hospitals/                    hospital list
/platform/superadmin/hospitals/<id>/edit/
/platform/superadmin/hospitals/<id>/delete/
/platform/superadmin/hospitals/<id>/toggle/
/platform/superadmin/hospitals/<id>/generate-invoice/
/platform/superadmin/hospitals/<id>/invoices/
/platform/superadmin/invoices/<id>/print/
/platform/superadmin/invoices/
/platform/superadmin/receipts/
/platform/superadmin/receipts/<id>/print/
/platform/superadmin/modules/
/platform/superadmin/modules/<id>/edit/
/platform/superadmin/subscription-plans/
/platform/superadmin/subscription-plans/<id>/edit/
/platform/superadmin/subscription-plans/<id>/delete/
/platform/superadmin/subscription-payments/
/platform/superadmin/subscription-payments/<id>/edit/
/platform/superadmin/subscription-payments/<id>/delete/
/platform/superadmin/audit-logs/
/platform/superadmin/notifications/
/platform/superadmin/notifications/<id>/delete/
/platform/superadmin/settings/                     PlatformSettings singleton edit
/platform/superadmin/tokens/                       support token list (filterable)
/platform/superadmin/tokens/<pk>/                  token thread + reply + status update

# Hospital Admin
/platform/hospital/                                hospital admin dashboard
/platform/hospital/users/                         manage staff (paginated, 10/page)
/platform/hospital/users/<id>/edit/
/platform/hospital/users/<id>/deactivate/
/platform/hospital/users/<id>/reset-password/
/platform/hospital/users/<id>/delete/
/platform/hospital/services/
/platform/hospital/services/<id>/edit/
/platform/hospital/services/<id>/delete/
/platform/hospital/expenses/
/platform/hospital/expenses/<id>/edit/
/platform/hospital/expenses/<id>/delete/
/platform/hospital/salaries/
/platform/hospital/salaries/<id>/edit/
/platform/hospital/salaries/<id>/delete/
/platform/hospital/inventory/
/platform/hospital/inventory/insights/
/platform/hospital/inventory/template/
/platform/hospital/inventory/upload/
/platform/hospital/inventory/report/
/platform/hospital/inventory/<id>/restock/
/platform/hospital/inventory/<id>/edit/
/platform/hospital/inventory/<id>/delete/
/platform/hospital/reports/
/platform/hospital/reports/consultations/
/platform/hospital/financials/
/platform/hospital/financials/bank-accounts/...    (bank, mobile money, receipts)
/platform/hospital/broadcast/                      send internal notification to staff
/platform/hospital/broadcast/<pk>/delete/
/platform/hospital/tokens/                         support token list (hospital side)
/platform/hospital/tokens/new/                     file new support token
/platform/hospital/tokens/<pk>/                    token thread + reply (hospital side)
```

Messaging (accounts) — prefix `/accounts/`:

```
/accounts/messages/                               unified inbox (3 tabs)
/accounts/messages/mark-read/<pk>/               mark broadcast notification read
/accounts/messages/internal/<pk>/mark-read/      mark internal notification read
/accounts/messages/compose/                       compose direct message
/accounts/messages/<pk>/                          direct message detail
/accounts/messages/<pk>/delete/                   soft-delete direct message
```

Reception:

```
/reception/                                       dashboard
/reception/patients/                              list/search
/reception/patients/new/                          register patient
/reception/patients/<id>/visits/                  visit history
/reception/patients/<id>/visit/new/               create visit
/reception/complete/<visit_id>/                   record payment
/reception/receipt/payment/<id>/                  print receipt
```

Doctor:

```
/doctor/                                          doctor queue
/doctor/visit/<visit_id>/consultation/            consultation form
/doctor/api/add-prescription/                    AJAX — add prescription
/doctor/api/remove-prescription/<id>/            AJAX — remove prescription
/doctor/api/add-lab-service/                     AJAX — on-the-fly lab service
```

Nurse:

```
/nurse/                                           nurse queue
/nurse/queue/<id>/care/                          triage + nursing note form
```

Lab:

```
/lab/                                             lab reports list
/lab/queue/                                       lab queue
/lab/<report_id>/edit/
/lab/<report_id>/send-to-doctor/
```

Finance:

```
/finance/                                         finance dashboard
/finance/journal/                                 journal entries (filterable)
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
/homecare/                                        homecare dashboard
/homecare/nurses/                                 nurse list
/homecare/nurses/register/
/homecare/nurses/<id>/
/homecare/nurses/<id>/delete/
/homecare/clients/
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

### 6.3 Support Token Flow

1. Hospital admin opens `Support Tokens` from sidebar → clicks `+ New Token`.
2. Selects category (Complaint / Inquiry / Bug Report / Feature Request / Other), enters subject and initial message body → submits.
3. `SupportToken` (status=`open`) + first `SupportTokenMessage` (is_from_provider=False) created.
4. Superadmin's dashboard shows an amber notification card for unread hospital messages.
5. Superadmin opens the token → thread renders → hospital messages are auto-marked `read_by_recipient=True` on open.
6. Superadmin replies → a new `SupportTokenMessage` (is_from_provider=True, read_by_recipient=False) is created; token status advances to `in_progress`.
7. Hospital admin's dashboard shows an indigo notification card for unread provider replies.
8. Hospital admin opens the token → provider replies auto-marked read.
9. Superadmin can update status to `resolved` or `closed` via the status form.
10. Hospital admin can add a reply (re-opens the token if closed).

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

Quick-link buttons on page: Today, This Month, This Year. Returns at most 100 entries.

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

## 12. Messaging & Notifications

Added 2026-07. Three-layer messaging system across the platform.

### 12.1 Layer 1 — System Broadcast (Superadmin → all or one hospital)

**Model**: `accounts.SystemNotification`

Fields: title, body, hospital (FK, nullable — null = platform-wide), is_active, created_at.

Dismissal: per-user via `NotificationRead` (user FK + notification FK, unique together).

Managed from `/platform/superadmin/notifications/`. Hospital admins and staff see active broadcasts in their messages inbox under the **Broadcast** tab.

**Gating**: always visible regardless of `PlatformSettings`.

### 12.2 Layer 2 — Internal Broadcast (Hospital admin → hospital staff)

**Model**: `accounts.InternalNotification`

Fields: hospital (FK), sender (FK → User), recipient (FK → User, nullable — null = all staff), subject, body, is_active, created_at.

Dismissal: per-user via `InternalNotificationRead`.

Sent from `/platform/hospital/broadcast/`. Staff see active internal notifications in their inbox under the **Internal** tab.

**Gating**: `PlatformSettings.internal_messages_enabled` — if False, tab is hidden and unread count is zeroed.

### 12.3 Layer 3 — Direct Messages (User → User, same hospital)

**Model**: `accounts.DirectMessage`

Fields: hospital (FK), sender (FK → User), recipient (FK → User), subject (blank=True), body, is_read, deleted_by_sender, deleted_by_recipient, created_at.

Soft-delete pattern: the row is never deleted; `deleted_by_sender` or `deleted_by_recipient` hides it from that user's view. When both sides delete, the message is logically gone.

Compose at `/accounts/messages/compose/`. Staff dropdown pre-filtered to the same hospital. Recipient pre-selectable via `?to=<pk>`.

**Gating**: `PlatformSettings.direct_messages_enabled` — if False, compose button hidden, tab hidden, unread count zeroed.

### 12.4 Unified Inbox (`/accounts/messages/`)

Three tabs on a single page:

| Tab | Source | Gating |
|---|---|---|
| Broadcast | SystemNotification | Always visible |
| Internal | InternalNotification | `ps.internal_messages_enabled` |
| Private | DirectMessage | `ps.direct_messages_enabled` |

Each tab paginates its own queryset (10 per page) independently using `?tab=broadcast|internal|private`.

### 12.5 Navbar envelope badge

`base.html` contains a mail SVG icon linking to `/accounts/messages/`. A red badge overlays when `message_unread_count > 0`. The count is computed by the context processor on every request.

### 12.6 Message purge command

```bash
python manage.py purge_old_messages           # uses PlatformSettings.message_retention_days
python manage.py purge_old_messages --days 30 # override
python manage.py purge_old_messages --days 0  # skip (no-op)
```

Deletes `SystemNotification`, `InternalNotification` (+ associated reads), and `DirectMessage` older than the configured retention period. Safe to run as a scheduled task.

### 12.7 Migration chain (accounts app)

```
0012 → 0013 → 0014_direct_messages
                   └→ 0015_platform_settings
                          └→ 0016_support_tokens
```

---

## 13. Support Tokens

Added 2026-07. Hospital admins file support tickets ("tokens") directly to the platform provider (superadmin). Supports threaded conversation, status tracking, and priority escalation.

### 13.1 Models

**`accounts.SupportToken`**

| Field | Type | Notes |
|---|---|---|
| hospital | FK → Hospital | Which hospital filed it |
| submitted_by | FK → User (nullable) | Hospital admin who submitted |
| subject | CharField(200) | Short description |
| category | CharField | complaint / inquiry / bug_report / feature_request / other |
| status | CharField | open / in_progress / resolved / closed |
| priority | CharField | low / normal / high / urgent |
| created_at | DateTimeField | auto |
| updated_at | DateTimeField | auto — used for "last activity" ordering |

`is_open` property returns True when status is `open` or `in_progress`.

**`accounts.SupportTokenMessage`**

| Field | Type | Notes |
|---|---|---|
| token | FK → SupportToken | Parent token |
| sender | FK → User (nullable) | Who sent this message |
| body | TextField | Message content |
| is_from_provider | BooleanField | True = sent by superadmin |
| read_by_recipient | BooleanField | False until the other party opens the thread |
| created_at | DateTimeField | auto, ordered ascending |

### 13.2 Hospital admin views

| View | URL | Description |
|---|---|---|
| `hospital_token_list` | `/platform/hospital/tokens/` | Filterable by status (open / resolved). Shows category, priority, status badges. |
| `hospital_token_create` | `/platform/hospital/tokens/new/` | Form: category + subject + initial message body. Creates token + first message. |
| `hospital_token_detail` | `/platform/hospital/tokens/<pk>/` | Thread view. Reply form shown only while token is open. Provider replies highlighted with indigo left border. On open: provider replies marked `read_by_recipient=True`. |

### 13.3 Superadmin views

| View | URL | Description |
|---|---|---|
| `superadmin_tokens` | `/platform/superadmin/tokens/` | All tokens across all hospitals. Tabs: Open/In Progress vs Resolved/Closed. Columns: hospital, subject, category, priority, status, message count, last update. |
| `superadmin_token_detail` | `/platform/superadmin/tokens/<pk>/` | Thread + inline status/priority update form + reply-as-provider form. On open: hospital messages marked `read_by_recipient=True`. |

### 13.4 Dashboard notifications

**Superadmin dashboard** (`developer_dashboard`): amber notification card appears when any token has unread hospital messages (hospital messaged, provider hasn't replied yet). Shows subject, hospital, priority badge, timestamp. Disappears when all tokens have been read.

**Hospital admin dashboard** (`hospital_dashboard`): indigo notification card appears when any of the hospital's tokens have an unread provider reply. Shows subject, status badge, timestamp. Disappears when opened.

### 13.5 Badge counts

- Navbar (hospital admin side): `token_unread_count` — count of tokens with `is_from_provider=True, read_by_recipient=False` for this hospital. Shown on "Support Tokens" sidebar link.
- Superadmin sidebar: `superadmin_open_token_count` — count of all `open` + `in_progress` tokens. Shown on "Support Tokens" nav link.

Both injected by `accounts/context_processors.py`.

---

## 14. Platform Settings

Added 2026-07. Singleton model controlling platform-wide feature toggles.

### 14.1 Model (`accounts.PlatformSettings`)

Always exactly one row, `pk=1`. Access via `PlatformSettings.get()` which calls `get_or_create(pk=1)`.

| Field | Type | Default | Description |
|---|---|---|---|
| broadcast_enabled | BooleanField | True | System-wide broadcast notifications |
| internal_messages_enabled | BooleanField | True | Hospital-admin-to-staff internal bulletins |
| direct_messages_enabled | BooleanField | True | User-to-user private messages |
| message_retention_days | PositiveSmallIntegerField | 7 | Days before old messages are purged (0 = never) |

### 14.2 Admin UI

URL: `/platform/superadmin/settings/`

Rendered as a toggle-switch form (custom CSS `.sw` track+thumb pattern). Superadmin only. Changes take effect immediately on save.

### 14.3 How gating works

- Context processor reads `PlatformSettings.get()` on every non-superadmin request.
- If `direct_messages_enabled=False`: direct message tab hidden, compose button hidden, unread count for direct messages = 0.
- If `internal_messages_enabled=False`: internal tab hidden, unread count for internal messages = 0.
- If both are False: only the broadcast tab is visible in the inbox.
- Purge command reads `message_retention_days` — set to 0 to disable auto-purge.

---

## 15. Deployment Notes

### 15.1 Platform
DigitalOcean App Platform. Database: managed PostgreSQL.

### 15.2 Running management commands
Use the DigitalOcean App Platform console (App → Console tab):

```bash
# Seed finance chart of accounts + backfill historical data
python manage.py setup_finance

# Run migrations after deployment
python manage.py migrate

# Purge messages older than the configured retention period
python manage.py purge_old_messages

# Override retention period (e.g., purge messages older than 30 days)
python manage.py purge_old_messages --days 30
```

### 15.3 Migration workflow
When the server auto-generates a migration (e.g. from a `makemigrations` run on the server console), replicate it locally before pushing:

```bash
python manage.py makemigrations <app_name>
git add .
git commit -m "replicate server-generated migration"
git push
```

This keeps local and server migration history in sync and avoids `InconsistentMigrationHistory` errors.

### 15.4 Known timezone requirement

The application is configured for `TIME_ZONE = "Africa/Kampala"` (UTC+3) with `USE_TZ = True`. All date-sensitive business logic must use `timezone.localdate()` rather than `timezone.now().date()` to avoid off-by-one date bugs in the 0:00–3:00 UTC window (= 3:00–6:00 Kampala time). The `CashDrawer` date lookup in `reception/models.py::Payment.save()` was corrected from `timezone.now().date()` to `timezone.localdate(self.paid_at)` for this reason.

---

## 16. HTMX Roadmap (Planned)

Not yet implemented. This section records the plan for progressively adding HTMX to eliminate full-page reloads.

### 16.1 What problem it solves

Currently every navigation link, form submit, and pagination click reloads the entire page — including sidebar, Tailwind CDN, Chart.js, and all static assets. HTMX allows only the content region to swap while the shell stays mounted.

### 16.2 Key decision: partial templates

HTMX requires views to return HTML fragments (not full pages) when called via HTMX. The cleanest pattern:

```python
# In any view:
if request.headers.get("HX-Request"):
    return render(request, "partials/token_table.html", ctx)
return render(request, "admin_dashboard/hospital_token_list.html", ctx)
```

The `django-htmx` package adds `request.htmx` (a typed attribute) and `trigger_client_event()` for toast notifications — recommended over raw header checks.

### 16.3 Proposed rollout phases

| Phase | Change | Complexity |
|---|---|---|
| 0 | Add HTMX via CDN to `base.html` | Trivial |
| 1 | `hx-boost="true"` on `<nav>` — navigation feels instant | Low |
| 2 | Pagination on all tables (hx-get + hx-target on page links) | Low |
| 3 | Inline status updates, mark-read, soft-delete (swap row in place) | Low |
| 4 | Notification badge polling (`hx-trigger="every 60s"`) | Low |
| 5 | Modal forms for new token / compose message | Medium |
| 6 | Live search / filter on hospital and patient lists | Medium |
| 7 | Dashboard stat card polling | Medium |

Each phase is independently shippable and reversible.

### 16.4 Known integration points to handle

**Chart.js re-init on `hx-boost` swap** — charts only initialise when their `<canvas>` element exists in the DOM. After a boosted navigation, re-init must fire:

```javascript
document.addEventListener("htmx:afterSwap", function () {
    if (document.getElementById("modulePieChart")) initCharts();
});
```

**Django messages framework** — `messages.success(...)` is designed for the redirect-then-render cycle. With HTMX eliminating redirects on inline actions, switch to `HX-Trigger` response headers to fire a client-side `showToast` event instead.

**CSRF** — one meta tag in `base.html` covers all `hx-post` requests:

```html
<meta name="htmx-config"
      content='{"antiForgery":{"headerName":"X-CSRFToken","cookie":"csrftoken"}}'>
```

---

## Appendix: Shared Print Templates

- `templates/partials/print_header.html` — hospital name, logo, address
- `templates/partials/print_footer.html`
- `nurse/templates/nurse/scan_report_print.html` — sonographer scan report with hospital header
- `homecare/templates/homecare/contract_print.html` — home care contract printout
- `homecare/templates/homecare/receipt_print.html` — home care receipt
