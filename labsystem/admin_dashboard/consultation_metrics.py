"""Doctor performance for the Consultations report: workload, time from the
doctor's queue to the consultation, what each doctor recommended, and what
their consulted visits were worth."""
from decimal import Decimal


def consultation_metrics(consultations):
    from reception.models import Payment, QueueEntry, Service

    rows = consultations.select_related("created_by", "visit").prefetch_related(
        "visit__visit_services__service", "visit__payments", "visit__queue_entries",
    )
    doctors, services = {}, {}
    totals = {"billed": Decimal("0"), "collected": Decimal("0"), "recommended_value": Decimal("0"),
              "lab_consults": 0, "waits": [], "count": 0}

    for c in rows:
        visit = c.visit
        doctor_queue = [q.created_at for q in visit.queue_entries.all() if q.queue_type == QueueEntry.TYPE_DOCTOR]
        arrived = min(doctor_queue) if doctor_queue else None
        wait = int((c.created_at - arrived).total_seconds() // 60) if arrived and c.created_at >= arrived else None

        # Recommended = anything billed after the patient reached the doctor
        # (or explicitly requested by them), apart from the consultation fee
        # reception charged up front and package-covered items.
        recommended = [
            vs for vs in visit.visit_services.all()
            if vs.service.category != Service.CATEGORY_CONSULTATION and not vs.covered_by_package
            and ((arrived and vs.created_at >= arrived) or (c.created_by_id and vs.requested_by_user_id == c.created_by_id))
        ]
        rec_value = sum((vs.price_at_time for vs in recommended), Decimal("0"))
        billed = visit.total_amount or Decimal("0")
        collected = sum((p.amount_paid for p in visit.payments.all() if p.status != Payment.STATUS_WAIVED), Decimal("0"))
        ordered_lab = any(vs.service.category == Service.CATEGORY_LAB for vs in recommended)

        name = (c.created_by.get_full_name() or c.created_by.username) if c.created_by else "Unknown"
        d = doctors.setdefault(name, {"name": name, "consultations": 0, "patients": set(), "waits": [],
                                      "lab_consults": 0, "recommended_count": 0,
                                      "recommended_value": Decimal("0"), "billed": Decimal("0"), "collected": Decimal("0")})
        d["consultations"] += 1
        d["patients"].add(visit.patient_id)
        d["lab_consults"] += ordered_lab
        d["recommended_count"] += len(recommended)
        d["recommended_value"] += rec_value
        d["billed"] += billed
        d["collected"] += collected
        if wait is not None:
            d["waits"].append(wait)
            totals["waits"].append(wait)

        totals["count"] += 1
        totals["billed"] += billed
        totals["collected"] += collected
        totals["recommended_value"] += rec_value
        totals["lab_consults"] += ordered_lab
        for vs in recommended:
            s = services.setdefault(vs.service_id, {"name": vs.service.name, "category": vs.service.get_category_display(),
                                                    "count": 0, "value": Decimal("0")})
            s["count"] += 1
            s["value"] += vs.price_at_time

    def avg(values):
        return round(sum(values) / len(values)) if values else None

    doctor_rows = []
    for d in doctors.values():
        d["patients"] = len(d.pop("patients"))
        d["avg_wait"] = avg(d.pop("waits"))
        d["lab_rate"] = round(100 * d["lab_consults"] / d["consultations"])
        d["per_consultation"] = d["billed"] / d["consultations"]
        doctor_rows.append(d)
    doctor_rows.sort(key=lambda r: r["billed"], reverse=True)

    count = totals["count"]
    summary = {
        "avg_wait": avg(totals["waits"]),
        "billed": totals["billed"],
        "collected": totals["collected"],
        "outstanding": totals["billed"] - totals["collected"],
        "recommended_value": totals["recommended_value"],
        "lab_rate": round(100 * totals["lab_consults"] / count) if count else 0,
        "per_consultation": totals["billed"] / count if count else Decimal("0"),
    }
    top_services = sorted(services.values(), key=lambda s: s["value"], reverse=True)[:10]
    return summary, doctor_rows, top_services
