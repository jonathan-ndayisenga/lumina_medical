"""What the Ternah Assistant can answer inside Ternah Health. Counts and totals
only, always for the signed-in person's own hospital; money needs finance access.
No patient names or clinical details are ever given out."""
from collections import Counter

from django.db.models import Count, Sum
from django.urls import NoReverseMatch, reverse

from labsystem.assistant import Assistant, Entity, Topic, answer, match_names, money

QUEUES = {
    "reception": ("reception", "Reception"), "doctor": ("doctor", "Doctor"), "doctors": ("doctor", "Doctor"),
    "consultation": ("doctor", "Doctor"), "nurse": ("nurse", "Nurse"), "nurses": ("nurse", "Nurse"),
    "triage": ("nurse", "Nurse"), "lab": ("lab", "Lab"), "laboratory": ("lab", "Lab"),
    "scan": ("sonographer", "Scan"), "sonographer": ("sonographer", "Scan"),
    "phlebotomy": ("phlebotomy", "Phlebotomy"), "blood": ("phlebotomy", "Phlebotomy"),
}


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


def _no_money():
    return answer("Money figures are only shown to admins and finance staff.")


def _visits(q, hospital):
    from reception.models import Visit
    return Visit.objects.filter(hospital=hospital, visit_date__date__range=(q.period.start, q.period.end))


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def visits(q, hospital, user):
    from reception.models import Patient
    qs = _visits(q, hospital)
    total = qs.count()
    new = Patient.objects.filter(hospital=hospital, created_at__date__range=(q.period.start, q.period.end)).count()
    facts = [("New patients registered", str(new)), ("Returning or follow-up", str(max(total - new, 0)))] if total else []
    link = _link("visit_summary_report", "Open visit summary", start=q.period.start.isoformat(), end=q.period.end.isoformat()) if user.is_hospital_admin else None
    return answer(f"{_plural(total, 'patient visit')} {q.period.phrase}.", facts, link, f"Patient visits · {q.period.label}",
                  suggestions=["How many new patients this month?", "Male and female patients this month"])


def new_patients(q, hospital, user):
    from reception.models import Patient
    n = Patient.objects.filter(hospital=hospital, created_at__date__range=(q.period.start, q.period.end)).count()
    return answer(f"{_plural(n, 'new patient')} registered {q.period.phrase}.", understood=f"New patients · {q.period.label}")


def collected(q, hospital, user):
    if not _can_see_money(user):
        return _no_money()
    from reception.models import Payment
    receipts = Payment.objects.filter(
        visit__hospital=hospital, paid_at__isnull=False, amount_paid__gt=0, paid_at__date__range=(q.period.start, q.period.end),
    ).exclude(status=Payment.STATUS_WAIVED)
    total = receipts.aggregate(t=Sum("amount_paid"))["t"] or 0
    modes = dict(Payment.MODE_CHOICES)
    facts = [(modes.get(row["mode"], row["mode"]), money(row["t"])) for row in receipts.values("mode").annotate(t=Sum("amount_paid")).order_by("-t")]
    return answer(f"Collected {q.period.phrase}: {money(total)}.", facts if len(facts) > 1 else [],
                  _link("financial_report", "Open financials"), f"Collected · {q.period.label}")


def spent(q, hospital, user):
    if not _can_see_money(user):
        return _no_money()
    from admin_dashboard.models import Expense
    qs = Expense.objects.filter(hospital=hospital, date__range=(q.period.start, q.period.end))
    total = qs.aggregate(t=Sum("amount"))["t"] or 0
    categories = dict(Expense.CATEGORY_CHOICES)
    facts = [(categories.get(row["category"], row["category"]), money(row["t"])) for row in qs.values("category").annotate(t=Sum("amount")).order_by("-t")]
    return answer(f"Expenses {q.period.phrase}: {money(total)}.", facts if len(facts) > 1 else [],
                  _link("financial_report", "Open financials"), f"Expenses · {q.period.label}")


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


def _service_count(q, hospital, category):
    from reception.models import VisitService
    return VisitService.objects.filter(
        visit__hospital=hospital, service__category=category, created_at__date__range=(q.period.start, q.period.end),
    )


def lab_tests(q, hospital, user):
    from reception.models import Service
    qs = _service_count(q, hospital, Service.CATEGORY_LAB)
    facts = [(row["service__name"], str(row["n"])) for row in qs.values("service__name").annotate(n=Count("id")).order_by("-n")[:5]]
    return answer(f"{_plural(qs.count(), 'lab test')} requested {q.period.phrase}." + (" Most requested:" if facts else ""), facts,
                  understood=f"Lab tests · {q.period.label}")


def scans(q, hospital, user):
    from reception.models import Service
    qs = _service_count(q, hospital, Service.CATEGORY_SCAN)
    facts = [(row["service__name"], str(row["n"])) for row in qs.values("service__name").annotate(n=Count("id")).order_by("-n")[:5]]
    return answer(f"{_plural(qs.count(), 'scan')} requested {q.period.phrase}.", facts, understood=f"Scans · {q.period.label}")


def top_services(q, hospital, user):
    from reception.models import VisitService
    rows = VisitService.objects.filter(visit__hospital=hospital, created_at__date__range=(q.period.start, q.period.end)) \
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


def pharmacy(q, hospital, user):
    from datetime import timedelta
    from django.db.models import F
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
    link = _link("pharmacy_queue", "Open the pharmacy") if getattr(user, "can_access_inventory", False) else None
    return answer(text, facts, link, f"Pharmacy · {q.period.label}")


def hello(q, hospital, user):
    return greeting(q.today, hospital, user)


TOPICS = [
    Topic("queue", {"queue": 3}, queue, default_period="today"),
    Topic("new_patients", {"new": 3}, new_patients, default_period="today"),
    Topic("collected", {"collected": 3, "revenue": 3, "payment": 2, "paid": 1, "cash": 2}, collected, default_period="today"),
    Topic("spent", {"spent": 3}, spent),
    Topic("lab_tests", {"test": 3, "lab": 1}, lab_tests, default_period="today"),
    Topic("scans", {"scan": 3}, scans, default_period="today"),
    Topic("pharmacy", {"pharmacy": 4, "drugs": 2, "drug": 2, "medicine": 2, "medicines": 2, "stock": 2, "expiring": 2, "expire": 2}, pharmacy, default_period="today"),
    Topic("top_services", {"services": 3, "service": 3}, top_services),
    Topic("sex", {"sex": 3, "male": 2, "female": 2}, sex_split),
    Topic("visits", {"visits": 3, "visit": 3, "patients": 2, "seen": 2, "clients": 2, "attended": 2}, visits, default_period="today"),
    Topic("hello", {"hello": 2, "help": 3, "thanks": 2, "thank": 2}, hello),
]


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


def greeting(today, hospital, user):
    from reception.models import Visit
    from reception.workflow import queue_counts_for_hospital
    seen = Visit.objects.filter(hospital=hospital, visit_date__date=today).count()
    waiting = sum(queue_counts_for_hospital(hospital).values())
    facts = [("Patient visits today", str(seen)), ("Waiting in queues now", str(waiting))]
    return answer("Hello. Here's today so far. Ask me about patients, queues, tests or money.", facts, suggestions=EXAMPLES[:4])


EXAMPLES = [
    "How many patients today?",
    "Who is waiting in the lab queue?",
    "How much did we collect today?",
    "New patients this month",
    "How many lab tests this week?",
    "Top services this month",
    "Male and female patients this month",
    "Expenses this month",
    "Pharmacy sales today",
]

assistant = Assistant(TOPICS, find_entities, EXAMPLES, greeting, restore_entities)
