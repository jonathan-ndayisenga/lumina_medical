# Multi-Tenant HMS Execution Package

Prepared on April 17, 2026 as the execution companion to:

- `docs/MULTI_TENANT_HMS_SHIFT_FEASIBILITY_PLAN.md`
- `docs/MULTI_TENANT_HMS_ARCHITECTURE_DECISIONS.md`

---

## 1. Purpose

This document turns the strategy into a practical execution package we can work from.

Its job is to answer:

- what we build first,
- what must be decided before coding,
- what gets delivered in each implementation block,
- how we protect the current working lab module,
- and how implementation should be guided from kickoff to rollout.

---

## 2. Executive Position

We can start implementation soon, but not by jumping straight into all modules at once.

The correct start point is:

1. freeze the current stable lab baseline,
2. lock branch and deployment discipline,
3. make the authentication decision,
4. add the tenant core,
5. then begin the reception backbone.

That means:

- we are close to implementation,
- but the first implementation work should be foundation work,
- not doctor, nurse, or finance screens yet.

---

## 3. Implementation Readiness

## 3.1 What is already in a good place

The current repository already gives us a strong starting point:

- a working Django project,
- an operational lab module,
- template-driven report generation,
- CBC and urinalysis workflows,
- mobile and print improvements,
- PostgreSQL awareness in production discussions,
- and planning documents that now define the target direction.

## 3.2 What must be locked before implementation starts

These are the non-negotiable preconditions:

### A. Branch Discipline

We need:

- `main` for production-only code,
- `develop` for in-progress HMS expansion,
- `hotfix/...` for urgent lab fixes.

### B. Environment Discipline

We need:

- separate production and staging databases,
- no experimental work against the production database,
- and no unfinished multi-tenant work deployed directly to live users.

### C. Authentication Decision

Before major app expansion, we must decide:

- custom user model now,
- or transitional hospital-linked user profile strategy.

This is the biggest early architectural gate.

### D. Baseline Tag

We should tag the current working lab system before the major shift.

Suggested tag:

- `lab-stable-v1`

---

## 4. Scope of the First Execution Wave

The first execution wave should cover only the platform foundation.

It should not yet try to deliver the full hospital system.

### Execution Wave 1 includes

- branch and deployment setup,
- `accounts` app,
- tenant core models,
- auth strategy implementation,
- hospital context middleware,
- role model groundwork,
- staging safety rules,
- developer superadmin baseline.

### Execution Wave 1 does not include

- reception CRUD screens in full,
- visit-driven lab workflow refactor,
- doctor queue,
- nurse queue,
- expenses,
- salaries,
- inventory,
- final hospital admin dashboards.

That keeps the first implementation block realistic and safe.

---

## 5. Workstreams

The project should be executed through five controlled workstreams.

## 5.1 Workstream A: Platform Foundation

Focus:

- accounts app,
- hospital model,
- subscription model,
- audit logging,
- auth strategy,
- request tenant context.

Output:

- the repo becomes ready for multi-tenant growth.

## 5.2 Workstream B: Reception Backbone

Focus:

- patient,
- visit,
- service catalog,
- visit service lines,
- queue entry,
- payment.

Output:

- a real patient journey backbone exists.

## 5.3 Workstream C: Lab Refactor

Focus:

- hospital-aware lab data,
- visit-linked lab workflow,
- queue entry integration,
- legacy report safety,
- template preservation.

Output:

- the current lab engine becomes part of the broader platform.

## 5.4 Workstream D: Clinical Expansion

Focus:

- doctor queue,
- consultation,
- nurse queue,
- nurse notes.

Output:

- the patient flow becomes end-to-end across care roles.

## 5.5 Workstream E: Admin and Finance

Focus:

- hospital admin dashboard,
- user management,
- service pricing,
- financial summaries,
- subscription oversight,
- inventory and expense tracking.

Output:

- the platform becomes operationally manageable and commercially usable.

---

## 6. Recommended Implementation Order

This is the recommended build order for the actual repo.

### Step 1: Protect the Current Lab System

Do first:

- tag the current stable state,
- create `develop`,
- confirm production deploys only from `main`,
- confirm staging uses a separate environment.

### Step 2: Create the Accounts Core

Build:

- `Hospital`
- `SubscriptionPlan`
- `AuditLog`
- subscription payment model

At this stage, the rest of the apps do not yet need full integration.

### Step 3: Resolve Auth Strategy

Choose and implement one of:

- custom hospital-aware user model now,
- or transitional profile strategy.

This should be completed before building dependent role-heavy modules.

### Step 4: Add Tenant Context

Build:

- hospital detection middleware,
- tenant helper utilities,
- tenant-aware access conventions.

### Step 5: Build the Superadmin Baseline

Build:

- developer dashboard,
- hospital list,
- activation/deactivation,
- subscription visibility,
- expiring subscription notification baseline.

### Step 6: Build the Reception Backbone

Build:

- `Patient`
- `Visit`
- `Service`
- `VisitService`
- `QueueEntry`
- `Payment`

This becomes the shared workflow backbone.

### Step 7: Refactor the Lab Module Into the Backbone

Add:

- `hospital` on lab-owned models where needed,
- `visit` on `LabReport`,
- hospital-scoped query filtering,
- queue-based entry to lab work.

Keep:

- report print safety,
- patient snapshot fields,
- template behavior,
- learning behavior.

### Step 8: Add Doctor and Nurse Modules

Build:

- doctor queue,
- consultation records,
- nurse queue,
- nurse notes.

### Step 9: Add Hospital Admin and Finance

Build:

- hospital dashboards,
- pricing management,
- financial summaries,
- expense tracking,
- salary tracking,
- inventory.

### Step 10: Integration and Hardening

Run:

- tenant leakage tests,
- end-to-end workflow tests,
- finance correctness checks,
- queue lifecycle tests,
- print regression tests,
- performance review.

---

## 7. Concrete Sprint Package

This is the most practical way for us to start.

## Sprint 0: Stabilization and Control

### Goal

Create a safe lane for the major shift.

### Deliverables

- `main` protected as production branch
- `develop` branch created
- stable tag created
- staging deployment strategy documented
- production and staging DB separation confirmed

### Exit Criteria

- no more unfinished work goes directly to production
- the current lab system is recoverable at any time

## Sprint 1: Accounts and Auth Foundation

### Goal

Introduce the tenant core safely.

### Deliverables

- `accounts` app scaffolded
- `Hospital` model
- subscription models
- audit log model
- auth strategy implemented
- hospital middleware added

### Exit Criteria

- request tenant context exists
- user/hospital relationship path is established
- superadmin path is conceptually supported

## Sprint 2: Superadmin Baseline

### Goal

Give platform-level control before hospital workflow expansion.

### Deliverables

- developer dashboard
- hospital management view
- hospital status toggle
- subscription overview
- expiring subscription warning mechanism

### Exit Criteria

- superadmin can see and manage hospitals centrally

## Sprint 3: Reception Backbone

### Goal

Introduce the operational spine of the product.

### Deliverables

- patient registration
- visit creation
- service selection
- queue entry creation
- payment registration

### Exit Criteria

- a patient can be registered and routed into a visit-backed workflow

## Sprint 4: Lab Refactor

### Goal

Plug the existing lab system into the new patient journey.

### Deliverables

- lab models tenant-aware
- lab reports optionally visit-linked
- queue-driven lab intake
- lab reports filtered by hospital
- legacy report compatibility retained

### Exit Criteria

- the lab module still works,
- but it now belongs to the HMS backbone rather than standing alone

---

## 8. First Technical Deliverables We Should Produce

Before or during Sprint 1, we should create these implementation artifacts:

### 8.1 Accounts Blueprint

Document:

- exact models,
- field list,
- admin behavior,
- migration order,
- role strategy.

### 8.2 Auth Decision Record

Document:

- whether we are adopting `AUTH_USER_MODEL` now,
- why,
- migration implications,
- fallback plan.

### 8.3 Reception ER Sketch

Document:

- `Patient`
- `Visit`
- `Service`
- `VisitService`
- `QueueEntry`
- `Payment`

and their exact relationships.

### 8.4 Lab Refactor Checklist

Document:

- what fields are added,
- what views change,
- what stays backward-compatible,
- and what is delayed until later.

### 8.5 Tenant Safety Checklist

Document:

- every model that must carry hospital ownership,
- every view that must filter by hospital,
- every admin/form/API risk point.

---

## 9. Feasibility by Implementation Stage

| Stage | Feasibility | Risk | Notes |
|------|-------------|------|-------|
| Stabilization | High | Low | Mostly process and environment control |
| Accounts/Auth | Medium-High | High | Main architectural risk area |
| Superadmin | High | Medium | Straightforward after tenant core exists |
| Reception Backbone | High | Medium | Clean Django work if modeled well |
| Lab Refactor | High | Medium-High | Best reuse zone, but migration-sensitive |
| Doctor/Nurse | High | Medium | Depends on queue stability |
| Finance/Admin | Medium | Medium | Must be correct, but not conceptually hard |
| Hardening | Medium-High | Medium | Requires discipline more than invention |

---

## 10. Risks We Must Actively Control

## 10.1 Shipping Half-Migrated Workflow Code

If lab is partially refactored but reception is not stable, the system will become harder to trust.

### Control

- release only stable slices,
- keep compatibility during migration,
- use staging for all major workflow changes.

## 10.2 Tenant Leakage

A single missed queryset can expose another hospital's data.

### Control

- hospital-aware model ownership,
- tenant-safe query rules,
- explicit review checklist,
- dedicated tenant isolation tests.

## 10.3 Auth Regret

If we dodge the auth decision too long, later modules will become harder to migrate.

### Control

- make the auth decision early,
- document it,
- implement it before deep module expansion.

## 10.4 Breaking Print and Historical Reports

The lab system already has real print behavior that matters operationally.

### Control

- preserve report snapshot fields,
- treat print regression as a release blocker,
- do not over-couple old reports to new visit-only assumptions.

---

## 11. When We Can Start Implementation

We can start implementation as soon as these four things are true:

1. `develop` branch exists,
2. production and staging are separated,
3. we agree on the auth strategy,
4. we agree to start with foundation work, not full-module work.

If those four are accepted, we can start immediately with Sprint 0 and Sprint 1.

---

## 12. How I Will Guide the Implementation

I can guide this in a structured, low-chaos way.

### I will help with:

- turning each sprint into a smaller coding package,
- designing the models before we touch migrations,
- checking migration safety before changes land,
- protecting the current lab module while we refactor,
- writing supporting docs as we go,
- and helping you keep production safe while development keeps moving.

### Practical guidance pattern

For each stage, we should work in this rhythm:

1. define the exact scope,
2. write the implementation note,
3. make the code changes,
4. run migrations/checks/tests,
5. review impact on production and staging,
6. then move to the next slice.

That rhythm will keep the shift manageable.

---

## 13. Immediate Recommended Next Actions

These are the next three concrete moves.

### Action 1

Create or confirm:

- `develop` branch
- branch/deploy rule for production vs staging

### Action 2

Draft the `accounts` app blueprint and auth decision record

### Action 3

Begin Sprint 0 and Sprint 1 implementation

That is the right practical starting line.

---

## 14. Final Recommendation

The right move is not to try to build the whole HMS immediately.

The right move is to:

- stabilize the working lab baseline,
- create the execution lane,
- build the tenant/auth core,
- then grow the rest of the system around that backbone.

That gives us the best chance of building something ambitious without damaging what already works.

---

Prepared as the execution package for the multi-tenant HMS shift.
