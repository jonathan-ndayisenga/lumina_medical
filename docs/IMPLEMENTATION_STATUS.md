# Ternah EMR — Implementation Status
*Last updated: July 2026*

---

## ✅ IMPLEMENTED

### Core Platform
- [x] Multi-tenant architecture (Hospital FK + HospitalMiddleware)
- [x] Module system (Module model + HospitalModuleSubscription)
- [x] 8 modules: Reception, Doctor, Nurse, Lab, Pharmacy/Inventory, Finance, Hospital Management, Home Care
- [x] `can_access_X` properties check both role AND hospital module subscription
- [x] Subscription expiry: `subscription_months` at onboarding, `deactivate_expired_hospitals` management command, 402 middleware block
- [x] Toggle hospital active/inactive (one-click from hospital list)
- [x] Module pricing editor (superadmin — edit price per module)
- [x] HospitalInvoice model + generate/print/list views
- [x] Reception carve-out: home-care-only hospitals skip forced Reception module
- [x] Workflow routing gate: `require_module_for_queue_type()` — blocks cross-module handoffs if module not subscribed
- [x] Superadmin dashboard: pie chart (income by module) + dual-mode line chart (income / onboarding trend)
- [x] Audit logs for Home Care deletes

### Branding (Partial)
- [x] Sidebar: hospital users see their own logo + name
- [x] Print header (`print_header.html`): hospital logo + name + Ternah attribution
- [x] Login page: Ternah logo SVG approximation + "Ternah Health Management" tagline
- [x] Home Care contract print: Ternah-styled A4

### Reception Module
- [x] Partial payment visibility: "Outstanding Balance" vs "Ready for Billing" split on dashboard + queue badge
- [x] WhatsApp pre-fill from `patient.contact`; Uganda number normalization (07... → 256...)
- [x] Receipt shows actual drug names (not "Pharmacy Item") — both walk-in and doctor-prescribed
- [x] Services and Drugs in separate sections on receipt
- [x] Receipt numbering: hospital initials prefix — `LMS20260702-000001`

### Doctor Module
- [x] Remove billable services from bill (inline × button)
- [x] Per-day service modal popup (asks "how many days?" before adding)
- [x] `is_per_day` field on Service (admin sets it; form includes live price total)

### Nurse Module
- [x] IV Nursing Care: `NursingAdmission`, `NursingCareItem`, `NursingDose` models
- [x] Dose-by-dose dispensing with per-dose inventory deduction
- [x] Shift handover via shared dose count state
- [x] Stop medication mid-treatment (with reason)
- [x] Discharge patient from nursing care
- [x] `nursing_managed` flag on Prescription
- [x] Nursing Care sidebar link + admissions dashboard

### Lab Module
- [x] Confirmed: TestProfile templates appear in lab report dropdown automatically (no extra wiring needed)
- [x] Template library view exists at `/lab/templates/`

### Reports Module
- [x] Reports hub page
- [x] Patients Seen report (date/doctor filters, CSV export, pagination)

### Financial Module
- [x] Financial report pulls from Payment table (receipts) — no longer filtered by visit status
- [x] Pharmacy income uses payment dates (not dispensing dates)
- [x] All income types (overview, cash, mobile, pharmacy) consistently from receipts

### Inventory Module
- [x] Batch delete
- [x] Restock modal fixed (DOMContentLoaded bug)
- [x] Batch dropdown in restock (select existing or add new)

### Home Care Management Module (full)
- [x] `HomeCareNurse`, `HomeCareClient`, `HomeCarePlacement`, `HomeCareContract`, `HomeCareReceipt` models
- [x] Billing cycle: Per Day / Per Week / Per Month
- [x] Dashboard with 6-month finance chart (income / nurse payouts / margin)
- [x] Nurse + Client CRUD with delete (audit-logged, blocked if active placement)
- [x] Placement create → auto-generates contract
- [x] Record receipt → auto-generates receipt number
- [x] Contract print: Ternah-branded A4 with hospital header
- [x] Receipt print: 80mm thermal with hospital header
- [x] Search + pagination (10/page) on all 5 list views
- [x] Home Care receipt/contract numbering: `HC20260702-0001` format

---

## ❌ NOT IMPLEMENTED (The Vault)

### High Priority
- [ ] **Landing page** — public-facing `/` page with Ternah brand kit (Deep Navy + Electric Blue + Pale Indigo), feature grid, module showcase, CTA to Login. Currently root URL routes straight to login.
- [ ] **Lab self-service template builder** — lab staff can't create their own TestProfile templates in-app. Currently only via Django admin. Plan: two modes (Quick Test = positive/negative, Panel Test = rows/columns). Backend model already exists, only UI missing.
- [ ] **Ternah logo file integration** — real logo PNG/SVG not yet in codebase. Login page has SVG approximation. Once file is provided, place at `static/images/ternah-logo.png` and update: login page, sidebar fallback (superadmin), landing page.
- [ ] **Full system rename** — several templates still say "Hospital EMR", "Lumina Medical Services": `base.html` title block, `admin_override_confirm.html`, `partials/print_footer.html`. Need global find-replace to "Ternah EMR" / "Ternah Software Ltd".

### Module Features
- [ ] **Sonographer module** — `ScanRequest` + `ScanReport` models, sonographer queue view, scan report print with hospital header. URLs exist in `nurse/urls.py` but views are stubs.
- [ ] **Configurable inter-module workflow routing** — `HospitalWorkflowRule` model (per-hospital: direct vs requires-approval for each module-to-module handoff). Documented in `SYSTEM_DESIGN_DOCUMENT.md`. Lab-only hospitals need Reception → Lab direct routing.
- [ ] **Home Care: Edit nurse / edit client** — no edit forms exist, only create + view + delete.
- [ ] **Home Care: Edit placement** — can't modify rates, dates, or nurse after creation. Need an edit view with audit trail.
- [ ] **Home Care: Actual nurse payout tracking** — currently nurse payouts on dashboard are projected (nurse_rate × active placements). No model to record actual payments made to nurses.
- [ ] **Home Care: Placement status filter** — status dropdown added to view but not wired into the list template filter UI.

### Platform Scaling
- [ ] **Module pricing per hospital** — price lives on `Module` (global). Can't offer a negotiated rate to a specific hospital. Needs price override on `HospitalModuleSubscription`.
- [ ] **Currency per hospital** — "UGX" is hardcoded throughout templates and receipts. Needs `Hospital.currency_code` field + template tag.
- [ ] **WhatsApp country code per hospital** — normalization hardcoded to Uganda (07... → 256...). Needs `Hospital.default_country_code` field.
- [ ] **Sign-up / self-service hospital onboarding** — no public `/signup`. Only superadmin can create hospitals. Placeholder "Request Access" form noted for future.
- [ ] **Cross-tenant isolation automated tests** — no test asserts "Hospital A admin cannot see Hospital B's data." Critical before onboarding a second paying client.

### Financial
- [ ] **Nurse payout model (Home Care)** — no `NursePayment` model. Dashboard shows projected costs only.
- [ ] **Automatic monthly invoice generation** — invoices are manually triggered. No auto-generation on subscription renewal date.

### Documentation
- [ ] **`SYSTEM_DESIGN_DOCUMENT.md` ER diagram** — needs updating with: `Module`, `HospitalModuleSubscription`, `NursingAdmission/CareItem/Dose`, all Home Care models, `HospitalInvoice`.
