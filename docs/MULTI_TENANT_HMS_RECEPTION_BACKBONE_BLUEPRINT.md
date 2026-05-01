# Multi-Tenant HMS Reception Backbone Blueprint

Prepared on April 17, 2026 as the next implementation package after the accounts/auth foundation.

---

## 1. Purpose

This document defines the first operational backbone of the broader HMS:

- patient registration,
- visit creation,
- service selection,
- queue generation,
- and payment capture.

This is the layer that will let the current lab module stop behaving like a standalone island.

---

## 2. Why Reception Comes Next

After accounts/auth, reception is the correct next move because it creates the shared workflow backbone that other modules depend on.

Without it:

- lab reports stay standalone,
- doctor consultations have no durable visit anchor,
- nursing flow cannot be routed consistently,
- and finance has no clean transaction base.

So reception is not "just another module."

It is the spine of the patient journey.

---

## 3. Scope

The reception backbone is responsible for:

- registering patients,
- creating visits,
- selecting billable services,
- generating queue entries,
- recording visit payment state.

It is not yet responsible for:

- final lab refactor,
- doctor consultation forms,
- nurse note workflow,
- inventory,
- payroll,
- or full finance reporting.

---

## 4. Core Models

## 4.1 Patient

Purpose:

- represent a hospital-owned patient record

Required fields:

- `hospital`
- `name`
- `age`
- `sex`
- `contact`
- `created_at`

Notes:

- keep `age` as text for compatibility with current lab age formats such as `22YRS` or `6MTH`
- this can evolve later if a DOB strategy is introduced

## 4.2 Visit

Purpose:

- represent one operational encounter for a patient

Required fields:

- `patient`
- `hospital`
- `visit_date`
- `status`
- `total_amount`
- `created_by`
- `notes`

Notes:

- this becomes the parent record for downstream workflow
- one patient can have many visits

## 4.3 Service

Purpose:

- define selectable hospital services with pricing

Required fields:

- `hospital`
- `name`
- `category`
- `price`
- `is_active`

Categories:

- `consultation`
- `lab`
- `procedure`
- `pharmacy`
- `other`

Notes:

- hospital-owned pricing is important for tenant isolation

## 4.4 VisitService

Purpose:

- capture which services were attached to a given visit

Required fields:

- `visit`
- `service`
- `price_at_time`
- `notes`
- `created_at`

Notes:

- keep `price_at_time` to preserve historical billing integrity even if the service price later changes

## 4.5 QueueEntry

Purpose:

- drive the shared workflow queue for lab, doctor, nurse, and future modules

Required fields:

- `hospital`
- `visit`
- `queue_type`
- `processed`
- `processed_at`
- `created_at`
- `notes`

Initial queue types:

- `lab_reception`
- `doctor`
- `nurse`

Notes:

- queue should remain generic
- later we can add assignment, priority, status detail, and timestamps

## 4.6 Payment

Purpose:

- record visit-level payment

Required fields:

- `visit`
- `amount`
- `mode`
- `status`
- `paid_at`
- `recorded_by`
- `notes`

Modes:

- `cash`
- `card`
- `mobile_money`
- `insurance`

Statuses:

- `pending`
- `paid`
- `part_paid`
- `waived`

Notes:

- starting with one payment record per visit is acceptable for the early phase
- later we can support partial and multiple payment rows if needed

---

## 5. Relationship Summary

The core relationship model should be:

- one `Hospital` has many `Patient`
- one `Patient` has many `Visit`
- one `Visit` has many `VisitService`
- one `Visit` has many `QueueEntry`
- one `Visit` has zero or one `Payment` in the first phase
- one `Hospital` has many `Service`

This gives the rest of the HMS a clean operational structure.

---

## 6. Workflow Rules

## 6.1 Reception Registration Flow

Expected first-phase flow:

1. receptionist selects or creates patient
2. receptionist creates visit
3. receptionist selects services
4. system creates `VisitService` rows
5. system calculates visit total
6. system creates queue entries from selected service categories
7. receptionist records payment state

## 6.2 Queue Generation Rules

Initial queue generation should be simple and deterministic:

- `consultation` -> `doctor`
- `lab` -> `lab_reception`
- optional downstream nurse queue later from doctor workflow

Avoid over-automating beyond this in the first version.

---

## 7. Tenant Safety Rules

Every reception model must be hospital-safe.

That means:

- `Patient` belongs to `Hospital`
- `Visit` belongs to `Hospital`
- `Service` belongs to `Hospital`
- `QueueEntry` belongs to `Hospital`
- payment logic must only see the active hospital's visits

Views and forms must always filter by:

- `request.hospital`

or through hospital-owned parent records.

---

## 8. Lab Integration Rules

The reception backbone must be designed with the coming lab refactor in mind.

That means:

- `LabReport` should later gain a nullable `visit` link
- queue entries of `lab_reception` should become the entry point into lab work
- lab reports should preserve their patient snapshot fields even after visit linkage exists

This keeps current report stability while enabling platform integration.

---

## 9. First Implementation Slice

The first reception implementation slice should only do the following:

- scaffold the `reception` app
- add the core models
- register them in admin
- prepare migrations

It should not yet build:

- the full receptionist UI,
- full payment UX,
- queue dashboards,
- or lab-linked execution screens.

We keep the slice small and stable.

---

## 10. Completion Criteria for the Reception Foundation

The reception backbone foundation is ready for the next step when:

- the app exists,
- the models exist,
- the admin registrations exist,
- the migrations are generated,
- and the relationships support future queue and lab integration.

At that point, the next implementation slice becomes:

- receptionist UI and workflow,
- or lab refactor against `Visit`.

---

## 11. Final Position

The reception backbone is the right next engineering move.

It gives us:

- patient ownership,
- visit-centric workflow,
- service-driven queue generation,
- and the cleanest path into lab, doctor, and nurse integration.

---

Prepared as the reception backbone blueprint for the multi-tenant HMS rollout.
