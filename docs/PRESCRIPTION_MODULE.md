# Prescriptions and dispensing: how it works

How Ternah Health prescribes, bills and dispenses medicine, and how stock moves with it. This describes the code as of November 2026, including the Pharmacy module (section 9). It's written so the same design can be rebuilt in another system.

## The pieces

| Piece | Where | What it holds |
|---|---|---|
| Drug (stock item) | `admin_dashboard.models.InventoryItem` | Name, category, units, prices, stock on hand, reorder level. One row per drug per hospital. |
| Batch | `admin_dashboard.models.InventoryBatch` | A delivery of a drug: batch number, quantity left, expiry date, unit cost. |
| Stock movement | `admin_dashboard.models.InventoryTransaction` | Every receive, dispense (`consume`) or correction (`adjust`), with who, when, the visit, the prescription and which batches were used. |
| Prescription | `doctor.models.Prescription` | One drug for one visit: dose, how often, how many days, calculated quantity and price, and whether it has been dispensed. |
| Bill line | `reception.models.VisitService` | The charge on the patient's bill. Each billed prescription has one, under a service named "Pharmacy Item: <drug>" in the Pharmacy category. |
| Payment | `reception.models.Payment` | Money received against the whole visit (not against single items). Part payments are allowed. |

## 1. Units and prices

A drug is stored in one unit and dispensed in another, so every item records both:

- `unit`: how it is stocked and sold (pack, bottle, tube).
- `base_unit`: what is counted out to the patient (tablet, capsule, ml).
- `units_per_pack`: how many base units are in one stocked unit.
- `strength_mg_per_unit`: mg in one tablet or capsule.
- `selling_price`: price of one stocked unit. The price of one base unit is `selling_price / units_per_pack`.
- `days_covered_per_pack`: for tubes (creams), how many days one tube lasts.

`to_stock_quantity()` converts a dispensed quantity into stock units, and `available_dispense_quantity` gives what can still be given out.

## 2. How the quantity and price are worked out

The doctor enters a dose, times per day and number of days. `Prescription.calculate_totals()` runs on every save:

| Drug type | Quantity | Price |
|---|---|---|
| Tablets, capsules (default) | `ceil(dose ÷ strength × times per day × days)` base units. If strength is missing, `ceil(times per day × days)`. | quantity × price per base unit |
| Liquids (syrup, IV fluid, IM, reagent) | total ml = dose × times per day × days; packs = `ceil(total ml ÷ units per pack)` | packs × selling price |
| Tubes | packs = `ceil(days ÷ days covered per pack)` | packs × selling price |

Quantities always round **up**, so the patient never gets less than the course needs.

## 3. Prescribing

`doctor.views.add_prescription_api` (doctors, admins and receptionists can call it):

1. Checks the visit belongs to the hospital and isn't cancelled.
2. Only drugs that are active and belong to the same hospital can be chosen.
3. Creates the prescription, which works out its quantity and price.
4. Decides who pays:
   - **Adjustment visit** (a drug changed part-way through a course): marked "covered by previous". No new charge.
   - **Covered by a package** the patient bought: no new charge, linked to the package.
   - **Otherwise:** adds a Pharmacy bill line for the price and adds it to the visit total.
5. IV, IV medicine and IM drugs are marked `nursing_managed`: nurses give them dose by dose on the ward instead of handing over the whole course. If the patient is already admitted, the doses are added to their nursing care list.

## 4. Dispensing

Every screen dispenses through one shared step (section 9). There are three places that dispense, plus the Pharmacy:

| Who | Where | When it's used |
|---|---|---|
| Nurse | `nurse.views.dispense_prescription` | The patient goes from the doctor to the nurse queue. |
| Reception | `reception.views.dispense_prescription` | At billing, on the Complete Visit page. |
| Reception, walk-in | `reception.views.quick_dispense_start` then the Complete Visit page | Someone buys medicine without seeing a doctor. A "walk-in" patient record is used if needed. |

The steps, every time:

1. Must be a POST, and the visit must not be cancelled.
2. If already dispensed, stop.
3. Check there is enough stock. If not, refuse with a message.
4. Take the stock off. With batches, the batch that **expires first** is used first (FEFO); reception can choose a specific batch.
5. Record an `InventoryTransaction` of type `consume`, including the exact batches used (`batch_breakdown`).
6. Mark the prescription dispensed, with the time and who did it.
7. Mark its bill line as performed.

Dispensing does **not** require the bill to be paid. It records how much had been paid at that moment (section 9).

## 5. Undoing

- **Removing a prescription** (`remove_prescription_workflow`): if it was already dispensed, the stock goes back to the **same batches** it came from (using `batch_breakdown`). The bill line is deleted and the visit total reduced. A completed visit that's no longer fully paid goes back to "ready for billing".
- **Voiding a walk-in sale** (`void_dispense_visit`): only if nothing has been paid. Returns all stock, deletes the charges (which reverses their accounting entries) and cancels the visit.

## 6. Money and accounting

- Adding a bill line posts revenue to the department's own account through `finance.posting.post_visit_service`. Medicine goes to **Revenue: Pharmacy**, lab work to Revenue: Lab, and so on. So *billed* revenue is already split by department.
- Payments are recorded against the **visit as a whole** (`Payment.visit`). To report money collected per department, each payment is split across the visit's charges in proportion (section 9).
- A visit can be part-paid. Dispensing is allowed either way.

## 7. Stock reports and warnings

- An item is low on stock when `current_quantity <= reorder_level`. Low items show on the hospital dashboard and inventory pages.
- Batches carry expiry dates. The inventory insights page lists expired and soon-to-expire batches.

## 8. Gaps the Pharmacy module closed

Before the Pharmacy module:

1. There was no pharmacy queue, and no way to give pharmacy staff their own screens.
2. Medicine could only be added by dose, times per day and days, never as a straight quantity.
3. Nothing recorded how much of the bill was paid when medicine was handed over.
4. Two people clicking Dispense at the same moment could both take stock off.
5. Money collected wasn't split by department.

Section 9 describes how each was solved.

## 9. The Pharmacy module

Code lives in the `pharmacy` app. The screens say **Pharmacy**; under the hood it's still the `inventory` module, so `user.can_access_inventory` decides who sees it. A user gets it by being a hospital admin, or by being in the **Inventory** module group on a facility that has the module.

### One dispensing step

`pharmacy.services.dispense(prescription, actor, batch_id=None)` is the only place stock leaves for a patient. Nurse, reception and pharmacy screens all call it. It:

1. Locks the prescription and the drug rows (`select_for_update`), so a double click takes stock off once.
2. Refuses a cancelled visit, and refuses if there isn't enough stock.
3. Takes stock from the batch that expires first, or from the batch the user picked.
4. Records the stock movement, with the batches used.
5. Marks the prescription dispensed and its bill line performed.
6. Saves `paid_at_dispense` and `bill_at_dispense`: how much of the bill had been paid at that moment. Medicine can go out on a part payment; this is the record of it.

It returns `(prescription, dispensed_now)`. A second call for the same prescription returns `dispensed_now = False` and changes nothing.

### Each facility chooses who gives out medicine

`Hospital.dispensing_mode`, set by a hospital admin on the Pharmacy page:

- `standard` (default): nurses and reception dispense, as before.
- `pharmacy`: the pharmacy dispenses. Nurses and reception see **Waiting at pharmacy** instead of a Dispense button. IV, IV medicine and IM drugs (`nursing_managed`) stay with the nurses on the ward.

`Hospital.pharmacy_mode_since` records when pharmacy mode was switched on.

### The pharmacy queue

There's no separate queue table. The queue is worked out from the prescriptions: every visit with medicine that is not dispensed, not `nursing_managed`, and not on a cancelled visit. That way medicine added by a doctor, by reception at billing, or at the pharmacy all appears automatically.

To keep old undispensed history out, only recent prescriptions count:

- In pharmacy mode: since `pharmacy_mode_since`, and at most 30 days back.
- In standard mode: the last two days.

### Selling by quantity

`pharmacy.services.add_by_quantity(visit, drug, quantity, counted_in, actor)` sells a drug straight, without a dose:

- The quantity can be counted in single units (tablets, ml) or whole packs (strips, bottles, tubes).
- Tablets and capsules are stored as single units, so packs are multiplied by `units_per_pack`.
- Liquids and tubes are sold in whole packs, so ml are divided by the pack size and rounded **up**.
- The result goes in `Prescription.sold_quantity`. `calculate_totals()` uses it instead of the dose formula.
- Such a sale is never `nursing_managed`, and shows as "Sold by quantity".
- It's billed exactly like a prescription: one Pharmacy bill line at the drug's selling price.

`Prescription.added_from` records where each line came from: `doctor`, `reception` or `pharmacy`.

### Walk-in sales

The pharmacy can start a sale for a walk-in customer or an existing patient. It creates a visit that is ready for billing, the pharmacist adds medicine by quantity and hands it over, and reception takes the payment. Reception's own Dispense desk still works; in pharmacy mode the medicine it adds goes to the pharmacy queue.

### Prices

Only hospital admins set selling prices. For other Pharmacy users the price field is locked, and bulk upload and deleting drugs are admin-only. Pharmacy users can add drugs and receive stock (with the buying cost).

### Revenue by department

`admin_dashboard.revenue.by_department(hospital, start, end)` is shared by the Finance, Doctor and Patient demographic reports, so they agree for the same dates:

- **Billed:** charges added in the period, by the department of each charge.
- **Collected:** receipts in the period. A payment is made against the whole visit, so each one is split across that visit's charges **in proportion to their value**. Someone who pays half the bill pays half of each department's share. The last department takes any rounding, so the shares always add up to the payment exactly.
- A receipt is a payment with `paid_at` set and `amount_paid > 0` that isn't waived. `revenue.receipts()` is the one definition, and the Finance report uses it too.

## Rebuilding this elsewhere

Keep these rules and the design carries over:

- Store each drug in a stocked unit and a counted unit, with the conversion between them.
- Work out quantity and price from the prescription, always rounding up, and keep them on the prescription so later price changes don't alter old bills.
- One bill line per billed prescription, so the drug can be dispensed, refunded and reported on its own.
- Never change stock without a movement record saying who, why and which batch.
- Use the batch that expires first, and record the batches used so an undo can return them exactly.
- Lock the prescription while dispensing.
