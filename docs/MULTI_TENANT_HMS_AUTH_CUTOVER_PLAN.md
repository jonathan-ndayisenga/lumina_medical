# Multi-Tenant HMS Auth Cutover Plan

Prepared on April 17, 2026 for the custom-user migration now introduced into the codebase.

---

## 1. Purpose

This document explains how we safely move from the current lab-first project state into the new custom-user foundation without damaging the working system.

This plan exists because:

- the codebase now declares `AUTH_USER_MODEL = "accounts.User"`,
- the existing database already has Django auth/admin migrations applied,
- and Django does not allow a casual in-place swap from the default user to a custom user on an established database.

So the correct move is a controlled cutover, not a forced live migration.

---

## 2. Current Reality

Right now we have two truths:

### Truth A: The code foundation is correct

We now have:

- `accounts.User`
- `Hospital`
- `SubscriptionPlan`
- `AuditLog`
- `HospitalSubscriptionPayment`
- tenant middleware
- superadmin and hospital admin entry points

### Truth B: The existing database cannot absorb this safely in-place

The current migration conflict proves that the legacy database history was built around Django's default auth user.

That means:

- the code can move forward,
- but the database transition needs its own rollout plan.

---

## 3. Decision

We will use a staging cutover strategy.

### Decision summary

- do not force the custom user model into the current working database,
- create a clean staging database,
- apply the new schema there,
- migrate the required legacy data into the new shape,
- verify authentication and permissions there,
- then perform a controlled production cutover later.

---

## 4. Why This Is the Right Approach

This is the safest option because it:

- preserves the current working lab baseline,
- lets us validate the new foundation in a realistic environment,
- avoids irreversible auth migration damage,
- and gives us a rehearsal path before production.

It also matches the real risk we just surfaced early, which is a good thing.

---

## 5. Environments We Need

## 5.1 Current Production

This remains untouched for now.

Use it only for:

- stable business operation,
- urgent hotfixes,
- and reference behavior.

## 5.2 Development

Local development remains our build/test environment for:

- code changes,
- migrations,
- view wiring,
- and non-production experimentation.

## 5.3 Staging

This is now the key environment.

Staging should have:

- its own PostgreSQL database,
- the new custom-user schema,
- safe test credentials,
- imported legacy data where needed,
- and the same environment style as production.

---

## 6. Cutover Strategy

The migration should happen in five controlled steps.

### Step 1: Freeze the Stable Baseline

Before doing anything else:

- tag the stable lab system,
- confirm `main` remains production-only,
- keep `develop` as the HMS branch,
- and confirm the production database is backed up.

### Step 2: Apply the New Schema to Staging Only

On a fresh staging database:

- deploy the current multi-tenant/auth foundation,
- run migrations cleanly,
- create initial superadmin account,
- create initial hospital records,
- verify login flow.

### Step 3: Migrate Legacy Business Data Into the New Schema

Move over only what we truly need first:

- lab reports,
- test results,
- test templates,
- test catalog,
- reference defaults.

For the first staging pass:

- do not try to migrate everything at once,
- map legacy users carefully,
- and create hospital ownership defaults explicitly.

### Step 4: Validate Role and Workflow Behavior

Before any production cutover:

- test superadmin access,
- test hospital admin access,
- test lab attendant access,
- confirm the lab module still works,
- confirm print behavior still works,
- confirm tenant context behaves correctly.

### Step 5: Production Cutover Later

Only after the staging version is stable:

- schedule production cutover,
- back up the production database again,
- deploy the new code to a production-ready database,
- import production data through the tested path,
- verify all critical flows.

---

## 7. Data Migration Scope for the First Cutover

The first cutover should focus on preserving the current lab business value.

## 7.1 Data We Must Preserve

- users needed for access
- lab reports
- test results
- test templates
- learned catalog entries
- reference defaults

## 7.2 Data We Can Defer

At this stage, these future HMS entities do not yet exist live:

- visits
- services
- queue entries
- consultations
- nurse notes
- expenses
- salaries
- inventory

So we should not pretend to migrate them yet.

---

## 8. Legacy-to-New Mapping Strategy

## 8.1 Users

Legacy system users must be recreated or mapped into `accounts.User`.

Recommended first-pass mapping:

- existing superusers become `role='superadmin'`
- existing staff lab users become `role='lab_attendant'`
- assign hospital later only after hospital records exist

## 8.2 Lab Reports

Legacy `LabReport` rows should remain valid in the new system.

For now:

- preserve the report record itself
- preserve patient snapshot fields
- preserve result rows
- preserve profile linkage where available

Future `visit` linkage can be introduced later.

## 8.3 Hospital Ownership

For the first migration wave:

- create a default hospital record for the existing Lumina operation
- assign imported operational records to that hospital where appropriate

This gives us tenant ownership without inventing fake complexity.

---

## 9. What We Should Not Do

Avoid these mistakes:

- do not run the new auth schema against the current live database first
- do not manually hack migration tables in production
- do not try to solve this by removing old Django auth migrations
- do not ship unfinished tenant logic live while auth is changing
- do not mix production user data with staging experiments

---

## 10. Staging Validation Checklist

The staging cutover is considered successful only if all of these are true:

- migrations run cleanly on a fresh staging database
- superadmin can log in
- hospital admin can log in
- lab attendant can log in
- dashboard routing works by role
- lab report list works
- report create/edit works
- CBC and urinalysis templates still work
- print output still works
- no obvious tenant leakage exists in current views

---

## 11. Production Readiness Checklist

Do not cut over production until:

- staging has passed the checklist above
- legacy data import has been rehearsed
- rollback steps are written down
- production backup is confirmed
- first-day admin accounts are prepared
- environment variables are validated

---

## 12. Recommended Immediate Next Actions

These are the next implementation actions from here.

### Action 1

Create the accounts/auth implementation blueprint

### Action 2

Prepare the initial hospital bootstrap plan:

- first hospital record
- first superadmin
- first hospital admin
- first lab attendant role mapping

### Action 3

Set up the staging database and validate the custom-user schema there

### Action 4

Begin the next code slice only after staging is ready:

- superadmin refinement
- hospital admin refinement
- reception backbone

---

## 13. Final Position

The custom-user decision is still the right one.

The only adjustment is that the rollout must be staged and deliberate.

That means we are not blocked.

We simply move from:

- "in-place migration thinking"

to:

- "staging cutover and controlled data migration"

That is the mature and production-safe path.

---

Prepared as the cutover plan for the custom-user foundation.
