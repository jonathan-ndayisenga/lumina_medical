from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from accounts.models import Hospital


def sync_hospital_account_balance(hospital):
    if hospital is None:
        return None

    Payment = apps.get_model("reception", "Payment")
    account, _ = HospitalAccount.objects.get_or_create(hospital=hospital)

    income = (
        Payment.objects.filter(visit__hospital=hospital).aggregate(total=models.Sum("amount_paid"))["total"]
        or Decimal("0")
    )
    expenses = Expense.objects.filter(hospital=hospital).aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
    salaries = (
        Salary.objects.filter(hospital=hospital, paid=True).aggregate(total=models.Sum("amount"))["total"]
        or Decimal("0")
    )

    account.balance = income - (expenses + salaries)
    account.save(update_fields=["balance", "updated_at"])
    return account


class HospitalAccount(models.Model):
    hospital = models.OneToOneField(Hospital, on_delete=models.CASCADE, related_name="account")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["hospital__name"]

    def __str__(self):
        return f"{self.hospital.name} Account"


class Expense(models.Model):
    CATEGORY_RENT = "rent"
    CATEGORY_UTILITIES = "utilities"
    CATEGORY_CONSUMABLES = "consumables"
    CATEGORY_MAINTENANCE = "maintenance"
    CATEGORY_LOGISTICS = "logistics"
    CATEGORY_OTHER = "other"

    CATEGORY_CHOICES = [
        (CATEGORY_RENT, "Rent"),
        (CATEGORY_UTILITIES, "Utilities"),
        (CATEGORY_CONSUMABLES, "Consumables"),
        (CATEGORY_MAINTENANCE, "Maintenance"),
        (CATEGORY_LOGISTICS, "Logistics"),
        (CATEGORY_OTHER, "Other"),
    ]

    SOURCE_CASH_DRAWER = "cash_drawer"
    SOURCE_BANK_ACCOUNT = "bank_account"
    SOURCE_MOBILE_MONEY = "mobile_money"
    
    SOURCE_CHOICES = [
        (SOURCE_CASH_DRAWER, "Cash Drawer"),
        (SOURCE_BANK_ACCOUNT, "Bank Account"),
        (SOURCE_MOBILE_MONEY, "Mobile Money"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="expenses")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_CASH_DRAWER,
        help_text="Which channel funded this expense",
    )
    bank_account = models.ForeignKey(
        "BankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    mobile_money_account = models.ForeignKey(
        "MobileMoneyAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    cash_drawer = models.ForeignKey(
        "CashDrawer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
    )
    date = models.DateField(auto_now_add=True)
    notes = models.TextField(blank=True, help_text="Additional notes about this expense")

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.hospital.name} - {self.description}"

    def get_source_display_with_badge(self):
        badge_colors = {
            self.SOURCE_CASH_DRAWER: "bg-green-50 text-green-700",
            self.SOURCE_BANK_ACCOUNT: "bg-blue-50 text-blue-700",
            self.SOURCE_MOBILE_MONEY: "bg-purple-50 text-purple-700",
        }
        return {
            "name": self.get_source_display(),
            "badge_class": badge_colors.get(self.source, "bg-gray-50 text-gray-700")
        }

    @property
    def source_account_label(self):
        if self.source == self.SOURCE_BANK_ACCOUNT and self.bank_account:
            return str(self.bank_account)
        if self.source == self.SOURCE_MOBILE_MONEY and self.mobile_money_account:
            return str(self.mobile_money_account)
        if self.source == self.SOURCE_CASH_DRAWER and self.cash_drawer:
            return f"Drawer {self.cash_drawer.date}"
        return "Not specified"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Auto-assign a daily cash statement row if the expense is funded from cash and none was selected.
        if self.source == self.SOURCE_CASH_DRAWER and not self.cash_drawer_id and self.amount > 0:
            drawer = CashDrawer.objects.filter(hospital=self.hospital, date=self.date).order_by("-id").first()
            if not drawer:
                last_with_balance = (
                    CashDrawer.objects.filter(hospital=self.hospital, closing_balance__isnull=False)
                    .order_by("-date", "-id")
                    .first()
                )
                opening = (
                    last_with_balance.closing_balance
                    if last_with_balance and last_with_balance.closing_balance is not None
                    else Decimal("0")
                )
                drawer = CashDrawer.objects.create(
                    hospital=self.hospital,
                    date=self.date,
                    opening_balance=opening,
                )
            self.cash_drawer = drawer
            super().save(update_fields=["cash_drawer"])

        # Keep cash drawer statements accurate by mirroring cash-funded expenses as cash-out transactions.
        if self.source == self.SOURCE_CASH_DRAWER and self.cash_drawer_id and self.amount > 0:
            existing = (
                CashTransaction.objects.filter(
                    expense=self,
                    transaction_type=CashTransaction.TYPE_CASH_OUT,
                )
                .order_by("id")
                .first()
            )
            if existing:
                existing.cash_drawer = self.cash_drawer
                existing.amount = self.amount
                existing.description = self.description
                existing.save(update_fields=["cash_drawer", "amount", "description"])
            else:
                CashTransaction.objects.create(
                    cash_drawer=self.cash_drawer,
                    expense=self,
                    amount=self.amount,
                    transaction_type=CashTransaction.TYPE_CASH_OUT,
                    description=self.description,
                )
        else:
            CashTransaction.objects.filter(expense=self).delete()
        sync_hospital_account_balance(self.hospital)

    def delete(self, *args, **kwargs):
        hospital = self.hospital
        CashTransaction.objects.filter(expense=self).delete()
        super().delete(*args, **kwargs)
        sync_hospital_account_balance(hospital)


class Salary(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="salaries")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="salary_records")
    month = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid = models.BooleanField(default=False)
    paid_at = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-month", "-id"]

    def __str__(self):
        return f"{self.employee} - {self.month:%Y-%m}"

    def save(self, *args, **kwargs):
        if self.paid and self.paid_at is None:
            self.paid_at = timezone.localdate()
        if not self.paid:
            self.paid_at = None
        super().save(*args, **kwargs)
        sync_hospital_account_balance(self.hospital)

    def delete(self, *args, **kwargs):
        hospital = self.hospital
        super().delete(*args, **kwargs)
        sync_hospital_account_balance(hospital)


class InventoryItem(models.Model):
    CATEGORY_DRUG = "drug"
    CATEGORY_SYRUP = "syrup"
    CATEGORY_IV = "iv"
    CATEGORY_IM = "im"
    CATEGORY_TUBE = "tube"
    CATEGORY_REAGENT = "reagent"
    CATEGORY_SUNDRY = "sundry"

    CATEGORY_CHOICES = [
        (CATEGORY_DRUG, "Tablet / Capsule"),
        (CATEGORY_SYRUP, "Syrup / Suspension"),
        (CATEGORY_IV, "IV Medication / Fluid"),
        (CATEGORY_IM, "IM Medication"),
        (CATEGORY_TUBE, "Tube / Cream / Ointment"),
        (CATEGORY_REAGENT, "Reagent"),
        (CATEGORY_SUNDRY, "Sundry"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="inventory_items")
    name = models.CharField(max_length=100)
    quantity = models.IntegerField(default=0)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    low_stock_threshold = models.IntegerField(default=5)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default=CATEGORY_SUNDRY)
    unit = models.CharField(max_length=20, default="unit")
    base_unit = models.CharField(
        max_length=20,
        default="unit",
        help_text="The smallest clinical unit used for prescribing, e.g. tablet, ml, g, vial.",
    )
    units_per_pack = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1,
        help_text="How many base units are contained in one stocked/sold pack.",
    )
    strength_mg_per_unit = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="For tablets/capsules, how many mg one unit contains.",
    )
    current_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    reorder_level = models.DecimalField(max_digits=10, decimal_places=2, default=10)
    is_active = models.BooleanField(default=True)
    concentration_mg_per_ml = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pack_size_ml = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    days_covered_per_pack = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="For tubes and other whole-pack items, how many treatment days one pack usually covers.",
    )

    class Meta:
        ordering = ["name"]
        unique_together = ("hospital", "name")

    def __str__(self):
        return f"{self.name} ({self.hospital.name})"

    @property
    def is_low_stock(self):
        return self.current_quantity <= self.reorder_level

    @property
    def quantity_label(self):
        if self.category == self.CATEGORY_DRUG:
            if self.unit != self.base_unit and self.units_per_pack > 0 and self.base_unit:
                total_base = self.current_quantity * self.units_per_pack
                return f"{self.current_quantity} {self.unit}(s) (~{total_base} {self.base_unit}(s))"
            return f"{self.current_quantity} {self.base_unit}(s)"
        if self.category == self.CATEGORY_SYRUP:
            if self.units_per_pack > 0 and self.base_unit:
                total_base = self.current_quantity * self.units_per_pack
                return f"{self.current_quantity} {self.unit}(s) (~{total_base} {self.base_unit})"
            return f"{self.current_quantity} {self.unit}(s)"
        if self.category in {self.CATEGORY_IV, self.CATEGORY_IM}:
            if self.units_per_pack > 0 and self.base_unit:
                total_base = self.current_quantity * self.units_per_pack
                return f"{self.current_quantity} {self.unit}(s) (~{total_base} {self.base_unit})"
            return f"{self.current_quantity} {self.unit}(s)"
        if self.category == self.CATEGORY_TUBE:
            if self.units_per_pack > 0 and self.base_unit and self.base_unit != "unit":
                total_base = self.current_quantity * self.units_per_pack
                return f"{self.current_quantity} tube(s) (~{total_base} {self.base_unit})"
            return f"{self.current_quantity} tube(s)"
        return f"{self.current_quantity} {self.unit}"

    @property
    def is_prescribable(self):
        return self.category in {
            self.CATEGORY_DRUG,
            self.CATEGORY_SYRUP,
            self.CATEGORY_IV,
            self.CATEGORY_IM,
            self.CATEGORY_TUBE,
        }

    @property
    def price_per_base_unit(self):
        selling_price = Decimal(self.selling_price or 0)
        units_per_pack = Decimal(self.units_per_pack or 0)
        if units_per_pack <= 0:
            return Decimal("0")
        return (selling_price / units_per_pack).quantize(Decimal("0.01"))

    @property
    def available_dispense_quantity(self):
        quantity = Decimal(self.current_quantity or 0)
        if self.category == self.CATEGORY_DRUG and self.unit != self.base_unit:
            units_per_pack = Decimal(self.units_per_pack or 0)
            if units_per_pack > 0:
                return (quantity * units_per_pack).quantize(Decimal("0.01"))
        return quantity

    def to_stock_quantity(self, dispense_quantity):
        quantity = Decimal(dispense_quantity or 0)
        if self.category == self.CATEGORY_DRUG and self.unit != self.base_unit:
            units_per_pack = Decimal(self.units_per_pack or 0)
            if units_per_pack > 0:
                return (quantity / units_per_pack).quantize(Decimal("0.01"))
        return quantity

    @property
    def has_batch_tracking(self):
        return self.batches.exists()

    def recalculate_current_quantity(self, save=True):
        batch_total = self.batches.aggregate(total=Sum("quantity")).get("total")
        if batch_total is None:
            return self.current_quantity
        self.current_quantity = Decimal(batch_total or 0)
        self.quantity = int(self.current_quantity or 0)
        if save:
            super().save(update_fields=["current_quantity", "quantity"])
        return self.current_quantity

    def add_or_update_batch(self, batch_number, quantity, expiry_date=None, unit_cost=None):
        batch_number = (batch_number or "").strip() or "UNSPECIFIED"
        defaults = {
            "expiry_date": expiry_date,
            "unit_cost": unit_cost if unit_cost is not None else Decimal("0"),
        }
        batch, created = self.batches.get_or_create(batch_number=batch_number, defaults=defaults)
        if created:
            batch.quantity = Decimal("0")
        elif expiry_date and batch.expiry_date != expiry_date:
            batch.expiry_date = expiry_date
        if unit_cost is not None:
            batch.unit_cost = unit_cost
        batch.quantity = (batch.quantity or Decimal("0")) + Decimal(quantity or 0)
        batch.save()
        self.recalculate_current_quantity()
        return batch

    def consume_stock(self, quantity_to_deduct):
        quantity_to_deduct = Decimal(quantity_to_deduct or 0)
        if quantity_to_deduct <= 0:
            return []

        consumption_log = []
        positive_batches = list(
            self.batches.filter(quantity__gt=0).order_by(
                models.F("expiry_date").asc(nulls_last=True),
                "created_at",
                "id",
            )
        )
        if not positive_batches:
            self.current_quantity = (self.current_quantity or Decimal("0")) - quantity_to_deduct
            self.save(update_fields=["current_quantity", "quantity", "unit_price", "low_stock_threshold"])
            return consumption_log

        remaining = quantity_to_deduct
        for batch in positive_batches:
            if remaining <= 0:
                break
            available = Decimal(batch.quantity or 0)
            if available <= 0:
                continue
            used = available if available <= remaining else remaining
            batch.quantity = available - used
            batch.save(update_fields=["quantity", "updated_at"])
            consumption_log.append({"batch": batch, "quantity": used})
            remaining -= used

        self.recalculate_current_quantity()
        if remaining > 0:
            raise ValueError(f"Not enough batch stock available for {self.name}. Remaining: {remaining}")
        return consumption_log

    def save(self, *args, **kwargs):
        if not self.current_quantity and self.quantity:
            self.current_quantity = Decimal(self.quantity)
        if not self.unit_cost and self.unit_price:
            self.unit_cost = self.unit_price
        if not self.reorder_level and self.low_stock_threshold:
            self.reorder_level = Decimal(self.low_stock_threshold)
        if self.category == self.CATEGORY_DRUG:
            self.base_unit = self.base_unit or "tablet"
            self.units_per_pack = self.units_per_pack or Decimal("1")
            if self.unit == "unit":
                self.unit = "tablet"
        elif self.category == self.CATEGORY_SYRUP:
            self.base_unit = "ml"
            if self.unit == "unit":
                self.unit = "bottle"
            if self.pack_size_ml and (not self.units_per_pack or self.units_per_pack == Decimal("1")):
                self.units_per_pack = self.pack_size_ml
        elif self.category in {self.CATEGORY_IV, self.CATEGORY_IM}:
            self.base_unit = self.base_unit or "ml"
            if self.unit == "unit":
                self.unit = "vial" if self.category == self.CATEGORY_IM else "bag"
        elif self.category == self.CATEGORY_TUBE:
            if self.unit == "unit":
                self.unit = "tube"
            if self.base_unit == "unit":
                self.base_unit = "g"
        self.quantity = int(self.current_quantity or 0)
        self.unit_price = self.unit_cost or Decimal("0")
        self.low_stock_threshold = int(self.reorder_level or 0)
        super().save(*args, **kwargs)


class InventoryTransaction(models.Model):
    TYPE_RECEIVE = "receive"
    TYPE_CONSUME = "consume"
    TYPE_ADJUST = "adjust"

    TYPE_CHOICES = [
        (TYPE_RECEIVE, "Receive"),
        (TYPE_CONSUME, "Consume"),
        (TYPE_ADJUST, "Adjust"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="inventory_transactions")
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="transactions")
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_CONSUME)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    visit = models.ForeignKey(
        "reception.Visit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
    )
    prescription = models.ForeignKey(
        "doctor.Prescription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
    )
    lab_report = models.ForeignKey(
        "lab.LabReport",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_transactions",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.item.name}"


class InventoryBatch(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name="batches")
    batch_number = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    expiry_date = models.DateField(null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["item__name", "expiry_date", "batch_number", "id"]
        unique_together = ("item", "batch_number")

    def __str__(self):
        return f"{self.item.name} - {self.batch_number}"


class BankAccount(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="bank_accounts")
    account_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=50)
    bank_name = models.CharField(max_length=100)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["bank_name", "account_name"]
        unique_together = ("hospital", "account_number")

    def __str__(self):
        return f"{self.bank_name} - {self.account_name}"


class MobileMoneyAccount(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="mobile_money_accounts")
    provider = models.CharField(max_length=50)
    number = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["provider", "number"]
        unique_together = ("hospital", "number")

    def __str__(self):
        return f"{self.provider} - {self.number}"


class MobileMoneyTransaction(models.Model):
    TYPE_CREDIT = "credit"
    TYPE_DEBIT = "debit"

    TYPE_CHOICES = [
        (TYPE_CREDIT, "Credit"),
        (TYPE_DEBIT, "Debit"),
    ]

    mobile_money_account = models.ForeignKey(
        MobileMoneyAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_CREDIT)
    reference = models.CharField(max_length=100, blank=True)
    is_reconciled = models.BooleanField(default=False)
    reconciled_with = models.ForeignKey(
        "reception.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mobile_money_transactions",
    )

    class Meta:
        ordering = ["-transaction_date", "-id"]

    def __str__(self):
        return f"{self.mobile_money_account} - {self.description}"


class BankTransaction(models.Model):
    TYPE_CREDIT = "credit"
    TYPE_DEBIT = "debit"

    TYPE_CHOICES = [
        (TYPE_CREDIT, "Credit"),
        (TYPE_DEBIT, "Debit"),
    ]

    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="transactions")
    transaction_date = models.DateField()
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    reference = models.CharField(max_length=100, blank=True)
    is_reconciled = models.BooleanField(default=False)
    reconciled_with = models.ForeignKey(
        "reception.Payment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bank_transactions",
    )

    class Meta:
        ordering = ["-transaction_date", "-id"]

    def __str__(self):
        return f"{self.bank_account} - {self.description}"


class CashDrawer(models.Model):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="cash_drawers")
    date = models.DateField(default=timezone.localdate)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_closing = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discrepancy = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.hospital.name} Cash Drawer - {self.date}"

    @property
    def is_closed(self):
        return self.closed_at is not None


class CashTransaction(models.Model):
    TYPE_CASH_IN = "cash_in"
    TYPE_CASH_OUT = "cash_out"

    TYPE_CHOICES = [
        (TYPE_CASH_IN, "Cash In"),
        (TYPE_CASH_OUT, "Cash Out"),
    ]

    cash_drawer = models.ForeignKey(CashDrawer, on_delete=models.CASCADE, related_name="transactions")
    payment = models.ForeignKey("reception.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="cash_transactions")
    expense = models.ForeignKey("admin_dashboard.Expense", on_delete=models.SET_NULL, null=True, blank=True, related_name="cash_transactions")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    description = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.cash_drawer} - {self.description}"


class ReconciliationStatement(models.Model):
    TYPE_BANK = "bank"
    TYPE_MOBILE_MONEY = "mobile_money"
    TYPE_THREE_WAY = "three_way"

    STATEMENT_TYPES = [
        (TYPE_BANK, "Bank Reconciliation"),
        (TYPE_MOBILE_MONEY, "Mobile Money Statement"),
        (TYPE_THREE_WAY, "Three-Way Reconciliation"),
    ]

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="reconciliation_statements")
    statement_type = models.CharField(max_length=20, choices=STATEMENT_TYPES)
    period_start = models.DateField()
    period_end = models.DateField()
    bank_account = models.ForeignKey(BankAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="statements")
    mobile_money_account = models.ForeignKey(
        MobileMoneyAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="statements",
    )
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deposits = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_withdrawals = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    outstanding_checks = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposits_in_transit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reconciled_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="reconciliation_statements",
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at", "-id"]

    def __str__(self):
        return f"{self.get_statement_type_display()} - {self.hospital.name}"
