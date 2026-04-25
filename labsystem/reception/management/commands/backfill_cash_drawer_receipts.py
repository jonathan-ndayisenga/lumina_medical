from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from reception.models import Payment


class Command(BaseCommand):
    help = "Backfill cash drawer transactions for existing cash receipts (Payment.mode=cash)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created without writing to the database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        from admin_dashboard.models import CashDrawer, CashTransaction

        qs = (
            Payment.objects.filter(mode=Payment.MODE_CASH)
            .exclude(status=Payment.STATUS_WAIVED)
            .exclude(amount_paid__lte=0)
            .select_related("visit__hospital", "visit__patient")
            .order_by("paid_at", "id")
        )

        created_drawers = 0
        created_txns = 0
        updated_txns = 0

        for payment in qs.iterator():
            hospital = payment.visit.hospital
            paid_date = (payment.paid_at or timezone.now()).date()

            drawer = (
                CashDrawer.objects.filter(hospital=hospital, date=paid_date)
                .order_by("-id")
                .first()
            )
            if not drawer:
                # Best-effort opening balance: last closed drawer closing, else 0.
                last_closed = (
                    CashDrawer.objects.filter(hospital=hospital, closed_at__isnull=False)
                    .order_by("-date", "-id")
                    .first()
                )
                opening = last_closed.closing_balance if last_closed and last_closed.closing_balance is not None else Decimal("0")
                if dry_run:
                    created_drawers += 1
                else:
                    drawer = CashDrawer.objects.create(hospital=hospital, date=paid_date, opening_balance=opening)
                    created_drawers += 1

            existing = (
                CashTransaction.objects.filter(payment=payment, transaction_type=CashTransaction.TYPE_CASH_IN)
                .order_by("id")
                .first()
            )
            description = f"Receipt {payment.receipt_number} - {payment.visit.patient.name}"

            if existing:
                if existing.cash_drawer_id != drawer.id or existing.amount != payment.amount_paid or existing.description != description:
                    if not dry_run:
                        existing.cash_drawer = drawer
                        existing.amount = payment.amount_paid
                        existing.description = description
                        existing.save(update_fields=["cash_drawer", "amount", "description"])
                    updated_txns += 1
                continue

            if dry_run:
                created_txns += 1
            else:
                CashTransaction.objects.create(
                    cash_drawer=drawer,
                    payment=payment,
                    amount=payment.amount_paid,
                    transaction_type=CashTransaction.TYPE_CASH_IN,
                    description=description,
                )
                created_txns += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete. drawers_created={created_drawers} cash_in_created={created_txns} cash_in_updated={updated_txns} dry_run={dry_run}"
            )
        )

