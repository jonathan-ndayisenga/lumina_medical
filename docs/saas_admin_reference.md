# SaaS Platform Administration — Reference

Generic reference for any multi-tenant SaaS system with subscription management, onboarding, billing cycles, and payment integration.

---

## 1. Super User Account

The software owner. Exists outside any tenant — no `hospital` FK, bypasses all module/role permission checks. Should be seeded once via a management command, never created through the UI.

**Key fields:**

| Field | Notes |
|---|---|
| `username` | Login identifier, e.g. `superadmin` |
| `email` | Receives billing alerts, expiry notices, error digests |
| `is_superuser` | Platform-level flag — bypasses all tenant/module checks |
| `is_staff` | Grants Django admin panel access |
| `hospital` | Always `NULL` — absence of tenant FK signals platform ownership |
| `role` | `superadmin` or `platform_owner` constant |
| `last_login` | Monitor for suspicious inactivity |

**What the super user can do:**
- Create, edit, suspend, delete any tenant
- Grant or revoke module subscriptions per tenant
- Reset any user's password across any tenant
- View full audit logs, cross-tenant
- Issue or revoke tokens to any hospital
- Manually activate or deactivate accounts
- View revenue reports across all tenants
- Configure pricing per module

---

## 2. Multi-Tenant Architecture

Each client business is a **tenant** (`Hospital` model). All data is isolated by FK to that tenant — no separate databases or schemas. The platform admin sits above all tenants.

**Key tenant fields:**

| Field | Notes |
|---|---|
| `name` | Display name for the business |
| `slug` | URL-safe identifier; useful for subdomain routing later |
| `contact_email` | Receives billing notices and expiry warnings |
| `is_active` | Master on/off switch — `False` puts all users into soft-lock |
| `subscription_expires_at` | When the current paid period ends; drives warning and lock cycle |
| `token_balance` | Current unused token units; decremented at each billing cycle |

---

## 3. Client Onboarding Flow

1. **Create tenant record** — super user fills name, contact email, address. Set `subscription_expires_at` to agreed trial or paid period end.
2. **Create business admin user** — `hospital = <new tenant>`, `role = hospital_admin`. Temporary password sent to contact email.
3. **Subscribe to modules** — activate agreed modules (Reception, Doctor, Nurse, Lab, Pharmacy, Finance, Sonographer…) via `HospitalModuleSubscription` records.
4. **Set initial token balance** — if client has pre-paid, credit their `token_balance`.
5. **Hand off** — business admin logs in, changes password, creates staff accounts.

---

## 4. Module Subscriptions

Each module a tenant uses needs a `HospitalModuleSubscription` record. This drives every `can_access_X` permission check — no active record = module is invisible.

**Key fields:** `hospital`, `module`, `is_active`, `subscribed_at`, `expires_at`, `price_per_cycle`

**Permission check pattern:**
```python
HospitalModuleSubscription.objects.filter(
    hospital=request.user.hospital,
    module__code="reception",
    is_active=True,
).exists()
```
Super user bypasses this entirely.

---

## 5. Token System (Current)

Tokens are the pre-payment mechanism. Clients purchase tokens offline (bank transfer, mobile money), the operator credits the balance. Tokens are consumed at billing cycles instead of requiring a live payment gateway.

**TokenTransaction fields:** `hospital`, `amount` (positive = top-up, negative = consumed), `reason`, `filed_by`, `reference` (bank slip / MoMo code), `created_at`

**How a client files tokens:**
1. Client pays offline and notes the transaction reference.
2. Client submits reference + amount to the platform admin (form, WhatsApp, or email).
3. Super user verifies against bank records, creates a `TokenTransaction` with positive `amount`, increments `token_balance`, extends `subscription_expires_at`.

---

## 6. Account Lifecycle

| Phase | Trigger | Effect |
|---|---|---|
| **Active** | Subscription is current | Full access to all subscribed modules |
| **Warning** | 7 days before expiry | Email notice sent; non-blocking banner shown to all users; nothing restricted yet. Repeat at 3 days and 1 day. |
| **Soft-Locked** | `subscription_expires_at` passed | `Hospital.is_active = False`; users can still log in; dashboard shows lock banner; all data entry and reports blocked |
| **Reactivated** | Token top-up received or payment confirmed | `Hospital.is_active = True`; expiry extended; full access restored immediately |

> **Recommendation:** build a 24–48 hour grace window after expiry before setting `is_active = False`, to absorb weekends and bank delays.

---

## 7. Soft-Lock Behaviour

Users can still authenticate — no redirect to login. Dashboard loads with a prominent top-of-page banner.

**Still accessible in soft-lock:**
- Login and authentication
- Dashboard (with lock banner)
- Account profile
- Contact / support link
- Token filing / top-up request form

**Blocked in soft-lock:**
- Creating or editing any patient or operational data
- Accessing any queue (reception, doctor, nurse, lab)
- Generating or printing reports
- Recording payments or billing
- Dispensing prescriptions

**Banner message example:**
> "Your subscription has expired. All your data is safe. To restore access, please top up your token balance or contact your administrator."

**Implementation pattern:** middleware or view mixin checks `request.user.hospital.is_active` on every non-public request. If `False`, returns 423 for API endpoints or renders a locked-dashboard template for HTML views. Login and profile views are whitelisted.

---

## 8. Audit Logging

Append-only. Never update or delete audit records.

**AuditLog fields:** `hospital`, `actor`, `actor_role` (denormalized), `action` (e.g. `ACCOUNT_LOCKED`, `TOKEN_TOP_UP`), `description`, `object_type`, `object_id`, `metadata` (JSONField for old/new values, IP), `created_at`

**Mandatory events:**
- Account activated / deactivated
- Expiry warning sent
- Token top-up credited / consumed
- Payment confirmed (future)
- Account reactivated (auto or manual)
- Tenant created / deleted
- Module subscribed / removed
- User role changed
- Super user login

---

## 9. Future Payment Integration

The token system stays — the gateway simply becomes one more way to acquire tokens. No core billing logic changes; only the trigger source changes.

**Automated reactivation flow:**
```
Client pays → Gateway → POST /payments/webhook/ → verify signature + idempotency key → credit tokens → set is_active=True → extend expiry → write audit log
```

**PaymentRecord fields:** `hospital`, `gateway` (stripe / flutterwave / mtn_momo / manual), `gateway_reference` (unique — idempotency guard), `amount_paid`, `currency`, `tokens_credited`, `status` (pending → confirmed → applied), `webhook_payload` (raw JSON for audit/replay), `created_at`, `applied_at`

**Critical:** check `gateway_reference` uniqueness before applying any state changes — gateways re-send webhooks on network failures; double-processing = double token credit.

**Self-service portal (future):** a payment page linked from the lock banner and warning emails. Client pays, gateway fires webhook, account reactivates instantly — no super user action required.

---

## 10. Revenue Model

| Stream | Mechanism |
|---|---|
| Module subscriptions | Per-module `price_per_cycle`; total monthly bill = sum of active module prices per tenant |
| Token top-ups | Pre-paid; clients buy tokens at a fixed rate; bulk volume discounts possible |
| Tiered pricing (future) | Different `price_per_cycle` configured at onboarding for clinic / hospital / enterprise tiers |
| Automated billing (future) | Gateway charges at each renewal, credits tokens, extends expiry — zero-touch for the operator |

The token model is deliberately gateway-agnostic: it works with any payment method today and becomes the unified ledger when the gateway launches.
