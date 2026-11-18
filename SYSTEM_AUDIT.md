# System Audit — Ternah Health / Lumina EMR

**Date:** 18 Nov 2026
**Scope:** Whole Django codebase under `labsystem/` (≈43k lines of Python across 12 apps: accounts, admin_dashboard, books, doctor, finance, homecare, lab, nurse, ophthalmology, pharmacy, reception), settings, middleware, deploy config, and repository hygiene.
**Method:** Static code review. I read the code, ran `manage.py check --deploy`, `makemigrations --check` and the full test suite. I did not drive the running app in a browser or attack a live deployment, so every finding below comes from reading code paths. Where a finding depends on configuration or timing, the report says so.

---

## 1. Executive summary

| Severity | Count | Headline |
|---|---|---|
| 🔴 Critical | 3 | Every staff role passes the lab permission check · An Owner can read every tenant's lab data · Stored XSS that runs in the superadmin's browser |
| 🟠 High | 9 | Double-submit overpayments · lost updates on bill totals · ledger posting can fail silently · salary ledger breaks on re-save · expired stock gets dispensed · unsafe prod defaults |
| 🟡 Medium | 13 | Racy receipt numbering, duplicate cash drawers, deleting a payment deletes bank statement lines, edits drop charges, UTC/local date mismatches, … |
| 🔵 Low | 9 | Hygiene, minor IDOR, validation gaps |

**Is it ACID?** Only partly. Production uses PostgreSQL, which is ACID at the database level. The application does not use those guarantees consistently. See [§2](#2-acid-assessment).

**Top 5 to fix first**
1. C-1 / C-2: remove `return user.is_staff` from the lab permission check, and make every "scope to hospital" filter **fail closed**.
2. C-3: drop `|safe` from `audit_logs.html`.
3. H-1 / H-2: lock the `Visit` row (`select_for_update`) before any payment or total change, and update totals with `F()` or a recompute.
4. H-3 / H-4: stop silently swallowing ledger errors, and fix `_reverse_salary`.
5. H-8 / H-9: make `DEBUG` default to `False`, and refuse to boot in production without a real `DATABASE_URL`.

---

## Fix status (updated 18 Nov 2026)

All 34 findings now have fixes in the working tree; none are committed yet. Regression tests for the main ones are in [labsystem/test_audit_fixes.py](labsystem/labsystem/test_audit_fixes.py). New migrations: `accounts 0028`, `admin_dashboard 0019`, `finance 0002`, `reception 0036`.

| ID | Fix |
|---|---|
| C-1 | `is_staff` fallback removed. Lab work needs the lab role, hospital admin or the Lab group. Doctors get read-only report pages (`report_reader_required`), and POSTs to `visit_report` return 403 for them. |
| C-2 | New [labsystem/tenancy.py](labsystem/labsystem/tenancy.py) `scope_to_hospital()` fails closed. All 41 sites in lab, doctor, nurse, ophthalmology and reception use it. |
| C-3 / M-13 | Audit log renders with `|pprint` (escaped). All 26 chart JSON blobs go through [safe_json.py](labsystem/labsystem/safe_json.py) `script_json()`. |
| H-1 | Payment views lock the visit row (`select_for_update`). The form carries a one-time token, and the new unique `Payment.idempotency_key` stores it. |
| H-2 | New `Visit.adjust_total()`: a single `UPDATE … SET total = GREATEST(total + x, 0)`. All 16 read-modify-write sites converted. |
| H-3 | New `finance.PostingFailure` model. Failures are recorded instead of only logged, a missing account now raises, the finance dashboard shows a banner with **Retry postings**, and there is a `manage.py retry_ledger_postings` command. |
| H-4 | New `JournalEntry.source_salary` link. Reversals skip entries already reversed. |
| H-5 | The nursing dose view locks the care item and the drug. A dose within 60 s of the previous one is rejected as a double tap. Batch deductions are conditional `F()` updates. |
| H-6 | Expired batches are excluded from `available_batches`, `available_dispense_quantity` and `consume_stock`. New `expired_quantity` property. |
| H-7 | Visit edit is refused once the doctor or pharmacy has added lines, or money has been taken. |
| H-8 / H-9 | `DEBUG` defaults off under gunicorn. Boot is refused with the dev secret key, or with no real `DATABASE_URL` (SQLite only for local `manage.py`, the `collectstatic` build, or `DJANGO_ALLOW_SQLITE=1`). |
| M-1 / M-6 | New [labsystem/numbering.py](labsystem/labsystem/numbering.py) takes the numeric maximum and retries on collision. Used for subscription receipts and invoices, home-care contracts and receipts, and journal references. |
| M-2 / M-3 | New `open_drawer_for()` and `mirror_cash()`. Drawer creation is serialised per hospital, and closed drawers are never changed: corrections go into an open drawer. |
| M-4 | Deleting a payment unlinks its statement lines instead of deleting them. |
| M-5 | A visit with payments can't be deleted. A user with salary or payment history is deactivated instead of deleted. `Salary.employee` is now PROTECT. |
| M-7 | Manual journals and opening balances are atomic and validated. Opening balances can only be posted once. |
| M-8 | All UTC `.date()` / `date.today()` uses switched to `timezone.localdate()`. |
| M-9 | Shared categories and specimen types can only be edited by superadmins. Deleting an in-use specimen shows a message instead of a 500. |
| M-10 | `Visit.removal_blocked_message()`: removing a paid charge is refused until a refund is made. |
| M-11 | Books `Payment.record` locks the client's open invoices before allocating. |
| M-12 | The hospital balance counts every non-waived receipt. |
| L-1 / L-2 | `ven/`, all `.pyc` files and the DB backup were removed from the git index (the files stay on disk), and `*.sqlite3*` was added to `.gitignore`. **They are still in git history**: rewrite history if the repo is ever shared. |
| L-3 | `reset_passwords.py` refuses to run unless DEBUG is on and the database is SQLite. |
| L-4 | `is_staff` is for superadmins only, and data migration `accounts 0028` clears it for everyone else. |
| L-5 | The admin password reset runs Django's full validators. |
| L-6 | The 5-failure lock is now per username *and address*. A username-wide lock still applies after 50 failures from anywhere. |
| L-7 | `view_lab_request` is scoped to the hospital. `test_clone` was left as designed. |
| L-8 | The root `requirements.txt` is now `-r labsystem/requirements.txt`. |
| L-9 | Books `next` redirects are host-checked. |

---

## 2. ACID assessment

| Property | Verdict | Evidence |
|---|---|---|
| **Atomicity** | ⚠️ Partial | `ATOMIC_REQUESTS` is **not** set ([settings.py](labsystem/labsystem/settings.py)). About 40 views carry `@transaction.atomic` (most of reception, nurse, doctor writes). These do **not**: `journal_entry_create`, `opening_balances`, `expense_delete` ([finance/views.py](labsystem/finance/views.py)), most of `admin_dashboard` (13 atomic blocks across 4,156 lines), and the multi-write `save()` overrides on `Payment` and `Expense`. Outside an atomic view, a crash between writes leaves half-written data. The finance ledger is **non-atomic on purpose**: every auto-post runs in its own savepoint, and its exception is logged and discarded ([finance/signals.py:20-25](labsystem/finance/signals.py#L20-L25)). A payment can therefore commit while its journal entry does not. |
| **Consistency** | ❌ Weak | Almost no invariants exist at the database level. Missing: a unique `(hospital, date)` on `CashDrawer`; CHECK constraints on amounts ≥ 0; enforcement that journal entries balance (`JournalLine.clean()` never runs through `objects.create`, and `is_balanced()` is never enforced); a `sum(payments) ≤ total` rule. `Visit.total_amount` is a denormalised running total that about 20 call sites change by read-modify-write, and nothing recomputes it from `VisitService`. |
| **Isolation** | ❌ Weak | PostgreSQL's default isolation is READ COMMITTED. `select_for_update` appears in only **3** places in the whole app: login throttle, `books.Sequence`, and `pharmacy.dispense`. Every other check-then-write (payments, bill totals, nursing doses, stock, receipt numbers, cash drawers, Books invoice allocations) can race. Concrete failures are listed under H-1, H-2, H-5 and M-1 to M-3. |
| **Durability** | ⚠️ Config-dependent | PostgreSQL on DigitalOcean is durable. However, if `DATABASE_URL` is missing, or arrives as an unresolved `${db.DATABASE_URL}` placeholder, settings **silently fall back to SQLite** on the container's ephemeral disk ([settings.py:62-81](labsystem/labsystem/settings.py#L62-L81)). The app then boots and accepts data, and the next deploy erases it (H-9). |

**Bottom line:** the database engine is ACID, but the application layer gives up atomicity on purpose for the ledger and gives up isolation by accident almost everywhere money or stock changes.

---

## 3. Critical findings

### C-1 · Every staff role passes the lab permission check
**Where:** [lab/views.py:56](labsystem/lab/views.py#L56), [accounts/models.py:388-398](labsystem/accounts/models.py#L388-L398)

`_lab_access_ok()` ends with `return user.is_staff`. `User.save()` sets `is_staff = True` for **every** hospital role: receptionist, doctor, nurse, accountant, hospital admin and owner. `@staff_required` therefore works like `@login_required` for the whole lab module.

**Failure scenario:** a receptionist or accountant opens `/lab/…/enter_result/<id>/` and can enter, edit or **amend released results**. They can also collect samples, route reports, and view every lab report and queue in their hospital. The codebase's own comment in [books/permissions.py](labsystem/books/permissions.py) already warns that `is_staff` "is set broadly across every customer hospital's staff".

**Fix:** delete the `is_staff` fallback. Grant access only by explicit role or group.

### C-2 · "Scope to hospital" filters fail open, so an Owner can read every tenant's lab data
**Where:** [lab/views.py:77-82](labsystem/lab/views.py#L77-L82), [lab/views.py:1110-1111](labsystem/lab/views.py#L1110-L1111), [lab/views.py:1224-1225](labsystem/lab/views.py#L1224-L1225), [lab/views.py:1256-1257](labsystem/lab/views.py#L1256-L1257), [lab/views.py:1477-1478](labsystem/lab/views.py#L1477-L1478); the same pattern appears in doctor/nurse views ([doctor/views.py:427](labsystem/doctor/views.py#L427), [nurse/views.py:49](labsystem/nurse/views.py#L49))

The pattern is `if hospital and role != "superadmin": qs = qs.filter(hospital=hospital)`. When `hospital` is `None`, the queryset stays **unfiltered across all tenants**.

**Failure scenario:** an Owner (`role="owner"`, `hospital=None`, `is_staff=True`) who has not yet picked a branch has `request.hospital = None` ([middleware.py](labsystem/labsystem/middleware.py) `HospitalMiddleware`). Because of C-1 they pass `staff_required`. Opening `/lab/queue/` lists **every hospital's** lab patients. Changing the ID in `report_detail`, `visit_report` or `enter_result` reads and **writes** other organisations' patient results. This is a cross-tenant PHI breach.

**Fix:** fail closed with `if hospital is None and not superadmin: return qs.none()` (or 403). Put it in one shared helper and use that everywhere.

### C-3 · Stored XSS that runs in the superadmin's browser
**Where:** [admin_dashboard/templates/admin_dashboard/audit_logs.html:66](labsystem/admin_dashboard/templates/admin_dashboard/audit_logs.html#L66)

`{{ log.details|safe }}` renders the AuditLog JSON without escaping. `details` contains user-typed text, such as `patient_name` and the free-text admin-override `reason` from `patient_delete`, `report_delete` and others.

**Failure scenario:** a hospital admin deletes a lab report and types `<img src=x onerror=fetch('/platform/…')>` as the reason. When the platform superadmin opens **Audit Logs**, the script runs with superadmin privileges and can create users, change subscriptions, and so on: a full platform takeover.

**Fix:** remove `|safe` (use `{{ log.details }}` or `|pprint`).

*Related (Medium):* `json.dumps(...)|safe` inside `<script>` tags in about 10 report templates. `json.dumps` does not escape `</script>`, so a doctor name, lab test name or option label containing `</script><script>…` breaks out. Use Django's `{{ data|json_script:"id" }}`.

---

## 4. High findings

### H-1 · Double-clicking "Record payment" can overpay a visit
**Where:** [reception/views.py:1745](labsystem/reception/views.py#L1745) `complete_visit`, [finance/views.py:478](labsystem/finance/views.py#L478) `debtor_collect`

Both views are `@transaction.atomic`, but they never lock the visit. `CompleteVisitForm` checks `amount_paid ≤ remaining_balance` against a balance read without a lock.

**Failure scenario:** the balance is 50,000. A double-click or a flaky-network retry sends two POSTs. Under READ COMMITTED both see a 50,000 balance and both insert a `Payment`. The visit now shows 100,000 collected, the cash drawer and ledger both double, and `balance_due` clamps to 0, so the overpayment is invisible.

**Fix:** `Visit.objects.select_for_update().get(pk=…)` at the top, then re-validate. Add an idempotency token to the form.

### H-2 · Lost updates on `Visit.total_amount`, so patients are under-billed
**Where:** about 20 sites, for example [pharmacy/services.py:133](labsystem/pharmacy/services.py#L133), [pharmacy/services.py:198](labsystem/pharmacy/services.py#L198), [doctor/views.py:528](labsystem/doctor/views.py#L528), [doctor/views.py:634](labsystem/doctor/views.py#L634), [doctor/views.py:773](labsystem/doctor/views.py#L773), [lab/views.py:1417](labsystem/lab/views.py#L1417), [reception/views.py:819](labsystem/reception/views.py#L819), [reception/views.py:961](labsystem/reception/views.py#L961)

Every one of them does `visit.total_amount = visit.total_amount + price; visit.save(update_fields=["total_amount"])` on an instance it read earlier, without a lock.

**Failure scenario:** a doctor adds a lab test (+20,000) while pharmacy adds a walk-in item (+5,000) on the same visit. Both read 30,000. One writes 50,000 and the other writes 35,000, so the last write wins and **one charge disappears from the bill**, even though its `VisitService` line exists. The `max(0, …)` clamps on removal also hide drift.

**Fix:** use `Visit.objects.filter(pk=…).update(total_amount=F("total_amount") + price)`. Better, recompute the total from `VisitService` rows inside a locked transaction, and add a nightly check that flags any visit whose total ≠ the sum of its lines.

### H-3 · Ledger posting fails silently, so the books drift from operations
**Where:** [finance/signals.py:20-25](labsystem/finance/signals.py#L20-L25), [finance/posting.py](labsystem/finance/posting.py)

Every auto-post is wrapped in `try/except Exception: logger.exception(...)`. Missing accounts also return early with no error (`if not cash_acc or not receivable: return`).

**Failure scenario:** a hospital deactivates its "Cash" account, or any IntegrityError occurs (see H-4 and M-6). Payments keep saving, the journal gets nothing, and the trial balance, P&L and cashbook go quietly wrong. Nothing re-posts the missed entry later, and nobody is alerted.

**Fix:** decide one of two things. Either ledger writes are part of the same transaction (fail the payment and show an error), or keep them best-effort but write a `PostingFailure` row and expose a "re-post" job and a dashboard counter.

### H-4 · The salary ledger breaks on the second edit
**Where:** [finance/posting.py:319-345](labsystem/finance/posting.py#L319-L345) `_reverse_salary`

Unlike `_reverse_existing`, this function does **not** filter `reversal_of__isnull=True`. It also finds entries by **matching the description text**. `reversed_entry` is a `OneToOneField`.

**Failure scenario:**
1. Mark a salary paid. Entry E1 is posted.
2. Edit the amount. E1 is reversed (R1) and E2 is posted.
3. Edit again. The function tries to reverse **E1 again**, which violates the one-to-one constraint. H-3 swallows the IntegrityError, so the ledger stays at E2's amount, and every later edit or delete is ignored as well.

Two salary rows for the same employee and month also share a description and reverse each other.

**Fix:** add a `source_salary` FK like the other sources, and filter `reversal_of__isnull=True`.

### H-5 · Nursing doses: double-click, stock race, and lost batch updates
**Where:** [nurse/views.py:402-450](labsystem/nurse/views.py#L402-L450) `administer_dose`

The care item and the drug are read without `select_for_update`. `consume_stock` writes absolute values (`batch.quantity = available - used`).

**Failure scenario:**
- A double-tap records **two doses**. Nothing locks the `is_complete` check, so the chart over-reports what the patient received.
- A nurse dose running at the same time as a pharmacy dispense overwrites the other's batch quantity, so stock drifts upward and a phantom stock appears.

**Fix:** lock the care item and the drug as `pharmacy.services.dispense` does. Better, route nursing doses through `dispense()`-style shared code. Make batch deductions use `F()`.

### H-6 · Expired batches are dispensed first
**Where:** [admin_dashboard/models.py:441-495](labsystem/admin_dashboard/models.py#L441-L495) `available_batches` / `consume_stock`

Batches are picked earliest-expiry-first (FEFO), but expired batches (`expiry_date < today`) are **not excluded**, so the earliest-expiring batch picked first is often an already-expired one.

**Failure scenario:** a drug has an expired batch with 20 tablets left and a fresh batch. Dispensing takes the expired tablets first. This is a patient-safety issue. The stock check (`available_dispense_quantity`) also counts expired stock as available.

**Fix:** exclude `expiry_date__lt=today` from consumption and from availability. Show expired stock separately for write-off.

### H-7 · Editing a visit silently drops doctor-added and pharmacy charges
**Where:** [reception/views.py:1566-1570](labsystem/reception/views.py#L1566-L1570) `visit_edit`

The total is recomputed from **only the services in the reception form**, and then the code runs `visit.visit_services.all().delete()`. The "no processed queue entries" guard does not cover work added while the doctor's queue entry is still open (prescription billing lines, lab tests the doctor ordered).

**Failure scenario:** during consultation the doctor prescribes a drug, which creates a "Pharmacy Item" `VisitService` and raises the total. Before the doctor finishes, reception edits the visit. The pharmacy line is deleted, `Prescription.billing_visit_service` is set to NULL, and the medicine is later dispensed **free**.

**Fix:** only touch lines that reception itself added. Refuse to edit when other departments have added lines.

### H-8 · Production defaults are unsafe (`DEBUG=True`, known secret key)
**Where:** [settings.py:92-101](labsystem/labsystem/settings.py#L92-L101)

`DEBUG` defaults to **True**. The secret-key guard only runs `if not DEBUG`.

**Failure scenario:** a deploy where `DJANGO_DEBUG` is missing or misspelled boots with `DEBUG=True` and the **public, committed** `django-insecure-…` key. That exposes full tracebacks and settings on every error and lets anyone forge sessions. Secure cookies, HSTS and SSL redirect all switch off too, because they key off `DEBUG`. `manage.py check --deploy` already reports 6 warnings in the default configuration.

**Fix:** use `DEBUG = env_bool("DJANGO_DEBUG", False)`, and always require `DJANGO_SECRET_KEY` unless an explicit `DJANGO_DEV=1` is set.

### H-9 · Silent fallback to ephemeral SQLite in production
**Where:** [settings.py:62-81](labsystem/labsystem/settings.py#L62-L81)

A missing `DATABASE_URL`, or an unresolved `${db.DATABASE_URL}` placeholder, falls back to `db.sqlite3` instead of failing. On App Platform the disk is wiped on every deploy, as the project's own comment on media storage notes.

**Failure scenario:** someone detaches or renames the database binding. The app comes up "healthy", runs migrations on an empty SQLite file, and staff register patients and take payments for hours or days. The next deploy erases all of it.

**Fix:** only fall back during the build phase, for example with `DJANGO_BUILD=1`. At runtime with `DEBUG=False`, raise `ImproperlyConfigured`.

---

## 5. Medium findings

| # | Finding | Where | Failure scenario / fix |
|---|---|---|---|
| M-1 | **Count-based document numbers race and reuse numbers** | [accounts/models.py:706-714](labsystem/accounts/models.py#L706-L714), [accounts/models.py:743-751](labsystem/accounts/models.py#L743-L751), [homecare/models.py:189-197](labsystem/homecare/models.py#L189-L197), [homecare/models.py:242-250](labsystem/homecare/models.py#L242-L250) | `count()+1` on save: two saves at the same moment collide on a `unique` field (500 error), and deleting one row makes the next save reuse an existing number (IntegrityError until midnight). Use the existing `books.ledger_models.Sequence`. |
| M-2 | **Duplicate cash drawers per day** | [admin_dashboard/models.py:740](labsystem/admin_dashboard/models.py#L740), [reception/models.py:870-884](labsystem/reception/models.py#L870-L884), [admin_dashboard/models.py:142-156](labsystem/admin_dashboard/models.py#L142-L156) | No unique `(hospital, date)` constraint, and creation is find-then-create. Two simultaneous first cash receipts of the day create two drawers, so the day's cash is split and reconciliation shows a discrepancy. Add a constraint and use `get_or_create` under a lock. |
| M-3 | **Receipts post into an already-closed cash drawer** | [reception/models.py:870](labsystem/reception/models.py#L870) | The variable is named `open_drawer`, but `closed_at` is never checked. A cash payment after end-of-day close (or a late debtor collection) changes a drawer that was already counted and signed off, and its stored `expected_closing` / `discrepancy` go stale. Post to the next open drawer, or block the payment. Expenses have the same problem. |
| M-4 | **Deleting a payment deletes the bank and mobile-money statement lines** | [reception/models.py:922-923](labsystem/reception/models.py#L922-L923) | Statement lines are external facts imported from the bank. `Payment.delete()` deletes them instead of un-reconciling them, so the bank reconciliation loses real transactions. Set `reconciled_with = NULL` instead. |
| M-5 | **Cascade deletes skip side effects** | [reception/views.py:1674](labsystem/reception/views.py#L1674) `visit_delete`; [admin_dashboard/views.py:1411](labsystem/admin_dashboard/views.py#L1411) `delete_user` | `visit_delete` does not check for existing payments. A queryset/cascade delete skips the `Payment.delete()` override, so no balance resync runs and the `CashTransaction` (SET_NULL) stays in the drawer pointing at nothing. `delete_user` hard-deletes, and `Salary.employee` is `CASCADE`, so **the employee's salary history is erased**. Block both deletes when money exists, and deactivate users instead of deleting them. |
| M-6 | **Journal reference numbers break after 9,999 per day across the whole platform** | [finance/models.py:157-175](labsystem/finance/models.py#L157-L175) | References look like `JNL-YYYYMMDD-NNNN`, and `order_by("-reference")` sorts them as strings, so `…-10000` sorts below `…-9999`. After 9,999 entries in one day (all tenants share one counter, and reversals count too), every new entry collides. Its IntegrityError is swallowed by H-3, so the ledger stops posting until midnight. Use a `Sequence` row or per-hospital numbers. |
| M-7 | **Manual journal entries and opening balances are not atomic and can be posted twice** | [finance/views.py:261-309](labsystem/finance/views.py#L261-L309), [finance/views.py:1001-1064](labsystem/finance/views.py#L1001-L1064) | No `@transaction.atomic`: if the second line fails, a one-sided entry remains. `opening_balances` can be posted repeatedly, doubling the opening position. A non-numeric account ID or a bad date returns a 500 instead of a form error. |
| M-8 | **UTC dates are used where local (Kampala) dates are meant** | [finance/posting.py:194](labsystem/finance/posting.py#L194), [finance/posting.py:234](labsystem/finance/posting.py#L234), [reception/models.py:804](labsystem/reception/models.py#L804), [admin_dashboard/views.py:1097](labsystem/admin_dashboard/views.py#L1097), [middleware.py:76](labsystem/labsystem/middleware.py#L76), `date.today()` and `now().strftime` in 10+ places | `USE_TZ=True` means `aware_dt.date()` gives the UTC date. A payment at 01:30 EAT on the 19th is journaled, receipt-stamped and charted on the **18th**, so daily revenue does not match the daily cash drawer (which correctly uses `localdate`). Subscriptions also expire 3 hours late. Use `timezone.localdate(dt)` everywhere. |
| M-9 | **Hospital admins can rename lab categories shared by every hospital** | [lab/views_catalog.py:223](labsystem/lab/views_catalog.py#L223), [lab/views_catalog.py:271](labsystem/lab/views_catalog.py#L271) | `ServiceCategory` and `SpecimenType` are platform-wide models, but `catalog_admin_required` lets any hospital admin edit them, so one tenant's rename shows up in every tenant's catalog. Deleting a specimen type that is in use raises an unhandled `ProtectedError` (500). Restrict these edits to superadmins. |
| M-10 | **Removing services can leave an invisible credit** | [reception/views.py:2042](labsystem/reception/views.py#L2042), [doctor/views.py:849](labsystem/doctor/views.py#L849), [doctor/views.py:895](labsystem/doctor/views.py#L895) | If a service is removed after a partial payment, the total can drop below the amount paid. `balance_due` clamps to 0, and no refund or credit is recorded, so the patient's overpayment disappears. Block the removal, or record a credit or refund. |
| M-11 | **Books invoice payments can over-allocate** | [books/document_models.py:543](labsystem/books/document_models.py#L543) `Payment.record` | The method is atomic, but the invoice row is not locked, so two payments recorded at once both pass `amount ≤ invoice.balance`. Lock the invoices with `select_for_update` before checking. |
| M-12 | **`HospitalAccount.balance` ignores money already collected** | [admin_dashboard/models.py:12-31](labsystem/admin_dashboard/models.py#L12-L31) | Income counts only payments on `status="completed"` visits, so partial payments and fully-paid visits still in a queue are left out. The function also re-aggregates **all** of the hospital's payments, expenses and salaries on every save, which gets slow as data grows (O(N) per receipt). |
| M-13 | **Stored XSS risk in chart JSON** | about 10 templates using `{{ x_json\|safe }}` inside `<script>` | See the note under C-3. |

---

## 6. Low findings and hygiene

| # | Finding | Where |
|---|---|---|
| L-1 | **The virtualenv and bytecode are committed.** 6,203 files under `ven/` and 1,367 `.pyc` files are tracked even though `.gitignore` lists them (they were added before the ignore rule). Run `git rm -r --cached ven **/__pycache__`. | repo root |
| L-2 | **A SQLite backup with real hashes and patient rows is committed.** `labsystem/db.sqlite3.pre-merge-backup` contains 22 user password hashes, 13 patients and 6 hospitals. Remove it from history if the repo is ever shared. | [labsystem/db.sqlite3.pre-merge-backup](labsystem/db.sqlite3.pre-merge-backup) |
| L-3 | **`reset_passwords.py` sets six well-known usernames to `admin123`.** It is committed next to `manage.py`, and running it by accident in production would open those accounts. Delete it or guard it with `if not settings.DEBUG: sys.exit()`. | [labsystem/reset_passwords.py](labsystem/reset_passwords.py) |
| L-4 | **`is_staff=True` for every role.** All staff can reach `/admin/`'s login page. They have no model permissions, but it widens the attack surface and caused C-1. Keep `is_staff` for superadmins only. | [accounts/models.py:373-399](labsystem/accounts/models.py#L373-L399) |
| L-5 | **The admin password reset skips `AUTH_PASSWORD_VALIDATORS`** and checks only `len ≥ 8`. Call `validate_password()`. | [admin_dashboard/views.py:1382](labsystem/admin_dashboard/views.py#L1382) |
| L-6 | **Lockout per username allows a nuisance DoS.** Five wrong guesses lock anyone's account for 15 minutes, so a reception login can be kept locked from outside. Consider CAPTCHA or progressive delay instead of a hard lock. `DO-Connecting-IP` is trusted unconditionally, which is only safe while traffic always arrives through DigitalOcean. | [accounts/login_throttle.py](labsystem/accounts/login_throttle.py) |
| L-7 | **Minor IDOR.** `view_lab_request` loads any hospital's `LabRequest` by ID (it only redirects, but it confirms the ID exists and leaks the visit ID). `test_clone` copies any tenant's lab test on purpose; confirm that the definitions really are non-sensitive. | [doctor/views.py:1426](labsystem/doctor/views.py#L1426), [lab/views_catalog.py:541](labsystem/lab/views_catalog.py#L541) |
| L-8 | **The two requirements files have drifted.** `labsystem/requirements.txt` includes `twilio==9.4.5` and the root `requirements.txt` does not. Deploys use `source_dir: labsystem`, so local installs from the root miss it. Keep one file. | requirements.txt |
| L-9 | **The Books "next" redirect is unvalidated.** `redirect(request.POST.get("next") or …)` has no host check, so it is an open redirect for logged-in Books staff. Use `url_has_allowed_host_and_scheme`, as the lab module already does. | [books/planning_views.py:76](labsystem/books/planning_views.py#L76) |

---

## 7. What's in good shape

- **Ternah Books** is clearly the most careful module. It uses a gapless, row-locked `Sequence` for document numbers, `@transaction.atomic` on every posting function, closed-financial-year enforcement, and access scoped by tenant rather than by `is_staff`.
- **`pharmacy.services.dispense`** locks the prescription and drug rows correctly. It is the model the nurse and stock code should follow.
- **Media serving** is deny-by-default with `CSP: sandbox` and `nosniff`, and private files go to Spaces with signed URLs that expire.
- **Security middleware**: idle-timeout logout, `no-store` on signed-in pages for shared computers, a login throttle that also covers `/admin/`, and a host check on `next` redirects in the lab module.
- **Released lab results are never silently overwritten**: amendments require a reason, keep the old values, and notify the doctor. C-1 still lets the wrong people make amendments.
- **No migration drift** (`makemigrations --check` reports no changes), and there is no raw SQL in request paths.

---

## 8. Test suite

**After the fixes:** 538 tests, all passing: the original 518 plus 20 new regression tests in `labsystem/test_audit_fixes.py`.

**Before the fixes:** `manage.py test` ran 518 tests, all passing (about 19.5 minutes on SQLite).

The suite passes even though the findings above exist, because it runs in a single thread against SQLite. It cannot reproduce the race conditions (H-1, H-2, H-5, M-1, M-2, M-11), and no test checks that non-lab roles are denied lab access (C-1) or that a user without a hospital sees nothing (C-2). Suggested additions:
- **Role-matrix tests:** for each role, which URLs return 200 and which return 403/redirect. Include an Owner with no branch selected.
- **Ledger tests:** save a paid salary three times and assert the net ledger equals the amount; force a missing account and assert the failure is surfaced, not swallowed.
- **Concurrency tests:** run on PostgreSQL with `TransactionTestCase` and two threads recording payments on the same visit.
