# What's New — Quick Guide

Short how-to for the features added in this release. Each section says who uses it and the steps.

---

## Ternah Books

### Quotations
**Who:** Ternah staff preparing price quotes for clients.

1. **Books → Quotations → New quotation.**
2. Pick the **client** and type a short **quotation code** (e.g. `SACCO`). The number is created for you: `TSC-SACCO-2026-001`, then `-002`, and so on, counted separately for each code.
3. Fill in the title (e.g. *SACCO Management System*), the opening paragraph, the optional green highlight box (e.g. *3 months of FREE hosting*) and the two note columns.
4. In **Number of items**, type how many lines the quote has. That many item rows appear. For each, enter the description, a detail line, the basis (e.g. *One-time*, *3 days × 150,000*) and the amount. The subtotal updates as you type.
5. **Save quotation.** The quote's page shows a preview. Use **Print / PDF** to send it.
6. As the client responds, press **Mark sent**, **Mark accepted** or **Mark declined**.
7. When the client accepts, press **Convert to invoice**, pick the income account, and review the draft invoice before issuing it.

**Settings → Quotations:**
- **Extra fields** (e.g. *Project duration*) appear on every quote's form and in the printed details box.
- **Defaults:** quote prefix, how many days a quote is valid, opening paragraph, note columns and footer line.

Tip: fill in **Settings → Letterhead** (trading name, legal name, TIN, Reg. No, phone, email, logo) first. The quote header uses them.

### Correcting an expense
**Who:** whoever records expenses.

1. **Books → Expenses**, then click **Edit** next to the expense.
2. Correct the figure (or date, account, payment method…) and press **Save correction**.

Books reverses the original journal entry and posts the corrected one, so reports show only the right figure and the **Journal** keeps a record of the correction. Void expenses, and expenses in a closed financial year, can't be edited.

### Dashboard: trend, cash, pipeline, clients, budgets
Open **Books → Dashboard**:
- **Last 12 months:** a line graph of money **invoiced**, actually **collected**, and **spent** each month. Hover a month for exact figures.
- **Cash position:** today's balance in each payment account (bank, MoMo, cash), and how many months of normal spending it covers (**runway**). Accounts appear here if *Is payment account* is ticked on them in the chart of accounts.
- **Quotation pipeline:** the value of quotes sent and still waiting on clients, and your **win rate** (accepted ÷ decided) over the last 90 days.
- **Top clients:** who brought in the most this financial year, and their share of revenue.
- **Budgets this month:** spending so far against each budget. Anything over budget shows in red.

### Monthly (recurring) invoices
**Who:** whoever bills hosting / subscriptions.

1. **Books → Recurring**, then fill in **New monthly invoice**: client, description (e.g. *Hosting & maintenance*), monthly amount, income account, and the **first invoice date** (e.g. the day the free hosting ends). Add a last date if the plan has an end.
2. On each due date, the plan shows **Due**, and the dashboard shows a banner. Press **Issue due invoices**. Books issues them, catching up any missed months, and moves each plan to next month.
3. **Pause** stops a plan (e.g. a client on hold); **Resume** restarts it.

For your technical team: running `python manage.py generate_recurring_invoices` once a day (e.g. as a scheduled job) issues due invoices automatically.

### WhatsApp payment reminders
On the **Dashboard → Overdue invoices** list, or on any unpaid invoice, click **Remind on WhatsApp**. WhatsApp opens with a polite message already filled in: client name, invoice number, balance, due date, and your bank / MoMo details from Letterhead settings. Check it and tap send; nothing is sent automatically. The client needs a phone number on their record.

### Budgets
**Books → Settings → Budgets**: enter a monthly amount for each expense account you want to watch (leave blank for none), then **Save budgets**. Results appear on the dashboard.

---

## Hospital app

### Ophthalmology (eye) consultations
**Who:** hospital admin (set-up), reception, doctors.

**Set-up (admin, once):** in **Hospital Management → Services**, create or edit a **Consultation** service (e.g. *Ophthalmology Consultation*) and set **Specialty** to *Ophthalmology (eye)*.

**Reception:** bill that service as usual. The patient goes to the doctor's queue.

**Doctor:**
1. In the **Doctor Queue**, eye patients show an **Eye** badge. Open the visit.
2. **Base Refraction** opens first: visual acuity, neuro/psych, pupils (tick **PERRL** if normal), refraction (autorefractor, retinoscope, subjective), keratometry, add and PD. Press **Save and continue**, or **Skip to main exam** if refraction isn't possible.
3. The **Eye Examination** replaces the usual notes:
   - external exam per eye;
   - all 13 **slit-lamp** sections (tick the findings for each eye; type anything else in **Other finding**);
   - CDR and IOP;
   - **+ Add diagnosis** (as many as needed);
   - history comments and management plan.
4. Labs, procedures, prescriptions, vitals and the hand-off buttons work exactly as in a normal consultation.
5. Choose the **Visit outcome** (below) and save. The saved consultation shows a summary of the eye exam.

### Visit outcome (all consultations)
Every consultation now records how the visit ended: **Ongoing** (default), **Discharged**, **Referred**, **Admitted**, **Sent to Theatre**, **Died**, or **Review Appointment**.
- **Referred / Admitted / Sent to Theatre:** type where to (e.g. *Mulago Eye Unit*).
- **Review Appointment:** set the review date. Reception books the follow-up visit when the patient comes back.

### Home screen: Laboratory "results pending"
The Laboratory tile now counts exactly the patients you can see in the **Lab Queue**, so the two always agree.

### Terminating a visit (admins)
Terminating a visit now also clears any lab tests that were never done for it, so they no longer linger as "pending". Tests that already have results are kept. The audit log records how many were cleared.
