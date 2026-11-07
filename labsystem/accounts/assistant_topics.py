"""What the Ternah Assistant can answer inside Ternah Health. Counts and totals
only, always for the signed-in person's own hospital, and only for the parts of
the work the person's role covers (see allowed_areas): a doctor asking about
money is turned away, not answered. No patient names or clinical details about
a person are ever given out; diagnoses only appear as counts across patients.

Questions with "I", "my" or "me" are answered for that person's own work:
"how many patients did I see today", "doses I gave this week"."""
import re
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import NoReverseMatch, reverse

from labsystem.assistant import Assistant, Entity, Topic, answer, money

QUEUES = {
    "reception": ("reception", "Reception"), "doctor": ("doctor", "Doctor"), "doctors": ("doctor", "Doctor"),
    "consultation": ("doctor", "Doctor"), "nurse": ("nurse", "Nurse"), "nurses": ("nurse", "Nurse"),
    "triage": ("nurse", "Nurse"), "lab": ("lab", "Lab"), "laboratory": ("lab", "Lab"),
    "scan": ("sonographer", "Scan"), "sonographer": ("sonographer", "Scan"),
    "phlebotomy": ("phlebotomy", "Phlebotomy"), "blood": ("phlebotomy", "Phlebotomy"),
}
ME_WORDS = ("i", "my", "me", "mine", "myself", "ive", "im")
ZERO = Decimal("0")


def _link(name, label, **query):
    try:
        url = reverse(name)
    except NoReverseMatch:
        return None
    if query:
        url += "?" + "&".join(f"{key}={value}" for key, value in query.items())
    return {"url": url, "label": label}


def _can_see_money(user) -> bool:
    return bool(getattr(user, "is_hospital_admin", False) or getattr(user, "is_owner", False) or getattr(user, "can_access_finance", False))


# ---------------------------------------------------------------- who may ask what

AREA_LABELS = {
    "money": "money and finance", "billing": "bills and payments", "pharmacy": "the pharmacy and stock",
    "lab": "lab tests", "lab_bench": "lab bench work", "scans": "scans",
    "clinical": "consultations and diagnoses", "ward": "the ward and nursing care",
    "appointments": "follow-up appointments", "admin": "staff and administration",
}
# Open to everyone in the hospital; listed last so each role's own questions lead.
COMMON_AREAS = ("patients", "queue")


def allowed_areas(hospital, user):
    """What this person may ask about. Admins, owners and superadmins: everything.
    Everyone else: patient numbers and queues, plus the areas their role works in.
    Uses the same access checks as the menus, so a doctor given the Finance group
    can ask about money, and a role without the module can't."""
    if user.is_superadmin or user.is_hospital_admin or user.is_owner:
        return None
    areas = set(COMMON_AREAS)
    clinical = user.can_access_doctor or user.can_access_nurse
    finance = _can_see_money(user)
    reception = user.can_access_reception
    if finance:
        areas.add("money")
    if finance or reception:
        areas.add("billing")
    if clinical:
        areas |= {"clinical", "ward"}
    if clinical or reception:
        areas.add("appointments")
    if clinical or finance or user.can_access_inventory:
        areas.add("pharmacy")
    if clinical or user.can_access_lab or reception:
        areas.add("lab")
    if user.can_access_lab:
        areas.add("lab_bench")
    if clinical or user.can_access_sonographer or reception:
        areas.add("scans")
    return areas


def _mine(q) -> bool:
    return q.has(*ME_WORDS)


def _plural(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _in(q, field):
    """A filter for `field` (a datetime) falling inside the question's period."""
    return {f"{field}__date__range": (q.period.start, q.period.end)}


def _top(values, limit=6):
    """Most common free-text values, grouped regardless of case and spacing."""
    counts, shown = Counter(), {}
    for value in values:
        text = re.sub(r"\s+", " ", (value or "").strip().splitlines()[0] if (value or "").strip() else "").strip(" .,;")[:60]
        if not text:
            continue
        key = text.casefold()
        counts[key] += 1
        shown.setdefault(key, text[:1].upper() + text[1:])
    return [(shown[key], str(n)) for key, n in counts.most_common(limit)]


def _duration(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} min"
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


# ---------------------------------------------------------------- patients and queues (everyone)

def _visits(q, hospital):
    from reception.models import Visit
    return Visit.objects.filter(hospital=hospital, **_in(q, "visit_date"))


def visits(q, hospital, user):
    from reception.models import Patient
    qs = _visits(q, hospital)
    if _mine(q):
        n = qs.filter(created_by=user).count()
        return answer(f"You opened {_plural(n, 'visit')} {q.period.phrase}.", understood=f"Visits you opened · {q.period.label}")
    total = qs.count()
    new = Patient.objects.filter(hospital=hospital, **_in(q, "created_at")).count()
    facts = [("New patients registered", str(new)), ("Returning or follow-up", str(max(total - new, 0)))] if total else []
    link = _link("visit_summary_report", "Open visit summary", start=q.period.start.isoformat(), end=q.period.end.isoformat()) if user.is_hospital_admin else None
    return answer(f"{_plural(total, 'patient visit')} {q.period.phrase}.", facts, link, f"Patient visits · {q.period.label}",
                  suggestions=["How many new patients this month?", "Male and female patients this month"])


def new_patients(q, hospital, user):
    from reception.models import Patient
    n = Patient.objects.filter(hospital=hospital, **_in(q, "created_at")).count()
    return answer(f"{_plural(n, 'new patient')} registered {q.period.phrase}.", understood=f"New patients · {q.period.label}")


def queue(q, hospital, user):
    from reception.workflow import queue_counts_for_hospital
    counts = queue_counts_for_hospital(hospital)
    chosen = q.entities.get("queue")
    if chosen:
        n = counts.get(chosen.pk, 0)
        return answer(f"{_plural(n, 'patient')} waiting in the {chosen.name} queue right now.", understood=f"{chosen.name} queue · now")
    labels = {key: label for key, label in QUEUES.values()}
    facts = [(labels.get(key, key.title()), str(n)) for key, n in counts.items() if n]
    total = sum(counts.values())
    return answer(f"{_plural(total, 'patient')} waiting across all queues right now." if total else "Nobody is waiting in any queue.",
                  facts, understood="Queues · now")


def top_services(q, hospital, user):
    from reception.models import VisitService
    rows = VisitService.objects.filter(visit__hospital=hospital, **_in(q, "created_at")) \
        .values("service__name").annotate(n=Count("id")).order_by("-n")[:6]
    facts = [(row["service__name"], str(row["n"])) for row in rows]
    return answer(f"Most used services {q.period.phrase}:" if facts else f"No services recorded {q.period.phrase}.", facts,
                  understood=f"Top services · {q.period.label}")


def sex_split(q, hospital, user):
    counts = Counter(_visits(q, hospital).values_list("patient__sex", flat=True))
    names = {"M": "Male", "F": "Female", "male": "Male", "female": "Female"}
    facts = [(names.get(sex, sex or "Not recorded"), str(n)) for sex, n in counts.most_common()]
    link = _link("patient_demographic_report", "Open demographics") if user.is_hospital_admin else None
    return answer(f"Patient visits {q.period.phrase} by sex:" if facts else f"No visits {q.period.phrase}.", facts, link,
                  f"Visits by sex · {q.period.label}")


# ---------------------------------------------------------------- doctors

def consultations(q, hospital, user):
    from doctor.models import Consultation, VisitOutcome
    qs = Consultation.objects.filter(visit__hospital=hospital, **_in(q, "created_at"))
    if _mine(q):
        mine = qs.filter(created_by=user)
        outcomes = dict(VisitOutcome.choices)
        facts = [(outcomes.get(row["outcome"], row["outcome"]), str(row["n"])) for row in mine.values("outcome").annotate(n=Count("id")).order_by("-n")]
        return answer(f"You saw {_plural(mine.count(), 'patient')} {q.period.phrase}." + (" Outcomes:" if facts else ""), facts,
                      understood=f"Your consultations · {q.period.label}")
    rows = qs.values("created_by__first_name", "created_by__last_name", "created_by__username").annotate(n=Count("id")).order_by("-n")[:6]
    facts = [((f"{r['created_by__first_name']} {r['created_by__last_name']}".strip() or r["created_by__username"] or "Not recorded"), str(r["n"])) for r in rows]
    return answer(f"{_plural(qs.count(), 'consultation')} {q.period.phrase}." + (" By doctor:" if len(facts) > 1 else ""),
                  facts if len(facts) > 1 else [], understood=f"Consultations · {q.period.label}",
                  suggestions=["How many patients did I see today?", "Most common diagnoses this month"])


def diagnoses(q, hospital, user):
    from doctor.models import Consultation
    qs = Consultation.objects.filter(visit__hospital=hospital, **_in(q, "created_at"))
    if _mine(q):
        qs = qs.filter(created_by=user)
    facts = _top(qs.values_list("diagnosis", flat=True))
    who = "your " if _mine(q) else ""
    return answer(f"Most common diagnoses in {who}consultations {q.period.phrase}:" if facts else f"No diagnoses recorded {q.period.phrase}.",
                  facts, understood=f"Diagnoses · {q.period.label}")


def outcomes(q, hospital, user):
    from doctor.models import Consultation, VisitOutcome
    qs = Consultation.objects.filter(visit__hospital=hospital, **_in(q, "created_at"))
    if _mine(q):
        qs = qs.filter(created_by=user)
    labels = dict(VisitOutcome.choices)
    facts = [(labels.get(row["outcome"], row["outcome"]), str(row["n"])) for row in qs.values("outcome").annotate(n=Count("id")).order_by("-n")]
    return answer(f"How consultations ended {q.period.phrase}:" if facts else f"No consultations {q.period.phrase}.", facts,
                  understood=f"Outcomes · {q.period.label}")


def follow_ups(q, hospital, user):
    from doctor.models import Consultation
    # Appointments lie ahead: "this week" and "this month" run to their end, not to today.
    end = q.period.end
    if q.period.label == "this week":
        end = q.period.start + timedelta(days=6)
    elif q.period.label == "this month":
        end = (q.period.start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    qs = Consultation.objects.filter(visit__hospital=hospital, follow_up_date__range=(q.period.start, end))
    if _mine(q):
        qs = qs.filter(created_by=user)
    n = qs.count()
    days = [(f"{row['follow_up_date']:%a %d %b}", str(row["n"])) for row in qs.values("follow_up_date").annotate(n=Count("id")).order_by("follow_up_date")[:7]]
    return answer(f"{_plural(n, 'follow-up appointment')} due {q.period.phrase}.", days if len(days) > 1 else [],
                  understood=f"Follow-ups due · {q.period.label}")


def prescriptions(q, hospital, user):
    from doctor.models import Prescription
    qs = Prescription.objects.filter(visit__hospital=hospital, **_in(q, "prescribed_at"))
    if _mine(q):
        qs = qs.filter(prescribed_by=user)
    total = qs.count()
    waiting = qs.filter(dispensed=False).count()
    facts = [("Not yet dispensed", str(waiting))] if total else []
    facts += [(row["drug__name"], str(row["n"])) for row in qs.values("drug__name").annotate(n=Count("id")).order_by("-n")[:5]]
    who = "You wrote" if _mine(q) else "Written"
    return answer(f"{who} {_plural(total, 'prescription')} {q.period.phrase}." + (" Most prescribed:" if total else ""), facts,
                  understood=f"Prescriptions · {q.period.label}")


# ---------------------------------------------------------------- nurses and the ward

def admissions(q, hospital, user):
    from nurse.models import NursingAdmission
    qs = NursingAdmission.objects.filter(hospital=hospital)
    now = qs.filter(status=NursingAdmission.STATUS_ACTIVE).count()
    facts = [
        (f"Admitted {q.period.phrase}", str(qs.filter(**_in(q, "admitted_at")).count())),
        (f"Discharged {q.period.phrase}", str(qs.filter(**_in(q, "discharged_at")).count())),
    ]
    return answer(f"{_plural(now, 'patient')} admitted right now.", facts, understood=f"Admissions · {q.period.label}")


def triage(q, hospital, user):
    from reception.models import Triage
    from reception.workflow import queue_counts_for_hospital
    qs = Triage.objects.filter(visit__hospital=hospital, **_in(q, "recorded_at"))
    if _mine(q):
        return answer(f"You recorded vitals for {_plural(qs.filter(recorded_by=user).count(), 'patient')} {q.period.phrase}.",
                      understood=f"Your triage · {q.period.label}")
    waiting = queue_counts_for_hospital(hospital).get("nurse", 0)
    return answer(f"Vitals recorded for {_plural(qs.count(), 'patient')} {q.period.phrase}.", [("Waiting for the nurse now", str(waiting))],
                  understood=f"Triage · {q.period.label}")


def doses(q, hospital, user):
    from nurse.models import NursingDose
    qs = NursingDose.objects.filter(care_item__admission__hospital=hospital, **_in(q, "administered_at"))
    if _mine(q):
        return answer(f"You gave {_plural(qs.filter(administered_by=user).count(), 'dose')} {q.period.phrase}.", understood=f"Your doses · {q.period.label}")
    facts = [((f"{r['administered_by__first_name']} {r['administered_by__last_name']}".strip() or r["administered_by__username"] or "Not recorded"), str(r["n"]))
             for r in qs.values("administered_by__first_name", "administered_by__last_name", "administered_by__username").annotate(n=Count("id")).order_by("-n")[:6]]
    return answer(f"{_plural(qs.count(), 'dose')} given on the ward {q.period.phrase}." + (" By nurse:" if len(facts) > 1 else ""),
                  facts if len(facts) > 1 else [], understood=f"Doses given · {q.period.label}")


# ---------------------------------------------------------------- lab

def _service_count(q, hospital, category):
    from reception.models import VisitService
    return VisitService.objects.filter(visit__hospital=hospital, service__category=category, **_in(q, "created_at"))


def lab_tests(q, hospital, user):
    from reception.models import Service
    qs = _service_count(q, hospital, Service.CATEGORY_LAB)
    facts = [(row["service__name"], str(row["n"])) for row in qs.values("service__name").annotate(n=Count("id")).order_by("-n")[:5]]
    return answer(f"{_plural(qs.count(), 'lab test')} requested {q.period.phrase}." + (" Most requested:" if facts else ""), facts,
                  understood=f"Lab tests · {q.period.label}")


def lab_work(q, hospital, user):
    """Where every unfinished test is right now, and what was released in the period."""
    from lab.models import LabOrder, LabResult, OrderStage
    if _mine(q):
        mine = LabResult.objects.filter(order__hospital=hospital, entered_by=user, **_in(q, "entered_at")).count()
        released = LabResult.objects.filter(order__hospital=hospital, released_by=user, **_in(q, "released_at")).count()
        return answer(f"You entered results for {_plural(mine, 'test')} {q.period.phrase}.", [("Released by you", str(released))],
                      understood=f"Your lab work · {q.period.label}")
    open_orders = LabOrder.objects.filter(hospital=hospital).exclude(stage=OrderStage.RELEASED)
    stages = dict(open_orders.values_list("stage").annotate(n=Count("id")))
    labels = [
        (OrderStage.PENDING, "Waiting for a sample"), (OrderStage.SAMPLE_COLLECTED, "Sample collected, not started"),
        (OrderStage.IN_PROGRESS, "Being worked on"), (OrderStage.ENTERED, "Results entered, awaiting review"),
        (OrderStage.REVIEWED, "Reviewed, awaiting release"),
    ]
    facts = [(label, str(stages.get(stage, 0))) for stage, label in labels]
    facts.append(("Urgent and not yet released", str(open_orders.filter(priority_routine=False).count())))
    released = LabResult.objects.filter(order__hospital=hospital, **_in(q, "released_at")).count()
    facts.append((f"Released {q.period.phrase}", str(released)))
    return answer(f"{_plural(sum(stages.values()), 'lab test')} not yet released.", facts, understood=f"Lab work · now and {q.period.label}")


def turnaround(q, hospital, user):
    from lab.models import LabResult
    rows = LabResult.objects.filter(order__hospital=hospital, **_in(q, "released_at")).values_list("order__created_at", "released_at", "order__test__name")
    hours = [(released - ordered).total_seconds() / 3600 for ordered, released, _ in rows if ordered and released and released >= ordered]
    if not hours:
        return answer(f"No results were released {q.period.phrase}, so there's no turnaround to show.", understood=f"Turnaround · {q.period.label}")
    by_test = {}
    for ordered, released, name in rows:
        if ordered and released and released >= ordered:
            by_test.setdefault(name, []).append((released - ordered).total_seconds() / 3600)
    slowest = sorted(((name, sum(v) / len(v)) for name, v in by_test.items()), key=lambda row: -row[1])[:3]
    hours.sort()
    facts = [("Results released", str(len(hours))), ("Middle case", _duration(hours[len(hours) // 2])), ("Slowest", _duration(hours[-1]))]
    facts += [(f"Slowest test: {name}", _duration(h)) for name, h in slowest if len(by_test) > 1]
    return answer(f"From order to released result, {_duration(sum(hours) / len(hours))} on average {q.period.phrase}.", facts,
                  understood=f"Lab turnaround · {q.period.label}")


def consumables(q, hospital, user):
    from lab.models import LabConsumable
    low = LabConsumable.objects.filter(hospital=hospital, is_active=True, current_quantity__lte=F("minimum_quantity")).order_by("current_quantity")
    facts = [(item.name, f"{item.current_quantity:g} {item.unit}") for item in low[:8]]
    return answer(f"{_plural(low.count(), 'lab consumable')} at or below the minimum." if facts else "No lab consumables are running low.", facts,
                  understood="Lab consumables · now")


# ---------------------------------------------------------------- scans

def scans(q, hospital, user):
    from nurse.models import ScanReport
    from reception.models import Service
    qs = _service_count(q, hospital, Service.CATEGORY_SCAN)
    reports = ScanReport.objects.filter(visit__hospital=hospital, **_in(q, "created_at"))
    if _mine(q):
        reports = reports.filter(sonographer=user)
        return answer(f"You wrote {_plural(reports.count(), 'scan report')} {q.period.phrase}.",
                      [("Still in draft", str(reports.filter(status=ScanReport.STATUS_DRAFT).count()))], understood=f"Your scans · {q.period.label}")
    types = dict(ScanReport.SCAN_TYPE_CHOICES)
    facts = [("Reports finalised", str(reports.filter(status=ScanReport.STATUS_FINAL).count())),
             ("Reports still in draft", str(reports.filter(status=ScanReport.STATUS_DRAFT).count()))]
    facts += [(types.get(row["scan_type"], row["scan_type"]), str(row["n"])) for row in reports.values("scan_type").annotate(n=Count("id")).order_by("-n")[:5]]
    return answer(f"{_plural(qs.count(), 'scan')} requested {q.period.phrase}.", facts, understood=f"Scans · {q.period.label}")


# ---------------------------------------------------------------- pharmacy

def pharmacy(q, hospital, user):
    from admin_dashboard.models import InventoryBatch, InventoryItem
    from pharmacy.services import pharmacy_queue, uses_pharmacy
    low = InventoryItem.objects.filter(hospital=hospital, is_active=True, current_quantity__lte=F("reorder_level")).count()
    expiring = InventoryBatch.objects.filter(
        item__hospital=hospital, quantity__gt=0, expiry_date__isnull=False, expiry_date__lte=q.today + timedelta(days=60),
    ).count()
    facts = [("Drugs at or below their minimum stock", str(low)), ("Batches expiring within 60 days", str(expiring))]
    if uses_pharmacy(hospital):
        facts.insert(0, ("Patients waiting at the pharmacy", str(pharmacy_queue(hospital).count())))
    text = "Pharmacy right now:"
    if _can_see_money(user):
        from admin_dashboard.revenue import by_department
        revenue = by_department(hospital, q.period.start, q.period.end)
        text = f"Pharmacy collected {money(revenue['pharmacy_collected'])} {q.period.phrase} (billed {money(revenue['pharmacy_billed'])})."
    elif q.has("revenue", "cash", "payment", "paid", "collected"):
        text = "Pharmacy money isn't part of what I answer for your role. Here's the pharmacy right now:"
    link = _link("pharmacy_queue", "Open the pharmacy") if getattr(user, "can_access_inventory", False) else None
    return answer(text, facts, link, f"Pharmacy · {q.period.label}")


# ---------------------------------------------------------------- bills (reception counts; finance amounts)

def _owing(visits):
    from reception.models import Payment, Visit
    paid = Coalesce(Sum("payments__amount_paid", filter=~Q(payments__status=Payment.STATUS_WAIVED)), Value(ZERO), output_field=DecimalField())
    return visits.exclude(status=Visit.STATUS_CANCELLED).annotate(paid=paid).filter(total_amount__gt=F("paid"))


def unpaid(q, hospital, user):
    from reception.models import Visit
    period = _owing(_visits(q, hospital))
    rows = list(period.values_list("paid", "total_amount"))
    nothing = sum(1 for paid, _ in rows if paid <= 0)
    facts = [("Nothing paid yet", str(nothing)), ("Part paid", str(len(rows) - nothing))]
    text = f"{_plural(len(rows), 'visit')} {q.period.phrase} still {'has' if len(rows) == 1 else 'have'} a balance to pay."
    link = None
    if _can_see_money(user):
        facts.append((f"Owed on these visits", money(sum((total - paid for paid, total in rows), ZERO))))
        ledger = list(_owing(Visit.objects.filter(hospital=hospital, debt_cleared_at__isnull=True)).values_list("paid", "total_amount", "visit_date"))
        old = q.today - timedelta(days=30)
        facts += [
            ("All open debts", f"{len(ledger)} · {money(sum((total - paid for paid, total, _ in ledger), ZERO))}"),
            ("Of which over 30 days old", money(sum((total - paid for paid, total, when in ledger if when.date() < old), ZERO))),
        ]
        link = _link("finance_debtors", "Open debtor ledger")
    return answer(text, facts, link, f"Unpaid bills · {q.period.label}")


# ---------------------------------------------------------------- finance

def _receipts(q, hospital):
    from admin_dashboard.revenue import receipts
    return receipts(hospital).filter(**_in(q, "paid_at"))


def collected(q, hospital, user):
    from reception.models import Payment
    qs = _receipts(q, hospital)
    total = qs.aggregate(t=Sum("amount_paid"))["t"] or 0
    modes = dict(Payment.MODE_CHOICES)
    facts = [(modes.get(row["mode"], row["mode"]), money(row["t"])) for row in qs.values("mode").annotate(t=Sum("amount_paid")).order_by("-t")]
    return answer(f"Collected {q.period.phrase}: {money(total)}.", facts if len(facts) > 1 else [],
                  _link("financial_report", "Open financials"), f"Collected · {q.period.label}")


def departments(q, hospital, user):
    from admin_dashboard.revenue import by_department
    report = by_department(hospital, q.period.start, q.period.end)
    facts = [(row["label"], f"{money(row['collected'])} (billed {money(row['billed'])})") for row in report["rows"]]
    return answer(f"Collected {q.period.phrase} by department, {money(report['total_collected'])} in all:" if facts else f"Nothing billed or collected {q.period.phrase}.",
                  facts, _link("financial_report", "Open financials"), f"By department · {q.period.label}")


def spent(q, hospital, user):
    from admin_dashboard.models import Expense
    qs = Expense.objects.filter(hospital=hospital, date__range=(q.period.start, q.period.end))
    total = qs.aggregate(t=Sum("amount"))["t"] or 0
    categories = dict(Expense.CATEGORY_CHOICES)
    facts = [(categories.get(row["category"], row["category"]), money(row["t"])) for row in qs.values("category").annotate(t=Sum("amount")).order_by("-t")]
    return answer(f"Expenses {q.period.phrase}: {money(total)}.", facts if len(facts) > 1 else [],
                  _link("financial_report", "Open financials"), f"Expenses · {q.period.label}")


def salaries(q, hospital, user):
    from admin_dashboard.models import Salary
    paid = Salary.objects.filter(hospital=hospital, paid=True, paid_at__range=(q.period.start, q.period.end))
    unpaid_rows = Salary.objects.filter(hospital=hospital, paid=False, month__lte=q.period.end)
    facts = [
        ("Salary payments made", str(paid.count())),
        ("Unpaid salaries so far", f"{unpaid_rows.count()} · {money(unpaid_rows.aggregate(t=Sum('amount'))['t'])}"),
    ]
    return answer(f"Salaries paid {q.period.phrase}: {money(paid.aggregate(t=Sum('amount'))['t'])}.", facts,
                  _link("manage_salaries", "Open salaries"), f"Salaries · {q.period.label}")


def profit(q, hospital, user):
    """The same sum as the Financial Report: money received less expenses and salaries paid."""
    from admin_dashboard.models import Expense, Salary
    income = _receipts(q, hospital).aggregate(t=Sum("amount_paid"))["t"] or ZERO
    expenses = Expense.objects.filter(hospital=hospital, date__range=(q.period.start, q.period.end)).aggregate(t=Sum("amount"))["t"] or ZERO
    wages = Salary.objects.filter(hospital=hospital, paid=True, paid_at__range=(q.period.start, q.period.end)).aggregate(t=Sum("amount"))["t"] or ZERO
    net = income - expenses - wages
    word = "Profit" if net >= 0 else "Loss"
    return answer(f"{word} {q.period.phrase}: {money(abs(net))}.", [("Money received", money(income)), ("Expenses", money(expenses)), ("Salaries paid", money(wages))],
                  _link("financial_report", "Open financials"), f"Profit · {q.period.label}")


# ---------------------------------------------------------------- admin

def staff(q, hospital, user):
    from accounts.models import User
    rows = User.objects.filter(hospital=hospital, is_active=True).values("role").annotate(n=Count("id")).order_by("-n")
    roles = dict(User.ROLE_CHOICES)
    facts = [(roles.get(row["role"], row["role"]), str(row["n"])) for row in rows]
    return answer(f"{_plural(sum(row['n'] for row in rows), 'active staff account')}:", facts, _link("manage_users", "Manage users"), "Staff · now")


def hello(q, hospital, user):
    return greeting(q.today, hospital, user)


# Order breaks ties: the more specific topic is listed first.
TOPICS = [
    Topic("consultations", {"consultation": 3}, consultations, default_period="today", area="clinical"),
    Topic("diagnoses", {"diagnosis": 3}, diagnoses, area="clinical"),
    Topic("outcomes", {"outcome": 3, "referred": 3, "died": 3, "discharged": 1}, outcomes, area="clinical"),
    Topic("follow_ups", {"follow": 3, "appointment": 3}, follow_ups, default_period="week", area="appointments"),
    Topic("prescriptions", {"prescription": 3, "dispensed": 1}, prescriptions, default_period="today", area="clinical"),
    Topic("admissions", {"admitted": 3, "discharged": 2}, admissions, default_period="today", area="ward"),
    Topic("triage", {"triage": 3}, triage, default_period="today", area="ward"),
    Topic("doses", {"dose": 3, "given": 1}, doses, default_period="today", area="ward"),
    Topic("unpaid", {"owe": 3, "invoice": 2, "billing": 3, "balance": 2}, unpaid, default_period="today", area="billing"),
    Topic("turnaround", {"turnaround": 3, "long": 2, "take": 1, "fast": 2, "quickly": 2}, turnaround, default_period="week", area="lab"),
    Topic("lab_work", {"result": 3, "sample": 3, "release": 3, "released": 3, "awaiting": 1, "urgent": 2}, lab_work, default_period="today", area="lab"),
    Topic("consumables", {"consumable": 3}, consumables, area="lab_bench"),
    Topic("queue", {"queue": 3}, queue, default_period="today", area="queue"),
    Topic("new_patients", {"new": 3}, new_patients, default_period="today", area="patients"),
    Topic("departments", {"department": 3}, departments, area="money"),
    Topic("salaries", {"salary": 3}, salaries, area="money"),
    Topic("profit", {"profit": 3, "loss": 2, "net": 1}, profit, area="money"),
    Topic("collected", {"collected": 3, "revenue": 3, "payment": 2, "paid": 1, "cash": 2}, collected, default_period="today", area="money"),
    Topic("spent", {"spent": 3}, spent, area="money"),
    Topic("lab_tests", {"test": 3, "lab": 1}, lab_tests, default_period="today", area="lab"),
    Topic("scans", {"scan": 3, "report": 1}, scans, default_period="today", area="scans"),
    Topic("pharmacy", {"pharmacy": 4, "drugs": 2, "drug": 2, "medicine": 2, "medicines": 2, "stock": 2, "expiring": 2, "expire": 2}, pharmacy,
          default_period="today", area="pharmacy"),
    Topic("top_services", {"services": 3, "service": 3}, top_services, area="patients"),
    Topic("sex", {"sex": 3, "male": 2, "female": 2}, sex_split, area="patients"),
    Topic("visits", {"visits": 3, "visit": 3, "patients": 2, "seen": 2, "clients": 2, "attended": 2}, visits, default_period="today", area="patients"),
    Topic("staff", {"staff": 3}, staff, area="admin"),
    Topic("hello", {"hello": 2, "help": 3, "thanks": 2, "thank": 2}, hello),
]
LAB_WORDS = ("test", "lab", "result", "sample")


class HealthAssistant(Assistant):
    def _score(self, topic, question):
        score = super()._score(topic, question)
        # "How many patients did I see today?" is about the doctor's own consultations.
        if topic.name == "consultations" and question.has(*ME_WORDS) and question.has("seen", "patients"):
            score += 5
        # "Pending lab tests" is about where the tests are, not how many were ordered.
        if topic.name == "lab_work" and question.has("pending", "awaiting", "ready", "done", "urgent") and question.has(*LAB_WORDS):
            score += 5
        return score


def find_entities(text, tokens, hospital, user):
    for word in tokens:
        if word in QUEUES and "queue" in tokens:
            key, label = QUEUES[word]
            return {"queue": [Entity("queue", key, label)]}
    return {}


def restore_entities(saved, hospital, user):
    restored = {}
    for kind, (entity_kind, pk) in saved.items():
        if kind == "queue":
            label = next((label for key, label in QUEUES.values() if key == pk), pk)
            restored[kind] = Entity("queue", pk, label)
    return restored


GREETING_SUBJECTS = (
    ("clinical", "your patients"), ("ward", "the ward"), ("lab", "lab work"), ("scans", "scans"),
    ("pharmacy", "the pharmacy"), ("billing", "bills"), ("money", "money"),
)


def greeting(today, hospital, user):
    from reception.models import Visit
    from reception.workflow import queue_counts_for_hospital
    seen = Visit.objects.filter(hospital=hospital, visit_date__date=today).count()
    waiting = sum(queue_counts_for_hospital(hospital).values())
    facts = [("Patient visits today", str(seen)), ("Waiting in queues now", str(waiting))]
    areas = allowed_areas(hospital, user)
    subjects = [word for area, word in GREETING_SUBJECTS if areas is None or area in areas] + ["patient numbers", "queues"]
    return answer(f"Hello. Here's today so far. Ask me about {', '.join(subjects[:-1])} or {subjects[-1]}.", facts,
                  suggestions=assistant.examples_for(hospital=hospital, user=user)[:4])


# Each role sees its own first (common areas go last), so the greeting and the
# "here's what I can answer" lists fit the person asking.
EXAMPLES = [
    ("money", "How much did we collect today?"),
    ("money", "Profit this month"),
    ("clinical", "How many patients did I see today?"),
    ("ward", "How many patients are admitted?"),
    ("lab", "Lab tests pending"),
    ("scans", "How many scans this week?"),
    ("billing", "Unpaid bills today"),
    ("lab", "Lab turnaround this week"),
    ("clinical", "Most common diagnoses this month"),
    ("ward", "Triage done today"),
    ("money", "Collections by department this month"),
    ("money", "Expenses this month"),
    ("clinical", "Referrals and outcomes this month"),
    ("appointments", "Follow-ups due this week"),
    ("clinical", "Prescriptions written today"),
    ("ward", "Doses given today"),
    ("lab_bench", "Lab consumables running low"),
    ("lab", "How many lab tests this week?"),
    ("pharmacy", "Drugs running low"),
    ("money", "Salaries this month"),
    ("admin", "Staff by role"),
    ("patients", "How many patients today?"),
    ("queue", "Who is waiting in the doctor queue?"),
    ("patients", "New patients this month"),
    ("patients", "Top services this month"),
    ("patients", "Male and female patients this month"),
]

assistant = HealthAssistant(TOPICS, find_entities, EXAMPLES, greeting, restore_entities, allowed_areas, AREA_LABELS)
