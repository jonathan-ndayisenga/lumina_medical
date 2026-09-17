"""
Two jobs, kept out of views so they're testable on their own:

  hydrate_entry_form(order) -> dict the Enter Results screen renders from,
      resolving each parameter's range against THIS patient's sex + age.
  save_results(order, user, payload) -> persists values and FREEZES the
      resolved range + flag, so the record never changes when a definition
      is edited later.

Named services_next.py, not services.py, purely to avoid ever colliding with
a same-named module the prior engine might add — the "_next" here just means
"the newer of the two engines", not a separate app anymore.
"""

from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .models import (
    CultureAntibiotic,
    DefinedOption,
    Flag,
    LabResult,
    LabTest,
    OrderStage,
    Parameter,
    ParameterRange,
    ResultType,
    ResultValue,
    ValueType,
)


def clone_lab_test(source, hospital):
    """Deep-copy a LabTest — parameters, ranges, defined options, culture
    antibiotics — into `hospital`'s own catalog. One place that knows what a
    full clone actually needs to copy, shared by the catalog's 'Clone a
    Test' picker and the legacy-service auto-linking command."""
    clone = LabTest.objects.create(
        hospital=hospital,
        name=source.name,
        code=source.code,
        category=source.category,
        description=source.description,
        active_for_ordering=source.active_for_ordering,
        result_type=source.result_type,
    )
    clone.accepted_specimens.set(source.accepted_specimens.all())

    for opt in source.options.all():
        DefinedOption.objects.create(
            test=clone, label=opt.label, sort_order=opt.sort_order, is_abnormal=opt.is_abnormal,
        )
    for p in source.parameters.all():
        p_clone = Parameter.objects.create(
            test=clone, name=p.name, unit=p.unit, value_type=p.value_type,
            group_label=p.group_label, sort_order=p.sort_order,
        )
        for r in p.ranges.all():
            ParameterRange.objects.create(
                parameter=p_clone, sex=r.sex, age_min=r.age_min, age_max=r.age_max,
                ref_low=r.ref_low, ref_high=r.ref_high, ref_text=r.ref_text,
            )
    for a in source.antibiotics.all():
        CultureAntibiotic.objects.create(
            test=clone, drug_class=a.drug_class, name=a.name, sort_order=a.sort_order,
        )
    return clone


def hydrate_entry_form(order):
    """Return a plain dict describing the entry UI for one LabOrder."""
    test = order.test
    base = {
        "order_id": order.pk,
        "test_name": test.name,
        "result_type": test.result_type,
    }
    existing = getattr(order, "result", None)

    if test.result_type == ResultType.FREE_ENTRY:
        base["free_text"] = existing.free_text if existing else ""
        return base

    if test.result_type == ResultType.DEFINED_OPTION:
        base["options"] = [
            {"id": o.pk, "label": o.label, "is_abnormal": o.is_abnormal}
            for o in test.options.all()
        ]
        base["chosen_option"] = existing.chosen_option if existing else ""
        return base

    if test.result_type == ResultType.PARAMETER_PANEL:
        existing_values = {v.parameter_id: v for v in existing.values.all()} if existing else {}
        rows = []
        for p in test.parameters.all():
            rng = p.resolve_range(order.patient_sex, order.patient_age_years)
            prior = existing_values.get(p.pk)
            rows.append({
                "parameter_id": p.pk,
                "name": p.name,
                "group_label": p.group_label,
                "unit": p.unit,
                "value_type": p.value_type,
                "ref_display": rng.display() if rng else "",
                "value": (prior.value_num if prior and prior.value_num is not None else (prior.value_text if prior else "")),
                "flag": prior.get_flag_display() if prior and prior.flag else "",
                "flag_code": prior.flag if prior else "",
            })
        base["parameters"] = rows
        # Sectioned view for rendering — e.g. a culture-style panel grouped
        # by drug class. Parameters with no group_label (a plain CBC, or the
        # "Organism Isolated" line ahead of the antibiotic groups on a
        # culture panel) come back under a blank-key group with no header.
        groups = []
        groups_by_label = {}
        for row in rows:
            key = row["group_label"]
            if key not in groups_by_label:
                groups_by_label[key] = {"group_label": key, "parameters": []}
                groups.append(groups_by_label[key])
            groups_by_label[key]["parameters"].append(row)
        base["parameter_groups"] = groups
        base["bench_notes"] = existing.bench_notes if existing else ""
        return base

    if test.result_type == ResultType.CULTURE:
        existing_sirs = {v.antibiotic_id: v.value_code for v in existing.values.all()} if existing else {}
        base["organism"] = existing.organism if existing else ""
        grouped = {}
        for a in test.antibiotics.all():
            grouped.setdefault(a.drug_class or "Other", []).append({
                "antibiotic_id": a.pk, "name": a.name, "sir": existing_sirs.get(a.pk, ""),
            })
        base["antibiotic_groups"] = [{"class_name": k, "antibiotics": v} for k, v in grouped.items()]
        return base

    return base


def _get_or_create_result(order):
    result, _ = LabResult.objects.get_or_create(
        order=order, defaults={"result_type": order.test.result_type},
    )
    return result


def _to_decimal(raw):
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return None


def save_results(order, user, payload):
    """
    Persist entered results and freeze presentation.

    payload shape by result_type:
      FREE_ENTRY      {"free_text": "..."}
      DEFINED_OPTION  {"chosen_option": "Positive"}
      PARAMETER_PANEL {"values": [{"parameter_id": 1, "value": "13.2"}, ...],
                       "bench_notes": "..."}
      CULTURE         {"organism": "Staph aureus",
                       "sensitivities": [{"antibiotic_id": 5, "sir": "R"}, ...]}
    """
    test = order.test
    result = _get_or_create_result(order)
    result.result_type = test.result_type
    result.bench_notes = payload.get("bench_notes", "")
    result.entered_by = user
    result.entered_at = timezone.now()
    result.values.all().delete()

    if test.result_type == ResultType.FREE_ENTRY:
        result.free_text = payload.get("free_text", "")

    elif test.result_type == ResultType.DEFINED_OPTION:
        result.chosen_option = payload.get("chosen_option", "")

    elif test.result_type == ResultType.PARAMETER_PANEL:
        params = {p.pk: p for p in test.parameters.all()}
        for i, item in enumerate(payload.get("values", [])):
            p = params.get(item.get("parameter_id"))
            if p is None:
                continue
            raw = item.get("value", "")
            if not str(raw).strip():
                continue
            rng = p.resolve_range(order.patient_sex, order.patient_age_years)
            num = _to_decimal(raw)
            if p.value_type == ValueType.CODED:
                # Susceptibility codes (S/I/R) aren't numeric — flag
                # Resistant as abnormal instead of running them through the
                # numeric ref-range comparison.
                flag = Flag.ABNORMAL if str(raw).strip().upper() == "R" else ""
            elif p.value_type == ValueType.GROWTH:
                flag = Flag.ABNORMAL if str(raw).strip() == "Growth Observed" else ""
            elif p.value_type == ValueType.LEVEL:
                # Any dipstick reading above Negative is worth flagging —
                # Trace and 1+ through 4+ all count as abnormal.
                flag = "" if str(raw).strip() == "Negative" else Flag.ABNORMAL
            else:
                flag = rng.flag_for(num) if (rng and num is not None) else ""
            ResultValue.objects.create(
                result=result, parameter=p, label=p.name, group_label=p.group_label,
                value_num=num, value_text="" if num is not None else str(raw),
                unit=p.unit, ref_display=rng.display() if rng else "",
                flag=flag, sort_order=i,
            )

    elif test.result_type == ResultType.CULTURE:
        result.organism = payload.get("organism", "")
        antibiotics = {a.pk: a for a in test.antibiotics.all()}
        for i, item in enumerate(payload.get("sensitivities", [])):
            a = antibiotics.get(item.get("antibiotic_id"))
            if a is None or not item.get("sir"):
                continue
            ResultValue.objects.create(
                result=result, antibiotic=a, label=a.name, group_label=a.drug_class,
                value_code=item.get("sir", ""), sort_order=i,
            )

    result.save()
    order.stage = OrderStage.ENTERED
    order.save(update_fields=["stage"])
    return result
