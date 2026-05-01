# Multi-Tenant HMS Architecture Decisions and Migration Strategy

Prepared as the follow-up planning document to:

- `docs/MULTI_TENANT_HMS_SHIFT_FEASIBILITY_PLAN.md`

This document focuses on:

1. architecture decisions,
2. migration strategy from the current lab-first system,
3. the exact first build sequence for Phase 0 and Phase 1.

---

## 1. Purpose

The purpose of this document is to lock the most important technical decisions before implementation begins.

The current repository is not a greenfield Hospital Management System. It is an existing Django project with a working lab module that already supports:

- lab report creation,
- CBC and urinalysis templates,
- learned test defaults,
- printing,
- and live deployment behavior.

That means the next phase must be handled as a controlled platform expansion.

---

## 2. Current System Reality

At the time of writing, this repository is centered around:

- a Django project package called `labsystem`,
- an active `lab` app,
- a production deployment pattern already tied to GitHub and PostgreSQL/App Platform behaviors,
- existing operational report data assumptions.

Important consequence:

- the safest path is to preserve the current project root and incrementally introduce the broader HMS architecture inside it.

---

## 3. Architecture Goals

The future system should achieve all of the following:

- support multiple hospitals as isolated tenants,
- support multiple user roles inside each hospital,
- preserve and reuse the current lab module,
- attach operational workflows to patient visits,
- create a queue-driven clinical workflow,
- centralize accounting and admin reporting,
- support safe staging and production deployments.

---

## 4. Core Architecture Decisions

## 4.1 Project Structure Decision

### Decision

Keep the current Django project package and expand within the existing repository.

### Decision Detail

Do not rename the live Django project from `labsystem` to `hms` during the first migration phase.

### Why

- renaming the top-level Django project creates avoidable migration risk,
- deployment settings and runtime assumptions already exist,
- the business gain from renaming is low,
- the operational risk is high compared to the value.

### Result

We keep:

- `labsystem/` project package
- `manage.py`

And add new apps such as:

- `accounts`
- `reception`
- `doctor`
- `nurse`
- `admin_dashboard`

---

## 4.2 Database Decision

### Decision

PostgreSQL is the only supported production database for the expanded HMS.

### Why

- multi-tenant hospital data must be persistent and reliable,
- multiple users and modules will hit the database concurrently,
- queueing, payments, and clinical workflows are not suitable for SQLite in production,
- PostgreSQL aligns with the current production direction already under discussion.

### Result

- production uses PostgreSQL,
- local development may still use SQLite for speed when appropriate,
- staging should use PostgreSQL wherever workflow realism matters.

---

## 4.3 Tenant Isolation Decision

### Decision

Tenant isolation must be enforced at multiple layers, not only in middleware.

### Why

Middleware can detect the active tenant, but it cannot by itself prevent all leakage.

### Required enforcement layers

- request tenant detection,
- queryset filtering,
- foreign keys to `Hospital`,
- form choice filtering,
- admin filtering,
- background task scoping,
- report filtering,
- permission checks.

### Result

Every operational model must be reviewed for whether it requires:

- direct `hospital = ForeignKey(Hospital, ...)`
- or guaranteed access through a tenant-owned parent model.

---

## 4.4 Hospital Detection Strategy

### Decision

Use request-bound hospital detection, but treat it as tenant context, not full tenant security.

### Approach

Use middleware to identify the hospital from:

- subdomain, or
- other deployment-safe routing strategy.

### Result

Middleware sets:

- `request.hospital`

But application code must still enforce:

- tenant-safe filtering
- role-safe access

---

## 4.5 User Model Decision

### Decision

The broader HMS should use a hospital-aware user model, but the implementation path must be chosen carefully before app expansion begins.

### Why

The future system needs:

- role awareness,
- hospital linkage,
- superadmin separation,
- and staff-type authorization.

### Risk

Changing Django's user model late is one of the riskiest framework migrations.

### Decision Status

This is a required architectural decision before serious Phase 0 build work.

### Recommended paths

#### Preferred path

If practical before further growth:

- introduce a custom user model early,
- then build the rest of the HMS on top of it.

#### Transitional path

If a direct auth swap would be too risky for the current deployed system:

- keep auth user temporarily,
- introduce a hospital-linked role/profile model,
- then plan a later migration with full care.

### Current Recommendation

Do a careful technical audit first, then decide whether the project can still safely adopt `AUTH_USER_MODEL` now.

---

## 4.6 Clinical Backbone Decision

### Decision

`Patient`, `Visit`, `Service`, and `QueueEntry` become the operational backbone of the platform.

### Why

Without a visit-centered backbone:

- reception stays disconnected,
- lab reports remain standalone,
- doctor consultations cannot be tied cleanly to services,
- nurse follow-through becomes inconsistent,
- payments become harder to reason about.

### Result

The expanded system should revolve around:

- `Patient`
- `Visit`
- `VisitService`
- `QueueEntry`

---

## 4.7 Lab Module Strategy Decision

### Decision

Refactor the current lab module rather than rewriting it.

### Why

The current lab module already provides strong value:

- test templates,
- learning defaults,
- report entry patterns,
- print layouts,
- mobile improvements,
- CBC and urinalysis workflows.

### Result

Preserve:

- `TestProfile`
- `TestProfileParameter`
- `TestCatalog`
- `ReferenceRangeDefault`
- `TestResult`
- report printing and template structures

Refactor:

- `LabReport`
- views
- routing
- patient/visit integration
- tenant scoping

---

## 4.8 Lab Data Ownership Decision

### Decision

Lab reports should become visit-linked while still keeping snapshot fields for print and history safety.

### Why

If lab reports rely only on live patient records, printed history may drift after later edits.

### Result

Keep on `LabReport` for now:

- `patient_name`
- `patient_age`
- `patient_sex`
- `referred_by`

Add:

- `hospital`
- `visit`

This gives us:

- workflow linkage,
- tenant safety,
- historical report integrity.

---

## 4.9 Queue Architecture Decision

### Decision

Queues should be generic and service-driven, not hardcoded independently inside each clinical module.

### Why

Reception should be able to send a patient into:

- lab,
- doctor,
- nurse,
- or future modules

without each module inventing its own isolated queue logic.

### Result

Introduce a shared `QueueEntry` model with:

- `visit`
- `hospital`
- `queue_type`
- `processed`
- timestamps
- optional assignment/priority fields later

---

## 4.10 Deployment Strategy Decision

### Decision

Production and staging must be separated by branch and environment.

### Why

The user already raised a real operational problem:

- major unfinished work exists,
- but urgent lab updates may still need to go live.

### Result

Recommended branch/environment model:

- `main` -> production
- `develop` or `staging` -> integration testing
- `hotfix/...` -> urgent production fixes

Recommended hosting model:

- one production environment
- one staging environment
- separate PostgreSQL databases

---

## 5. Target App Layout

Recommended target apps:

- `accounts`
- `reception`
- `lab`
- `doctor`
- `nurse`
- `admin_dashboard`

Optional later:

- `billing`
- `inventory`
- `pharmacy`
- `notifications`
- `api`

---

## 6. Migration Strategy From the Current Lab System

This is the most important operational section.

## 6.1 Migration Principle

Do not break the working lab module while introducing the platform.

### Rule

Every migration step should preserve one of these two states:

- the system still works as the current lab product,
- or the system works as the next stable tenant-aware version.

No half-state should be deployed to production.

---

## 6.2 Migration Sequence

### Step 1: Freeze the current stable lab baseline

Before platform expansion:

- identify the last stable production lab state,
- tag it,
- preserve data backups,
- confirm branch discipline.

Recommended tag example:

- `lab-stable-v1`

### Step 2: Introduce staging discipline

Before major work:

- create `develop` or `staging` branch,
- stop putting unfinished work directly on `main`,
- set up a staging deployment if possible.

### Step 3: Add tenant core before touching lab workflows

Build first:

- `Hospital`
- subscription structures
- tenant detection
- auth strategy

Do not yet rewrite lab behavior at this step.

### Step 4: Add reception backbone

Build:

- `Patient`
- `Visit`
- `Service`
- `VisitService`
- `QueueEntry`
- `Payment`

This gives the lab refactor somewhere correct to attach.

### Step 5: Refactor `LabReport` to be tenant-aware and visit-aware

At this step:

- add `hospital`
- add `visit`
- keep existing patient snapshot fields
- update queries to filter by hospital

### Step 6: Change lab entry flow

Move from:

- free standalone patient biodata entry

Toward:

- reception-created visit
- lab queue selection
- report linked to visit

### Step 7: Preserve compatibility for old reports

Existing old reports should remain displayable and printable even if they predate `Visit`.

That means:

- nullable `visit` during transition
- no destructive assumption that all old rows have visit links

### Step 8: Add doctor and nurse workflows

After visit and queue become stable:

- add doctor queue
- add consultation
- add nurse queue
- add nurse notes

### Step 9: Add financial/admin layers

Only after operational flow is stable:

- add hospital dashboard
- add user management
- add expenses
- add salaries
- add inventory

---

## 6.3 Existing Lab Data Migration Rules

To protect the current lab data:

- do not delete current `LabReport` records,
- do not require all old reports to have `hospital` immediately unless a safe default exists,
- do not require all old reports to have `visit` immediately,
- write one or more data migrations to backfill what can be inferred safely.

Recommended transitional strategy:

1. add nullable `hospital` and `visit`,
2. backfill `hospital` where safe,
3. leave `visit` nullable for legacy reports,
4. make new records require `visit` once reception is live.

---

## 6.4 Print Safety Rule

Printing must continue to work throughout the migration.

That means:

- reports must keep their own printable patient snapshot,
- historical print output must not depend entirely on mutable patient records,
- template layout changes must be tested separately from platform architecture changes.

---

## 7. Exact First Build Sequence for Phase 0 and Phase 1

This section turns the big plan into the first executable work package.

## 7.1 Phase 0: Project Setup and Foundation

### Goal

Prepare the current repo for platform growth without destabilizing the lab module.

### Sequence

#### 0.1 Branch and deployment discipline

Do first:

- confirm `main` is production-only,
- create `develop` branch,
- identify whether a staging deployment will exist,
- verify separate production and staging databases.

#### 0.2 Architecture audit

Review:

- current auth usage,
- current deployed user assumptions,
- current PostgreSQL behavior,
- current branch/deploy workflow,
- current lab model dependencies.

#### 0.3 Add `accounts` app

Add:

- `Hospital`
- `SubscriptionPlan`
- `AuditLog`
- payment/subscription entities

But do not yet wire every app to them.

#### 0.4 Make the auth decision

Before more feature work:

- decide whether to adopt custom `AUTH_USER_MODEL` now,
- or use a transitional profile strategy.

This is a hard gate.

#### 0.5 Add tenant context middleware

Add middleware that resolves:

- current hospital

But keep enforcement rules explicit in app code too.

#### 0.6 Add tenant-safe helper conventions

Introduce reusable patterns such as:

- hospital-filtered queryset helpers,
- tenant-aware decorators,
- tenant ownership checks.

#### 0.7 Create staging-safe configuration

Ensure:

- environment variables are separated,
- production and staging DBs are not mixed,
- no production DB is used for local experimentation.

### Phase 0 deliverables

- branch strategy locked,
- tenant core models added,
- auth decision made,
- middleware added,
- staging plan documented.

---

## 7.2 Phase 1: Developer Super Admin Portal

### Goal

Give the platform owner visibility and control across hospitals without yet depending on the full hospital workflow stack.

### Sequence

#### 1.1 Add `admin_dashboard` app

Create the app and start with:

- developer dashboard
- hospital list
- subscription overview
- expiring subscriptions view

#### 1.2 Restrict by role

Only superadmin users should reach:

- developer dashboard
- global hospital management
- global audit visibility

#### 1.3 Add hospital activation controls

Build:

- hospital active/inactive toggles
- hospital details
- plan association

#### 1.4 Add subscription payment tracking

Build:

- subscription payment records
- total income summary
- expiring subscription visibility

#### 1.5 Add notifications

Implement:

- subscription expiry warnings
- daily scheduled checks

### Phase 1 deliverables

- superadmin portal
- hospital oversight
- subscription visibility
- notification baseline

---

## 8. What Must Not Happen During Early Implementation

Avoid these mistakes:

- renaming the whole project immediately,
- rewriting the lab module from scratch,
- introducing doctor/nurse apps before visit and queue exist,
- mixing staging and production databases,
- changing auth casually after multiple new apps are already built,
- making legacy lab reports dependent on new visit-only rules.

---

## 9. Suggested Decision Log Format

For the real build, every major decision should be captured like this:

- Decision
- Status
- Context
- Chosen option
- Rejected options
- Consequences

This will help especially with:

- auth model choice,
- tenancy implementation,
- hosting strategy,
- lab migration approach.

---

## 10. Recommendation Summary

The implementation report is strong.

The best way to make it succeed in this repo is:

- keep the current project root,
- treat PostgreSQL as the real platform database,
- build tenant/auth foundation first,
- introduce reception and visit backbone before doctor/nurse,
- refactor lab into that backbone,
- preserve report printing and historical safety,
- and separate staging from production immediately.

---

## 11. Immediate Next Recommended Work Items

If we continue from here, the next concrete deliverables should be:

1. `accounts` app creation plan,
2. auth strategy decision note,
3. reception backbone ER sketch,
4. lab-to-visit refactor checklist,
5. branch/deployment workflow note for production vs staging.

---

Prepared as the architecture decision and migration companion document for the HMS platform shift.
