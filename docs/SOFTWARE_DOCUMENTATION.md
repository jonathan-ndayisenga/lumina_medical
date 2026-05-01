# Hospital EMR (Lumina) - Current System Documentation

**Last updated:** 2026-04-24

This document describes the system as it currently stands: modules, key models, cross-module workflows, and the financials/reconciliation features used by the hospital admin.

## 1) Modules At A Glance

### `accounts`
- Multi-tenant core (`Hospital`) and the custom user model (`User`) with role values.
- `Hospital` stores metadata used across the app (name/subdomain/contact + optional logo).

### `reception`
- Patient registration, visit creation, billing completion (payment capture), and receipts printing.
- Core workflow objects: `Patient`, `Visit`, `VisitService`, `QueueEntry`, `Payment`.

### `lab`
- Lab queue, report editing, printing, and integration with the visit workflow.
- Core objects: `LabReport`, `TestProfile`, `TestResult` and templates for profiles (CBC, Urinalysis, Manual Entry, etc.).

### `doctor`
- Doctor queue and consultation notes.
- Doctor can request lab tests during consultation which route into the lab queue.

### `nurse`
- Nurse queue and nurse notes.
- Nurse can route back to doctor or forward to reception for billing readiness.

### `admin_dashboard`
- Superadmin (developer) onboarding of hospitals and plans (separate developer layout).
- Hospital admin operations: users, services/prices, inventory, expenses, salaries.
- Financials: receipts audit, cash drawer, bank statements, mobile money statements, three-way reconciliation.

### `labsystem` (project)
- Django settings, middleware, and global URL routing.

## 2) Tenant + Role Model

### Tenant scoping
- Every operational record is scoped by `Hospital` either directly (`hospital` FK) or indirectly through `Visit.hospital`.
- Request scoping uses `request.hospital` (middleware) and/or `request.user.hospital`.

### Roles (current)
- `superadmin`: platform/developer role (uses developer layout + superadmin tools).
- `hospital_admin`: manages hospital operations + financials.
- `receptionist`: registers patients, creates visits, completes visits, prints receipts.
- `lab_attendant`: manages lab queue, enters lab results, prints lab reports.
- `doctor`: consultations + lab requests.
- `nurse`: nurse notes + routing handoffs.

## 3) End-to-End Patient Workflow (Cross-Module)

### Step A: Reception registers patient and creates a visit
1. Reception creates `Patient` (includes registration date, optional weight).
2. Reception creates `Visit` and selects services.
3. System creates `VisitService` rows and `QueueEntry` rows per service category:
   - Consultation -> doctor queue
   - Lab -> lab queue

### Step B: Care delivery via queues
- Doctor opens the visit via doctor queue and records consultation.
- Doctor may request additional lab services mid-consultation:
  - New lab services can be added and become billable (hospital-scoped service catalog).
  - A lab queue entry is created with a clear `reason` and `requested_by`.
- Lab attendant processes lab queue entries and fills `LabReport` / `TestResult`.
- Nurse records nursing notes and can route back to doctor or forward to billing readiness.

### Step C: Billing completion + receipt
- Reception completes a visit using `Payment` (partial or full).
- Receipt printing uses patient + visit + visit services and shows payment details.

## 4) Financials (Hospital Admin)

The financials section is designed around:
1) audit trail of receipts, and
2) three core statements:
   - Bank statement (external `BankTransaction` vs internal card/mobile receipts)
   - Mobile money statement (external `MobileMoneyTransaction` vs internal mobile receipts)
   - Cash drawer statement (daily open/close with `CashTransaction` in/out)

### 4.1 Receipts Audit (Payments)
- Source of truth: `reception.Payment`
- Tracks: billed amount, amount paid, payment mode, who recorded, and timestamp.
- Receipts list provides filtering and links back to printable receipts.

### 4.2 Cash Drawer
- Source of truth: `admin_dashboard.CashDrawer` and `admin_dashboard.CashTransaction`
- If there is an open cash drawer:
  - Cash payments automatically create/update a `CashTransaction (cash_in)` linked to the `Payment`.
  - Cash-funded expenses automatically create/update a `CashTransaction (cash_out)` linked to the `Expense`.
- Closing calculates:
  - expected = opening + cash_in - cash_out
  - discrepancy = actual_closing - expected

### 4.3 Bank Statements
- External lines: `admin_dashboard.BankTransaction`
- Internal receipts: `reception.Payment` where mode is card/mobile money.
- A statement snapshot is saved as `admin_dashboard.ReconciliationStatement (type=bank)`.

### 4.4 Mobile Money Statements
- External lines: `admin_dashboard.MobileMoneyTransaction`
- Internal receipts: `reception.Payment` where mode is mobile money.
- A statement snapshot is saved as `admin_dashboard.ReconciliationStatement (type=mobile_money)`.

### 4.5 Three-Way Reconciliation
Compares:
- external bank deposits (credits),
- internal receipts totals,
- and patient billed totals for the same period.

## 5) Hosting/Deployment Notes (High Level)

### Recommended target: DigitalOcean App Platform + Managed PostgreSQL
- Use the repository as the app source.
- Attach a managed PostgreSQL database and expose its connection string as `DATABASE_URL`.
- Run `collectstatic` and `migrate` during build/release.

### Production environment variables (minimum)
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=0`
- `DJANGO_ALLOWED_HOSTS` (comma-separated)
- `DJANGO_CSRF_TRUSTED_ORIGINS` (comma-separated, include `https://...` origins)
- `DATABASE_URL` (PostgreSQL connection string; use `sslmode=require` in production)
- Optional hardening:
  - `DJANGO_SECURE_SSL_REDIRECT=1`
  - `DJANGO_SECURE_HSTS_SECONDS=3600`
  - `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1`
  - `DJANGO_SECURE_HSTS_PRELOAD=0`
  - `DB_CONN_MAX_AGE=60`

### Pre-deploy sanity checks (local)
```powershell
cd C:\Users\USER\Desktop\Projects\Lumina_medical_services\labsystem
..\ven\Scripts\python.exe manage.py check
..\ven\Scripts\python.exe manage.py migrate
```

### Static/media
- Static files are collected via Django and served in production via the configured stack.
- Hospital logo is uploaded media (ImageField); ensure `MEDIA_*` is configured for production storage/serving.

## 6) Database ER Diagram

See `docs/ER_DIAGRAM.md`.
