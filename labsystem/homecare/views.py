import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import (
    HomeCareClient,
    HomeCareContract,
    HomeCareNurse,
    HomeCareReceipt,
    HomeCarePlacement,
)


def homecare_access_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.can_access_home_care:
            return HttpResponseForbidden(
                "This hospital does not have the Home Care Management module enabled, "
                "or your account does not have access to it."
            )
        return view_func(request, *args, **kwargs)
    return wrapped


def get_active_hospital(request):
    return getattr(request, "hospital", None) or getattr(request.user, "hospital", None)


@homecare_access_required
def homecare_dashboard(request):
    hospital = get_active_hospital(request)
    today = timezone.now().date()

    # ── Stat cards ─────────────────────────────────────────────────────────────
    active_nurses = HomeCareNurse.objects.filter(hospital=hospital, is_active=True).count()
    active_placements = HomeCarePlacement.objects.filter(
        hospital=hospital, status=HomeCarePlacement.STATUS_ACTIVE
    ).count()
    recent_placements = HomeCarePlacement.objects.filter(
        hospital=hospital
    ).select_related("client", "nurse").order_by("-created_at")[:5]

    month_start = today.replace(day=1)
    income_this_month = HomeCareReceipt.objects.filter(
        placement__hospital=hospital,
        paid_at__date__gte=month_start,
        paid_at__date__lte=today,
    ).aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")

    # ── Finance chart: last 6 months ───────────────────────────────────────────
    months = []
    cursor = today.replace(day=1)
    for _ in range(6):
        months.append(cursor)
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    months.reverse()

    month_labels = [m.strftime("%b %Y") for m in months]
    income_values = []
    payout_values = []
    margin_values = []

    all_placements = list(
        HomeCarePlacement.objects.filter(hospital=hospital)
        .exclude(status=HomeCarePlacement.STATUS_TERMINATED)
        .values("id", "nurse_rate", "contract_start", "contract_end")
    )

    for month_start_d in months:
        next_month = (month_start_d + timedelta(days=32)).replace(day=1)
        month_end_d = next_month - timedelta(days=1)

        # Income = actual receipts collected in that calendar month
        income = HomeCareReceipt.objects.filter(
            placement__hospital=hospital,
            paid_at__date__gte=month_start_d,
            paid_at__date__lte=month_end_d,
        ).aggregate(total=Sum("amount_paid"))["total"] or Decimal("0")

        # Nurse payouts = projected cost for placements active in that month
        payout = Decimal("0")
        for p in all_placements:
            if p["contract_start"] <= month_end_d and p["contract_end"] >= month_start_d:
                payout += (p["nurse_rate"] or Decimal("0"))

        income_values.append(str(income))
        payout_values.append(str(payout))
        margin_values.append(str(income - payout))

    return render(request, "homecare/dashboard.html", {
        "active_nav": "homecare",
        "hospital": hospital,
        "active_nurses": active_nurses,
        "active_placements": active_placements,
        "income_this_month": income_this_month,
        "recent_placements": recent_placements,
        "chart_labels_json": json.dumps(month_labels),
        "income_values_json": json.dumps(income_values),
        "payout_values_json": json.dumps(payout_values),
        "margin_values_json": json.dumps(margin_values),
    })


@homecare_access_required
def nurse_list(request):
    hospital = get_active_hospital(request)
    nurses = HomeCareNurse.objects.filter(hospital=hospital)
    return render(request, "homecare/nurse_list.html", {"active_nav": "homecare", "nurses": nurses})


@homecare_access_required
def register_nurse(request):
    hospital = get_active_hospital(request)
    from django.contrib import messages
    from .forms import HomeCareNurseForm
    if request.method == "POST":
        form = HomeCareNurseForm(request.POST)
        if form.is_valid():
            nurse = form.save(commit=False)
            nurse.hospital = hospital
            nurse.created_by = request.user
            nurse.save()
            messages.success(request, f"Nurse {nurse.name} registered successfully.")
            return redirect("homecare_nurse_list")
        messages.error(request, "Please fix the details below.")
    else:
        form = HomeCareNurseForm()
    return render(request, "homecare/register_nurse.html", {"active_nav": "homecare", "form": form})


@homecare_access_required
def nurse_detail(request, nurse_id):
    hospital = get_active_hospital(request)
    nurse = get_object_or_404(HomeCareNurse, pk=nurse_id, hospital=hospital)
    placements = nurse.placements.select_related("client").order_by("-created_at")
    return render(request, "homecare/nurse_detail.html", {"active_nav": "homecare", "nurse": nurse, "placements": placements})


@homecare_access_required
def client_list(request):
    hospital = get_active_hospital(request)
    clients = HomeCareClient.objects.filter(hospital=hospital)
    return render(request, "homecare/client_list.html", {"active_nav": "homecare", "clients": clients})


@homecare_access_required
def register_client(request):
    hospital = get_active_hospital(request)
    from django.contrib import messages
    from .forms import HomeCareClientForm
    if request.method == "POST":
        form = HomeCareClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.hospital = hospital
            client.created_by = request.user
            client.save()
            messages.success(request, f"Client {client.name} registered successfully.")
            return redirect("homecare_client_list")
        messages.error(request, "Please fix the details below.")
    else:
        form = HomeCareClientForm()
    return render(request, "homecare/register_client.html", {"active_nav": "homecare", "form": form})


@homecare_access_required
def client_detail(request, client_id):
    hospital = get_active_hospital(request)
    client = get_object_or_404(HomeCareClient, pk=client_id, hospital=hospital)
    placements = client.placements.select_related("nurse").order_by("-created_at")
    return render(request, "homecare/client_detail.html", {"active_nav": "homecare", "client": client, "placements": placements})


@homecare_access_required
def placement_list(request):
    hospital = get_active_hospital(request)
    placements = HomeCarePlacement.objects.filter(hospital=hospital).select_related("client", "nurse")
    return render(request, "homecare/placement_list.html", {"active_nav": "homecare", "placements": placements})


@homecare_access_required
def placement_create(request):
    hospital = get_active_hospital(request)
    from django.contrib import messages
    from .forms import HomeCarePlacementForm
    if request.method == "POST":
        form = HomeCarePlacementForm(request.POST, hospital=hospital)
        if form.is_valid():
            placement = form.save(commit=False)
            placement.hospital = hospital
            placement.created_by = request.user
            placement.save()
            HomeCareContract.objects.create(placement=placement)
            messages.success(request, f"Placement created and contract generated for {placement.client.name}.")
            return redirect("homecare_placement_detail", placement_id=placement.pk)
        messages.error(request, "Please fix the details below.")
    else:
        form = HomeCarePlacementForm(hospital=hospital)
    return render(request, "homecare/placement_create.html", {"active_nav": "homecare", "form": form})


@homecare_access_required
def placement_detail(request, placement_id):
    hospital = get_active_hospital(request)
    placement = get_object_or_404(
        HomeCarePlacement.objects.select_related("client", "nurse").prefetch_related("receipts"),
        pk=placement_id, hospital=hospital,
    )
    return render(request, "homecare/placement_detail.html", {"active_nav": "homecare", "placement": placement})


@homecare_access_required
def record_receipt(request, placement_id):
    hospital = get_active_hospital(request)
    from django.contrib import messages
    from .forms import HomeCareReceiptForm
    placement = get_object_or_404(HomeCarePlacement, pk=placement_id, hospital=hospital)
    if request.method == "POST":
        form = HomeCareReceiptForm(request.POST)
        if form.is_valid():
            receipt = form.save(commit=False)
            receipt.placement = placement
            receipt.recorded_by = request.user
            receipt.save()
            messages.success(request, f"Receipt {receipt.receipt_number} issued — UGX {receipt.amount_paid}.")
            return redirect("homecare_receipt_print", receipt_id=receipt.pk)
        messages.error(request, "Please fix the details below.")
    else:
        form = HomeCareReceiptForm(initial={"amount_paid": placement.balance_due})
    return render(request, "homecare/record_receipt.html", {
        "active_nav": "homecare",
        "placement": placement,
        "form": form,
    })


@homecare_access_required
def contract_list(request):
    hospital = get_active_hospital(request)
    contracts = HomeCareContract.objects.filter(
        placement__hospital=hospital
    ).select_related("placement__client", "placement__nurse")
    return render(request, "homecare/contract_list.html", {"active_nav": "homecare", "contracts": contracts})


@homecare_access_required
def contract_print(request, contract_id):
    hospital = get_active_hospital(request)
    contract = get_object_or_404(HomeCareContract, pk=contract_id, placement__hospital=hospital)
    return render(request, "homecare/contract_print.html", {"hospital": hospital, "contract": contract})


@homecare_access_required
def receipt_list(request):
    hospital = get_active_hospital(request)
    receipts = HomeCareReceipt.objects.filter(
        placement__hospital=hospital
    ).select_related("placement__client", "placement__nurse", "recorded_by")
    return render(request, "homecare/receipt_list.html", {"active_nav": "homecare", "receipts": receipts})


@homecare_access_required
def receipt_print(request, receipt_id):
    hospital = get_active_hospital(request)
    receipt = get_object_or_404(HomeCareReceipt, pk=receipt_id, placement__hospital=hospital)
    return render(request, "homecare/receipt_print.html", {"hospital": hospital, "receipt": receipt})
