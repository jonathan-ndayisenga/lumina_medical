# Multi-Tenant HMS Accounts Blueprint

Prepared on April 17, 2026 as the implementation blueprint for the `accounts` foundation.

---

## 1. Purpose

This document defines the `accounts` app as the foundation of the multi-tenant HMS.

It is the build reference for:

- authentication,
- tenant ownership,
- role structure,
- superadmin support,
- and subscription-level platform control.

---

## 2. Scope

The `accounts` app is responsible for:

- hospitals as tenants,
- subscription plans,
- hospital subscription payments,
- audit logs,
- and the custom user model.

It is not responsible for:

- patient registration,
- visit workflow,
- queueing,
- doctor consultation,
- nursing activity,
- inventory,
- or billing operations.

Those come later.

---

## 3. Core Models

## 3.1 SubscriptionPlan

Purpose:

- define the commercial package a hospital is on

Fields:

- `name`
- `price_monthly`
- `price_yearly`
- `max_users`
- `max_storage_mb`
- `description`
- `is_active`
- `created_at`

Notes:

- should be manageable by superadmin
- should support future feature flags if needed

## 3.2 Hospital

Purpose:

- represent one tenant in the platform

Fields:

- `name`
- `subdomain`
- `subscription_plan`
- `is_active`
- `subscription_end_date`
- `created_at`

Notes:

- every operational record in the future should either belong directly to a hospital or inherit tenant ownership through a hospital-owned parent

## 3.3 User

Purpose:

- serve as the single authentication model for the broader HMS

Base:

- extends `AbstractUser`

Fields:

- standard Django auth fields
- `hospital`
- `role`

Roles:

- `superadmin`
- `hospital_admin`
- `receptionist`
- `lab_attendant`
- `doctor`
- `nurse`

Notes:

- `superadmin` must remain global
- hospital-linked roles should belong to exactly one hospital at a time in the current model

## 3.4 AuditLog

Purpose:

- track important platform actions for accountability

Fields:

- `user`
- `hospital`
- `action`
- `model_name`
- `object_id`
- `details`
- `timestamp`

Notes:

- this should grow over time, not be treated as complete on day one

## 3.5 HospitalSubscriptionPayment

Purpose:

- record subscription payments at the platform level

Fields:

- `hospital`
- `amount`
- `period_start`
- `period_end`
- `paid_at`
- `notes`

---

## 4. Role Rules

These rules should guide behavior from the start.

## 4.1 Superadmin

- global access
- no hospital required
- can manage hospitals and plans
- can access developer dashboard

## 4.2 Hospital Admin

- must belong to a hospital
- can manage users and hospital-level settings later
- can access hospital dashboard

## 4.3 Operational Users

- receptionist
- lab attendant
- doctor
- nurse

Rules:

- must belong to a hospital
- only see hospital-scoped data
- should eventually route into role-specific dashboards or queues

---

## 5. Authentication Rules

## 5.1 Login

All users authenticate through the custom `accounts.User` model.

## 5.2 Redirect Logic

Initial role-based landing should be:

- `superadmin` -> developer dashboard
- `hospital_admin` -> hospital dashboard
- all other users -> current lab dashboard for now

This is transitional and practical.

## 5.3 Staff/Superuser Flags

Expected behavior:

- `superadmin` -> `is_staff=True`, `is_superuser=True`
- `hospital_admin` and operational staff -> `is_staff=True`
- later front-office users can remain admin-disabled if desired, but for now keeping them staff-capable helps with admin operations

---

## 6. Tenant Context Rules

The accounts layer must support request-bound tenant context.

## 6.1 Middleware Role

Middleware should:

- resolve `request.hospital`
- use user-linked hospital when present
- allow subdomain fallback for future routing

## 6.2 Important Caution

Middleware is not enough.

Application code must still enforce:

- tenant-filtered querysets
- tenant-owned form choices
- tenant-safe dashboards

---

## 7. Admin Requirements

The Django admin should support:

- `SubscriptionPlan` management
- `Hospital` management
- `User` management
- `AuditLog` viewing
- `HospitalSubscriptionPayment` viewing

Admin should expose:

- role
- hospital
- status
- plan
- subscription metadata

---

## 8. Initial Bootstrap Records

These are the first data records we should create in staging.

## 8.1 Subscription Plan

Minimum:

- one default plan for Lumina or staging

## 8.2 Hospital

Minimum:

- one default hospital for current Lumina operations

Suggested example:

- `Lumina Medical Services`

## 8.3 Users

Minimum:

- one `superadmin`
- one `hospital_admin`
- one `lab_attendant`

This gives us the first meaningful role test set.

---

## 9. Implementation Sequence

The accounts app should be implemented in this order.

### Step 1

Add models:

- `SubscriptionPlan`
- `Hospital`
- `User`
- `AuditLog`
- `HospitalSubscriptionPayment`

### Step 2

Configure:

- `AUTH_USER_MODEL`
- admin registration
- creation/change forms

### Step 3

Add:

- middleware for hospital context
- role-based home routing

### Step 4

Add:

- superadmin dashboard
- hospital admin dashboard

### Step 5

Bootstrap staging data for testing

---

## 10. Risks Specific to Accounts

## 10.1 Mid-Project User Model Swap

This is the biggest risk and must be managed by staged rollout.

## 10.2 Weak Tenant Enforcement

If roles exist but hospital scoping is not enforced later, the platform becomes unsafe.

## 10.3 Overloading Accounts Too Early

The accounts app should not absorb reception or finance logic just because it is foundational.

Keep it clean.

---

## 11. Completion Criteria

The accounts foundation is considered complete for the first wave when:

- the custom user model is active in the target environment
- superadmin login works
- hospital admin login works
- operational lab user login works
- hospital context can be resolved
- the dashboards route correctly by role
- tenant-owned foundation records exist

---

## 12. Immediate Follow-On After Accounts

Once this accounts foundation is stable, the next build target should be:

- reception backbone

That means:

- `Patient`
- `Visit`
- `Service`
- `VisitService`
- `QueueEntry`
- `Payment`

That is the correct next dependency layer.

---

Prepared as the execution blueprint for the `accounts` app.
