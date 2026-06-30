# Home Care Management Module — Documentation

## 1. Purpose

A new sellable platform module aimed at **personal/independent doctors** who run a home-care nursing business — placing nurses with private clients for live-in (24hr) or live-out (10hr) care, rather than operating a walk-in clinic.

It is priced and sold the same way every other module is: **50,000/month**, toggled on a per-hospital basis at onboarding or later by the superadmin.

## 2. Relationship to the Existing Architecture

This module is built entirely on top of the **module/subscription system** already in place (`accounts.Module`, `accounts.HospitalModuleSubscription`, the `can_access_X` gating pattern, and the workflow-routing gate). No new platform-level mechanism is needed — Home Care Management is simply a 7th row in the `Module` table.

| Existing piece | How Home Care uses it |
|---|---|
| `Hospital` model (name, logo, address, phone, email) | Reused as-is for contract and receipt headers — no new header data needed |
| `Module` + `HospitalModuleSubscription` | Home Care becomes `code="home_care"`, `monthly_price=50000`, `is_core=False` |
| `can_access_X` property pattern on `User` | New `can_access_home_care` property, same shape as `can_access_inventory`/`can_access_finance` |
| `*_access_required` decorator pattern | New `home_care_access_required` decorator |
| Sidebar gating pattern in `base.html` | New nav block, gated by `user.can_access_home_care` |
| Receipt template pattern (`reception/payment_receipt.html`) | Visually cloned for the client receipt — same headed layout, different line items |
| Chart.js dashboard pattern (used 3× already: financial_report, inventory_insights, developer_dashboard) | Reused for the Home Care dashboard's finance-over-time chart |

### The one exception this module requires

Every other module's hospital is **forced to also have Reception** (`is_core=True` modules are auto-included regardless of what's unchecked — this was a deliberate platform rule: "Reception is the entry point for every facility"). Home Care Management breaks that rule by design — a personal doctor's home-care business has no walk-in patients, no `Visit`/`QueueEntry` workflow, no use for Reception at all.

**Resolution (confirmed):** Home Care Management is exempted from the core-module auto-inclusion. A hospital that buys *only* Home Care does not get Reception forced onto it. This requires one small change to `HospitalForm.save_module_subscriptions()` — when the selected module set is `{home_care}` alone (or `home_care` is present and no clinical modules are selected), Reception is not force-injected.

If, in the future, the business wants to integrate nurse logins or tie Home Care into the clinical side, Reception can be manually added back for that hospital with zero migration needed — the carve-out is a business-rule exception, not a structural wall.

## 3. New Data Model (none of this exists yet — proposed)

A new Django app, suggested name: `homecare`.

### `HomeCareNurse`
Profile record only — **no login account** for now (confirmed: pure record managed by the doctor; revisit if nurse self-service is ever needed).

| Field | Type | Notes |
|---|---|---|
| hospital | FK → Hospital | tenant scope |
| name | CharField | |
| age | PositiveIntegerField | |
| tribe | CharField | |
| religion | CharField | |
| address | CharField | |
| qualification | CharField | |
| nin | CharField | National ID |
| contact | CharField | phone |
| is_active | BooleanField | for availability tracking |
| created_at | DateTimeField | |

### `HomeCareClient`

| Field | Type | Notes |
|---|---|---|
| hospital | FK → Hospital | tenant scope |
| name | CharField | |
| location | CharField | |
| contact | CharField | |
| nin | CharField | |
| created_at | DateTimeField | |

### `HomeCarePlacement` — the central record

Links a nurse to a client for a contract period. Both the contract and the receipt are generated *from* a placement.

| Field | Type | Notes |
|---|---|---|
| hospital | FK → Hospital | |
| client | FK → HomeCareClient | |
| nurse | FK → HomeCareNurse | |
| service_type | Choice | `live_in` (24hr) / `live_out` (10hr) |
| nurse_rate | DecimalField | what the nurse is paid |
| client_rate | DecimalField | what the client is billed |
| contract_start | DateField | |
| contract_end | DateField | |
| status | Choice | active / completed / terminated |
| created_by | FK → User | the doctor |
| created_at | DateTimeField | |

### `HomeCareContract`

| Field | Type | Notes |
|---|---|---|
| placement | FK → HomeCarePlacement | |
| contract_number | CharField | auto-generated, unique |
| generated_at | DateTimeField | |
| terms_snapshot | TextField | frozen copy of rate/dates at generation time, so later rate edits don't retroactively alter an issued contract |

### `HomeCareReceipt`

| Field | Type | Notes |
|---|---|---|
| placement | FK → HomeCarePlacement | |
| receipt_number | CharField | auto-generated, unique |
| amount_paid | DecimalField | |
| paid_at | DateTimeField | |
| recorded_by | FK → User | |

## 4. Sidebar / Navigation

```
Home Care Management
├── Dashboard          (finance chart over time, active nurse count, recent placements)
├── Register Client
├── Register Nurse
├── Placements         (active/ended assignments — the operational list view)
├── Contracts          (generated contract log)
└── Receipts           (issued client receipts)
```

## 5. Open Items for a Future Iteration (not blocking initial build)

- Nurse self-service login (currently explicitly out of scope, flagged for "future" by the business owner)
- Automatic contract renewal/expiry reminders
- PDF export of contracts (initial version can be print-friendly HTML, same as existing receipts)
