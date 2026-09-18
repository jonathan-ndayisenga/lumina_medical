"""
Lab Management screens: ServiceCategory and SpecimenType (shared,
platform-wide taxonomy — see their models.py docstrings) plus LabTest (owned
per-hospital — a new hospital starts with zero tests and builds its own
templates; test_clone lets one be copied in as a starting point instead of
built from nothing) plus a read-only view of the fixed ResultType choices.

Viewing is open to anyone with lab access; creating/editing is gated to
hospital admins (and superadmins) via catalog_admin_required — lab
attendants get read-only access to every screen here.

Creating/editing a LabTest here also creates/updates a matching
`reception.Service` scoped to the current hospital and keeps it linked via
Service.lab_tests_next, instead of that being a separate manual step. A
service can be linked to more than one LabTest (see Manage Services) for a
bundled offering billed once but entered as separate independent tests.
"""

import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

CATALOG_PAGE_SIZE = 20

from reception.models import Service

from .forms_catalog import (
    DefinedOptionFormSet,
    LabTestForm,
    ParameterRowFormSet,
    ServiceCategoryForm,
    SpecimenTypeForm,
)
from .models import (
    DefinedOption,
    LabSettings,
    LabTest,
    Parameter,
    ParameterRange,
    ResultType,
    ServiceCategory,
    Sex,
    SpecimenType,
)
from .services_next import clone_lab_test
from .views import get_active_hospital, staff_required


RESULT_TYPE_NOTES = {
    ResultType.FREE_ENTRY: "Single free-text or numeric result entry.",
    ResultType.DEFINED_OPTION: "Select from predefined qualitative options.",
    ResultType.PARAMETER_PANEL: "Capture multiple analytes or parameters.",
    ResultType.CULTURE: "Culture-oriented workflow and narrative output.",
}

_GENDER_TO_SEX = {"any": Sex.ANY, "male": Sex.MALE, "female": Sex.FEMALE}
_SEX_TO_GENDER = {v: k for k, v in _GENDER_TO_SEX.items()}
_RANGE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(?:-|to|–|—)\s*(-?\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def _parse_ref_range(label):
    """'12.0 - 16.0' -> (Decimal('12.0'), Decimal('16.0'), ''); anything else
    (e.g. 'Negative', '<1:80') is kept verbatim as free text instead."""
    match = _RANGE_RE.match(label or "")
    if not match:
        return None, None, (label or "").strip()
    try:
        return Decimal(match.group(1)), Decimal(match.group(2)), ""
    except InvalidOperation:
        return None, None, (label or "").strip()


def _initial_defined_options(test):
    return [{"label": o.label} for o in test.options.all()] if test else []


def _initial_parameters(test):
    if not test:
        return []
    rows = []
    for param in test.parameters.select_related(None).prefetch_related("ranges"):
        ranges = list(param.ranges.all())
        if not ranges:
            rows.append({
                "name": param.name, "group_label": param.group_label, "unit": param.unit, "sex": "any",
                "value_type": param.value_type, "ref_range": "",
            })
            continue
        for r in ranges:
            rows.append({
                "name": param.name, "group_label": param.group_label, "unit": param.unit,
                "sex": _SEX_TO_GENDER.get(r.sex, "any"),
                "age_min": r.age_min, "age_max": r.age_max,
                "value_type": param.value_type,
                "ref_range": r.display(),
            })
    return rows


def _group_parameter_forms(formset):
    """Group a flat ParameterRowFormSet into blocks — one block per unique
    parameter name, its forms in submission order. The first form in each
    block is the "primary" row (shows Name/Unit/Value Type); the rest are
    range-only rows for that same parameter (Sex/Age/Reference Range only,
    keeping the same name via JS-synced hidden fields — see
    catalog_test_form.html). Groups by whatever the form's `name` field
    currently holds — submitted value if bound, initial value otherwise —
    so this works identically for a fresh GET, an edit's initial data, and
    a validation-error repaint. A blank name never merges with another
    blank name, so freshly added empty blocks stay separate.
    """
    blocks = []
    index_by_key = {}
    for form in formset.forms:
        raw_name = form["name"].value()
        name = (raw_name or "").strip().lower()
        if name and name in index_by_key:
            blocks[index_by_key[name]].append(form)
        else:
            if name:
                index_by_key[name] = len(blocks)
            blocks.append([form])
    return blocks


def _save_defined_options(test, formset):
    test.options.all().delete()
    order = 0
    for form in formset:
        cleaned = getattr(form, "cleaned_data", None) or {}
        if cleaned.get("DELETE"):
            continue
        label = (cleaned.get("label") or "").strip()
        if not label:
            continue
        DefinedOption.objects.create(test=test, label=label, sort_order=order)
        order += 1


def _save_parameters(test, formset):
    test.parameters.all().delete()  # cascades to ParameterRange; ResultValue.parameter SET_NULLs
    order = 0
    params_by_name = {}
    for form in formset:
        cleaned = getattr(form, "cleaned_data", None) or {}
        if cleaned.get("DELETE"):
            continue
        name = (cleaned.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        param = params_by_name.get(key)
        if param is None:
            param = Parameter.objects.create(
                test=test, name=name, unit=cleaned.get("unit") or "",
                group_label=(cleaned.get("group_label") or "").strip(),
                value_type=cleaned.get("value_type") or "numeric", sort_order=order,
            )
            params_by_name[key] = param
            order += 1
        ref_low, ref_high, ref_text = _parse_ref_range(cleaned.get("ref_range"))
        ParameterRange.objects.create(
            parameter=param,
            sex=_GENDER_TO_SEX.get(cleaned.get("sex"), Sex.ANY),
            age_min=cleaned.get("age_min"), age_max=cleaned.get("age_max"),
            ref_low=ref_low, ref_high=ref_high, ref_text=ref_text,
        )


def _catalog_admin_ok(user):
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    if getattr(user, "role", "") in {user.ROLE_SUPERADMIN, user.ROLE_HOSPITAL_ADMIN}:
        return True
    return bool(getattr(user, "can_access_hospital_admin", False))


catalog_admin_required = user_passes_test(_catalog_admin_ok)


# ---------------------------------------------------------------------------
# Service Categories
# ---------------------------------------------------------------------------

@login_required
@staff_required
def category_list(request):
    paginator = Paginator(ServiceCategory.objects.all(), CATALOG_PAGE_SIZE)
    return render(request, "lab/catalog_category_list.html", {
        "categories": paginator.get_page(request.GET.get("page")),
        "is_admin": _catalog_admin_ok(request.user),
        "active_nav": "lab_categories",
    })


@login_required
@catalog_admin_required
def category_create(request):
    if request.method == "POST":
        form = ServiceCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Service category added.")
            return redirect("lab_category_list")
    else:
        form = ServiceCategoryForm()
    return render(request, "lab/catalog_category_form.html", {
        "form": form, "active_nav": "lab_categories", "is_edit": False,
    })


@login_required
@catalog_admin_required
def category_edit(request, pk):
    category = get_object_or_404(ServiceCategory, pk=pk)
    if request.method == "POST":
        form = ServiceCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "Service category updated.")
            return redirect("lab_category_list")
    else:
        form = ServiceCategoryForm(instance=category)
    return render(request, "lab/catalog_category_form.html", {
        "form": form, "active_nav": "lab_categories", "is_edit": True, "category": category,
    })


# ---------------------------------------------------------------------------
# Specimen Types
# ---------------------------------------------------------------------------

@login_required
@staff_required
def specimen_list(request):
    paginator = Paginator(SpecimenType.objects.all(), CATALOG_PAGE_SIZE)
    return render(request, "lab/catalog_specimen_list.html", {
        "specimens": paginator.get_page(request.GET.get("page")),
        "is_admin": _catalog_admin_ok(request.user),
        "active_nav": "lab_specimens",
    })


@login_required
@catalog_admin_required
def specimen_create(request):
    if request.method == "POST":
        form = SpecimenTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Specimen type added.")
            return redirect("lab_specimen_list")
    else:
        form = SpecimenTypeForm()
    return render(request, "lab/catalog_specimen_form.html", {
        "form": form, "active_nav": "lab_specimens", "is_edit": False,
    })


@login_required
@catalog_admin_required
def specimen_edit(request, pk):
    specimen = get_object_or_404(SpecimenType, pk=pk)
    if specimen.is_default:
        messages.error(request, "Default specimen types are locked and can't be edited.")
        return redirect("lab_specimen_list")
    if request.method == "POST":
        form = SpecimenTypeForm(request.POST, instance=specimen)
        if form.is_valid():
            form.save()
            messages.success(request, "Specimen type updated.")
            return redirect("lab_specimen_list")
    else:
        form = SpecimenTypeForm(instance=specimen)
    return render(request, "lab/catalog_specimen_form.html", {
        "form": form, "active_nav": "lab_specimens", "is_edit": True, "specimen": specimen,
    })


@login_required
@catalog_admin_required
def specimen_delete(request, pk):
    specimen = get_object_or_404(SpecimenType, pk=pk)
    if specimen.is_default:
        messages.error(request, "Default specimen types are locked and can't be deleted.")
        return redirect("lab_specimen_list")
    if request.method == "POST":
        specimen.delete()
        messages.success(request, "Specimen type deleted.")
        return redirect("lab_specimen_list")
    return render(request, "lab/catalog_specimen_confirm_delete.html", {
        "specimen": specimen, "active_nav": "lab_specimens",
    })


# ---------------------------------------------------------------------------
# Result Types — fixed, code-defined (each drives its own entry UI and
# report rendering in services_next.py / report.html), so this is a
# read-only view, not a CRUD screen. Adding a row here wouldn't do anything.
# ---------------------------------------------------------------------------

@login_required
@staff_required
def result_type_list(request):
    # The retired CULTURE result_type isn't how Culture & Sensitivity panels
    # are actually built anymore (see LabTest docstring) — they're a
    # PARAMETER_PANEL, so the starter to point at is found by name, not type.
    culture_starter = LabTest.objects.filter(
        name="Urine Culture & Sensitivity", is_starter_template=True,
    ).first()
    rows = [
        {
            "name": label, "code": value, "description": RESULT_TYPE_NOTES.get(value, ""),
            "retired": value == ResultType.CULTURE,
            "starter": culture_starter if value == ResultType.CULTURE else None,
        }
        for value, label in ResultType.choices
    ]
    return render(request, "lab/catalog_result_type_list.html", {
        "rows": rows, "active_nav": "lab_result_types", "is_admin": _catalog_admin_ok(request.user),
    })


# ---------------------------------------------------------------------------
# Laboratory Services (LabTest) — the shared catalog; price is per-hospital.
# ---------------------------------------------------------------------------

def _sync_hospital_service(test, hospital, price):
    """Keep this hospital's billable Service in sync with the LabTest catalog
    entry. No price submitted -> this hospital doesn't offer it (the link is
    cleared, not deleted, so past orders keep their history). A price ->
    get_or_create/update the Service and link it via lab_tests_next.

    Only ever touches a service that's linked to THIS test alone -- a
    service someone has manually bundled with other tests (e.g. "Malaria
    Test" -> MRDT + B/S) is a deliberate grouping and must never get its
    name/price silently overwritten by editing just one of its tests here.

    Returns an error string on a name clash with an unrelated existing
    Service at this hospital, else None.
    """
    existing_link = (
        Service.objects.filter(hospital=hospital, lab_tests_next=test)
        .annotate(_test_count=Count("lab_tests_next"))
        .filter(_test_count=1)
        .first()
    )

    if price is None:
        if existing_link:
            existing_link.lab_tests_next.remove(test)
        return None

    if existing_link:
        existing_link.name = test.name
        existing_link.price = price
        existing_link.is_active = test.active_for_ordering
        existing_link.save(update_fields=["name", "price", "is_active"])
        return None

    name_clash = Service.objects.filter(hospital=hospital, name=test.name).exclude(lab_tests_next=test).first()
    if name_clash:
        return (
            f'A service named "{test.name}" already exists for your hospital and isn\'t linked to this '
            "test. Rename or relink it under Manage Services before setting a price here."
        )

    service = Service.objects.create(
        hospital=hospital, name=test.name, category=Service.CATEGORY_LAB,
        price=price, is_active=test.active_for_ordering,
    )
    service.lab_tests_next.add(test)
    return None


@login_required
@staff_required
def test_list(request):
    hospital = get_active_hospital(request)
    services_by_test = {}
    if hospital:
        for svc in Service.objects.filter(hospital=hospital, lab_tests_next__isnull=False).prefetch_related("lab_tests_next"):
            for test in svc.lab_tests_next.all():
                services_by_test[test.pk] = svc

    tests = LabTest.objects.select_related("category").prefetch_related("accepted_specimens")
    if hospital and getattr(request.user, "role", "") != "superadmin":
        tests = tests.filter(hospital=hospital)
    rows = [{"test": t, "service": services_by_test.get(t.pk)} for t in tests]
    paginator = Paginator(rows, CATALOG_PAGE_SIZE)

    return render(request, "lab/catalog_test_list.html", {
        "rows": paginator.get_page(request.GET.get("page")),
        "is_admin": _catalog_admin_ok(request.user), "active_nav": "lab_tests",
    })


@login_required
@catalog_admin_required
@transaction.atomic
def test_create(request):
    hospital = get_active_hospital(request)
    if request.method == "POST":
        form = LabTestForm(request.POST)
        options_formset = DefinedOptionFormSet(request.POST, prefix="options")
        params_formset = ParameterRowFormSet(request.POST, prefix="params")
        if form.is_valid():
            result_type = form.cleaned_data["result_type"]
            rows_ok = (
                options_formset.is_valid() if result_type == ResultType.DEFINED_OPTION
                else params_formset.is_valid() if result_type == ResultType.PARAMETER_PANEL
                else True
            )
            if rows_ok:
                test = form.save(commit=False)
                test.hospital = hospital
                test.save()
                form.save_m2m()
                if result_type == ResultType.DEFINED_OPTION:
                    _save_defined_options(test, options_formset)
                elif result_type == ResultType.PARAMETER_PANEL:
                    _save_parameters(test, params_formset)
                error = _sync_hospital_service(test, hospital, form.cleaned_data.get("price"))
                if error:
                    messages.warning(request, f'"{test.name}" added to your catalog, but: {error}')
                else:
                    messages.success(request, f'"{test.name}" added to your catalog.')
                return redirect("lab_test_list")
    else:
        form = LabTestForm()
        options_formset = DefinedOptionFormSet(prefix="options")
        params_formset = ParameterRowFormSet(prefix="params")
    return render(request, "lab/catalog_test_form.html", {
        "form": form, "active_nav": "lab_tests", "is_edit": False,
        "result_type_notes": RESULT_TYPE_NOTES,
        "options_formset": options_formset, "params_formset": params_formset,
        "param_blocks": _group_parameter_forms(params_formset),
    })


@login_required
@catalog_admin_required
@transaction.atomic
def test_edit(request, pk):
    hospital = get_active_hospital(request)
    tests = LabTest.objects.all()
    if hospital and getattr(request.user, "role", "") != "superadmin":
        tests = tests.filter(hospital=hospital)
    test = get_object_or_404(tests, pk=pk)
    existing_service = Service.objects.filter(hospital=hospital, lab_tests_next=test).first()

    if request.method == "POST":
        form = LabTestForm(request.POST, instance=test)
        options_formset = DefinedOptionFormSet(request.POST, prefix="options")
        params_formset = ParameterRowFormSet(request.POST, prefix="params")
        if form.is_valid():
            result_type = form.cleaned_data["result_type"]
            rows_ok = (
                options_formset.is_valid() if result_type == ResultType.DEFINED_OPTION
                else params_formset.is_valid() if result_type == ResultType.PARAMETER_PANEL
                else True
            )
            if rows_ok:
                test = form.save()
                if result_type == ResultType.DEFINED_OPTION:
                    _save_defined_options(test, options_formset)
                elif result_type == ResultType.PARAMETER_PANEL:
                    _save_parameters(test, params_formset)
                error = _sync_hospital_service(test, hospital, form.cleaned_data.get("price"))
                if error:
                    messages.warning(request, f'"{test.name}" updated, but: {error}')
                else:
                    messages.success(request, f'"{test.name}" updated.')
                return redirect("lab_test_list")
    else:
        initial = {"price": existing_service.price} if existing_service else {}
        form = LabTestForm(instance=test, initial=initial)
        options_formset = DefinedOptionFormSet(prefix="options", initial=_initial_defined_options(test))
        params_formset = ParameterRowFormSet(prefix="params", initial=_initial_parameters(test))

    return render(request, "lab/catalog_test_form.html", {
        "form": form, "active_nav": "lab_tests", "is_edit": True, "test": test,
        "result_type_notes": RESULT_TYPE_NOTES,
        "options_formset": options_formset, "params_formset": params_formset,
        "param_blocks": _group_parameter_forms(params_formset),
    })


@login_required
@staff_required
def test_clone_picker(request):
    """Browse tests to copy in as a starting point for a new template.
    Test definitions aren't sensitive clinical data (no patients, no
    orders) so browsing another hospital's catalog here is fine — it's the
    same idea as an industry-standard test panel, just sourced from a peer
    instead of a static library.

    Ternah-curated `is_starter_template` rows (a ready-made CBC, Malaria,
    Typhoid, Urinalysis, Culture & Sensitivity...) are pinned to the top so
    a hospital isn't stuck hunting through every other hospital's catalog
    just to find a standard panel."""
    hospital = get_active_hospital(request)
    q = request.GET.get("q", "").strip()
    tests = LabTest.objects.select_related("category", "hospital").prefetch_related("accepted_specimens")
    if q:
        tests = tests.filter(name__icontains=q)
    if hospital:
        tests = tests.exclude(hospital=hospital)  # you already have your own tests — nothing to clone there
    tests = tests.order_by("-is_starter_template", "hospital__name", "name")
    return render(request, "lab/catalog_test_clone_picker.html", {
        "tests": tests, "q": q, "active_nav": "lab_tests",
    })


@login_required
@catalog_admin_required
@transaction.atomic
def test_clone(request, pk):
    hospital = get_active_hospital(request)
    if request.method != "POST":
        return redirect("lab_test_clone_picker")
    if not hospital:
        messages.error(request, "Select a hospital before cloning a test.")
        return redirect("lab_test_clone_picker")

    source = get_object_or_404(LabTest.objects.select_related("category"), pk=pk)
    clone = clone_lab_test(source, hospital)

    messages.success(
        request,
        f'"{source.name}" copied into your catalog as a starting point — set a price and review '
        "the parameters/ranges before it's orderable, since your lab's own reference values may differ.",
    )
    return redirect("lab_test_edit", pk=clone.pk)


@login_required
@staff_required
def report_settings(request):
    """How this hospital's printed lab reports are formatted: whether the
    standard disclaimer footnote prints, and whether simple Positive/Negative
    results share one page instead of each getting its own. Separate from
    Consumables (`lab_settings` view) -- this is purely report layout, not
    stock. Same view/edit split as the rest of Lab Management: anyone with
    lab access can see current settings, only hospital admins (and
    superadmins) can change them -- lab attendants get read-only."""
    hospital = get_active_hospital(request)
    if not hospital:
        messages.error(request, "Select a hospital first.")
        return redirect("lab_test_list")

    is_admin = _catalog_admin_ok(request.user)
    settings_row, _ = LabSettings.objects.get_or_create(hospital=hospital)

    if request.method == "POST":
        if not is_admin:
            raise PermissionDenied("Only hospital admins can change report settings.")
        settings_row.show_report_footnote = bool(request.POST.get("show_report_footnote"))
        settings_row.combine_defined_option_reports = bool(request.POST.get("combine_defined_option_reports"))
        settings_row.updated_by = request.user
        settings_row.save(update_fields=["show_report_footnote", "combine_defined_option_reports", "updated_by", "updated_at"])
        messages.success(request, "Report settings updated.")
        return redirect("lab_report_settings")

    return render(request, "lab/catalog_report_settings.html", {
        "settings_row": settings_row, "is_admin": is_admin, "active_nav": "lab_report_settings",
    })
