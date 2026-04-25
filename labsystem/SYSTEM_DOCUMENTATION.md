# Lumina Medical Services (Hospital EMR) - System Documentation

Last Updated: 2026-04-25
Django Version: 6.0.3
Primary DB: SQLite (development) / PostgreSQL (production via DATABASE_URL)

This document describes the current state of the codebase (models, modules, routing, and workflows).

## Table of Contents

1. System Overview
2. Authentication, Authorization, and Multi-Tenancy
3. Core Data Models (ER Summary)
4. Module Breakdown
5. URL Routing Map
6. End-to-End Workflows
7. Financial Sync Rules (Receipts -> Statements)

---

## 1. System Overview

The system is a multi-tenant Hospital EMR centered on a single "Visit" object that connects:

- Reception: patient registration, visit creation, billing/receipts
- Nurse: shared triage (vitals) and nursing notes, queue handoff
- Doctor: consultation, lab requests, queue visibility
- Lab: lab queue, lab reports tied to visit, send results back to doctor
- Hospital Admin: user management, services/pricing, accounting, financial statements and reconciliation tooling
- Developer (Superadmin): platform-level hospital onboarding and subscription management

Project structure (high level):

```
labsystem/
  accounts/         auth + multi-tenant hospital/user models
  admin_dashboard/  developer + hospital admin operations (services, expenses, salaries, financials)
  reception/        patients, visits, billing (receipts), queue routing
  nurse/            triage + nursing notes + handoff
  doctor/           consultation + lab request workflow
  lab/              lab reports + lab queue
  templates/        shared templates (base + shared print partials)
  static/           static assets
```

---

## 2. Authentication, Authorization, and Multi-Tenancy

### 2.1 Multi-tenancy

- Tenant root model: `accounts.Hospital`
- Tenant selection middleware: `labsystem.middleware.HospitalMiddleware`
  - Typically sets `request.hospital` by subdomain (production) or by `request.user.hospital` (local/dev usage).

### 2.2 Authentication

- Custom user model: `accounts.User` (extends `AbstractUser`)
- Login view: `accounts.views.RoleAwareLoginView`
- Default landing/router: `accounts.views.app_home` (also used by `LOGIN_REDIRECT_URL`)

### 2.3 Authorization model (current transitional state)

The system currently supports BOTH:

1) **Single primary role** (stored on `User.role`)
2) **Optional multi-module access** via Django `Group` membership

Module Groups currently used:

- Reception
- Lab
- Doctor
- Nurse

Important notes:

- Roles still exist and remain the primary policy for superadmin/hospital_admin.
- Groups are additive: they allow one user to access multiple modules without replacing roles yet.
- Module access decorators now accept: (role in allowed set) OR (member of module group).

Where this is implemented:

- Reception gating: `reception.views.reception_role_required`
- Doctor gating: `doctor.views.doctor_role_required`
- Nurse gating: `nurse.views.nurse_role_required`
- Lab gating: `lab.views.staff_required` (role OR group OR staff)

### 2.4 Group seeding / legacy mapping

A data migration creates default module groups and assigns existing users based on their `role`:

- `accounts/migrations/0003_create_default_module_groups.py`

---

## 3. Core Data Models (ER Summary)

### 3.1 Tenant + users

- `SubscriptionPlan` 1 -> N `Hospital`
- `Hospital` 1 -> N `User`

`Hospital` includes extended metadata used on printouts:

- location, box_number, phone_number, email, logo

### 3.2 Reception / clinical hub models

#### Patient (`reception.Patient`)

- hospital (FK)
- name (string)
- registration_date (date, required in UI)
- date_of_birth (date, optional)
- age (string, stored as "22YRS" or "6MTH")
- sex, contact, weight_kg
- optional biodata: email, address, next_of_kin, next_of_kin_contact, nin, id_verified, insurance_provider, insurance_policy_number

Validation rules (form-layer):

- Either `date_of_birth` OR age (value+unit) is required.
- Form synchronizes DOB <-> age:
  - If DOB is entered: age is computed and stored.
  - If age is entered: DOB is approximated (Jan 1 for years, 1st of month for months).

#### Visit (`reception.Visit`)

- patient (FK), hospital (FK)
- visit_date (auto)
- status:
  - in_progress
  - ready_for_billing
  - completed
  - cancelled
- total_amount (sum of services at time of creation / edits)
- created_by (FK to User)

Computed properties:

- `Visit.total_paid`: SUM(payments.amount_paid) excluding waived
- `Visit.balance_due`: total_amount - total_paid
- `Visit.is_fully_paid`: balance_due <= 0

#### Triage (`reception.Triage`)

Shared vital signs per visit (doctor + nurse share the same record):

- visit (OneToOne)
- weight_kg, BP sys/dia, pulse, resp_rate, temp, spo2, glucose
- recorded_by/updated_by + timestamps

Minimum required (nurse sign-off):

- weight_kg + bp_systolic + bp_diastolic

Legacy migration:

- Prior doctor vitals stored in `doctor.Consultation.vitals` JSON are migrated into `Triage`
  - `reception/migrations/0010_migrate_consultation_vitals_to_triage.py`

#### Service (`reception.Service`)

Billable items (hospital-scoped):

- hospital (FK)
- name (unique per hospital)
- category:
  - consultation
  - lab
  - triage
  - procedure
  - pharmacy
  - other
- price, is_active
- optional `test_profile` (FK to `lab.TestProfile`) for lab services

#### VisitService (`reception.VisitService`)

Join table for visit billing:

- visit (FK)
- service (FK)
- price_at_time
- performed (bool) + notes

#### QueueEntry (`reception.QueueEntry`)

Unified queue system (hospital-scoped):

- hospital (FK), visit (FK)
- queue_type:
  - lab_reception
  - lab_doctor
  - doctor
  - nurse
- reason (text)
- requested_by (FK to User, optional)
- processed + timestamps + notes

#### Payment (`reception.Payment`)

Receipts are stored as individual Payment rows (supports partial payments):

- visit (FK)  (NOTE: one visit can have MANY payments)
- amount (billed total at time of receipt; used for printing context)
- amount_paid (receipt amount)
- mode: cash / card / mobile_money / insurance
- bank_account (FK, required when mode=card)
- mobile_account (FK, required when mode=mobile_money)
- recorded_by (FK), paid_at, notes

Receipt identification:

- `Payment.receipt_number` property (derived from date + PK)

Cash drawer mirroring:

- On save, if mode=cash and there is an open cash drawer, a matching `CashTransaction` (cash_in) is created/updated.

Important: reconciliation statement lines are NOT auto-created from receipts for bank/mobile.
Instead, external statement lines are entered/imported under financials and matched against receipts.

### 3.3 Lab models (hospital + visit scoped)

- `lab.LabReport` is tied to `Visit` (FK) and hospital (FK) and contains many `TestResult`.
- `lab.TestProfile` + `TestProfileParameter` define templates (CBC, Urinalysis, etc).
- `lab.TestCatalog` is the test dictionary used by results.

### 3.4 Financial / accounting models (hospital admin)

Core:

- `admin_dashboard.HospitalAccount` (one per hospital; balance synced from receipts/expenses/salaries)
- `admin_dashboard.Expense` (supports tracking payout source: bank/mobile/cash drawer)
- `admin_dashboard.Salary`

Statements / reconciliation inputs:

- `admin_dashboard.BankAccount`
- `admin_dashboard.MobileMoneyAccount`
- `admin_dashboard.CashDrawer` + `admin_dashboard.CashTransaction`
- `admin_dashboard.BankTransaction` (external statement line, can be matched to a Payment)
- `admin_dashboard.MobileMoneyTransaction` (external statement line, can be matched to a Payment)
- `admin_dashboard.ReconciliationStatement` (generated statement summaries)

---

## 4. Module Breakdown

### 4.1 accounts

Key files:

- `accounts/models.py`: Hospital + User
- `accounts/views.py`: login redirect + `app_home` router

Routing logic:

- Superadmin -> developer dashboard
- Hospital admin -> hospital dashboard
- Else: groups (Reception/Doctor/Lab/Nurse) -> module dashboard
- Else: role-based fallback

### 4.2 admin_dashboard

Hospital admin:

- Users: create/edit/deactivate users; now supports selecting multiple module groups
- Services: manage billable services (including triage category)
- Financials:
  - Financial report
  - Financial statements (bank/mobile/cash drawer statements)
  - Receipts list (audit trail of Payment receipts)
  - Bank accounts + Mobile money settings
  - Cash drawer open/close + transactions
  - Bank/mobile external transaction entry + matching
  - Expenses + salaries + inventory

Developer (superadmin):

- Manage hospitals, subscription plans, subscription payments, audit logs
- Dedicated developer base navigation templates

### 4.3 reception

Key workflows:

- Register patient (smart age/DOB + grouped optional biodata)
- Create visit with services (NO upfront payment)
  - services add to bill
  - queue entries created per service category (doctor/lab/nurse triage)
- Billing/receipts:
  - `complete_visit` records a Payment receipt and prints it
  - partial receipts are allowed until balance is 0

### 4.4 nurse

- Nurse queue shows queued patients for triage and nursing work
- Nurse form includes triage capture (required weight + BP) and optional notes
- Nurse can route:
  - triage -> doctor
  - triage -> reception (if desired)
  - nursing -> doctor
  - nursing -> reception billing

### 4.5 doctor

- Doctor queue shows active queue entries
- Consultation form supports:
  - shared triage fields (writes to Triage unless "Send to Nurse" is checked)
  - requesting additional lab services (dropdown + "Add other" creates new Service)
  - routing to nurse or billing

### 4.6 lab

- Lab queue shows reason + requested_by
- Lab report entry tied to Visit
- "Send to doctor" action sends results back by creating/keeping a doctor queue entry (reason includes "Lab results ready...")

---

## 5. URL Routing Map (Current)

Root:

- `/` -> login (accounts)
- `/home/` -> role/group router (`app_home`)

Platform (admin_dashboard):

- `/platform/superadmin/` -> developer dashboard
- `/platform/hospital/` -> hospital admin dashboard
- `/platform/hospital/financials/` -> financial report
- `/platform/hospital/financials/statements/` -> unified statements
- `/platform/hospital/financials/receipts/` -> receipts list
- `/platform/hospital/financials/bank-accounts/` -> bank accounts
- `/platform/hospital/financials/mobile-money/` -> mobile accounts
- `/platform/hospital/financials/cash-drawer/` -> cash drawer

Reception:

- `/reception/` -> dashboard
- `/reception/patients/` -> list/search
- `/reception/patients/new/` -> register patient
- `/reception/patients/<patient_id>/visits/` -> visit history
- `/reception/patients/<patient_id>/visit/new/` -> create visit
- `/reception/complete/<visit_id>/` -> record payment (receipt)
- `/reception/receipt/payment/<payment_id>/` -> print a specific receipt

Doctor:

- `/doctor/` -> doctor queue
- `/doctor/visit/<visit_id>/consultation/` -> consultation form
- `/doctor/api/add-lab-service/` -> create lab service on-the-fly

Nurse:

- `/nurse/` -> nurse queue
- `/nurse/queue/<queue_entry_id>/care/` -> triage + nursing note form

Lab:

- `/lab/` -> lab reports list
- `/lab/queue/` -> lab queue
- `/lab/<report_id>/edit/` -> edit report
- `/lab/<report_id>/send-to-doctor/` -> send results to doctor

---

## 6. End-to-End Workflows

### 6.1 Reception -> Nurse (Triage) -> Doctor -> (Lab optional) -> Billing

1) Reception registers patient
2) Reception creates visit and selects services
   - If a triage service is selected, a nurse queue entry is created
3) Nurse captures triage (required weight + BP)
   - Nurse sends patient to doctor queue
4) Doctor consults
   - Can request additional lab services (adds to bill + lab queue)
5) Lab completes report
   - Sends results back to doctor queue
6) Reception records payment(s)
   - Each payment creates a receipt (Payment row)
   - Visit becomes completed only when balance_due reaches 0

---

## 7. Financial Sync Rules (Receipts -> Statements)

### 7.1 Receipts list is the source of truth for internal income

Internal income is computed from:

- SUM(`Payment.amount_paid`) per hospital per time period

Receipts always show:

- payment mode (cash/card/mobile)
- the account used (bank account or mobile money account when applicable)
- recorded_by + timestamp

### 7.2 Cash drawer syncing

When a cash receipt is recorded and a drawer is open:

- A `CashTransaction(cash_in)` is created/updated for that Payment.

### 7.3 Bank/mobile reconciliation

External statement lines are stored separately:

- Bank: `BankTransaction`
- Mobile: `MobileMoneyTransaction`

Reconciliation matches those external credits against internal receipts (`Payment`) using:

- receipt reference first, then amount/date fallback (as implemented in admin_dashboard reconciliation flows)

---

Appendix: Shared Print Templates

Shared print header/footer partials used by receipts:

- `templates/partials/print_header.html`
- `templates/partials/print_footer.html`

