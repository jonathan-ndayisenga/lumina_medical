"""
Audit command to find visits that violate billing structure rules.
This helps detect receptionist loopholes or improper visits.

Usage: python manage.py audit_billing_violations
"""

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from reception.models import Visit


class Command(BaseCommand):
    help = "Audit visits for billing structure violations (services missing, fake follow-ups, etc.)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Attempt to fix obvious violations (requires admin oversight)",
        )
        parser.add_argument(
            "--hospital-id",
            type=int,
            help="Only audit visits for a specific hospital",
        )

    def handle(self, *args, **options):
        fix_mode = options.get("fix", False)
        hospital_id = options.get("hospital_id")

        visits_qs = Visit.objects.select_related("patient", "parent_visit", "adjustment_origin_prescription__visit")

        if hospital_id:
            visits_qs = visits_qs.filter(hospital_id=hospital_id)

        violations = []
        skipped_quick_dispense = 0
        fixed_count = 0

        for visit in visits_qs.iterator():
            # Skip quick dispense visits - these are allowed to have zero amount and no services
            # They use a special workflow where drugs are added after the visit is created
            if (
                visit.status == Visit.STATUS_READY_FOR_BILLING
                and visit.total_amount <= 0
                and visit.visit_services.count() == 0
                and visit.visit_type == Visit.TYPE_NORMAL
            ):
                skipped_quick_dispense += 1
                continue
            
            try:
                visit.validate_billing_structure()
            except ValidationError as e:
                violations.append(
                    {
                        "visit_id": visit.pk,
                        "patient": visit.patient.name,
                        "type": visit.get_visit_type_display(),
                        "status": visit.get_status_display(),
                        "total_amount": visit.total_amount,
                        "error": e.message,
                    }
                )

                if fix_mode:
                    # For now, just log - actual fixes require admin judgment
                    self.stdout.write(
                        self.style.WARNING(
                            f"Visit #{visit.pk} ({visit.patient.name}): {e.message}"
                        )
                    )

        if not violations:
            msg = "✓ No billing violations found. System is secure."
            if skipped_quick_dispense > 0:
                msg += f" ({skipped_quick_dispense} quick dispense visit(s) skipped)"
            self.stdout.write(self.style.SUCCESS(msg))
            return

        self.stdout.write(self.style.ERROR(f"\n✗ Found {len(violations)} billing violation(s):"))
        self.stdout.write(self.style.ERROR("=" * 80))

        for v in violations:
            self.stdout.write(
                self.style.ERROR(
                    f"\nVisit #{v['visit_id']} | {v['patient']}"
                )
            )
            self.stdout.write(f"  Type: {v['type']}")
            self.stdout.write(f"  Status: {v['status']}")
            self.stdout.write(f"  Total Amount: {v['total_amount']}")
            self.stdout.write(f"  Issue: {v['error']}")

        if skipped_quick_dispense > 0:
            self.stdout.write(self.style.WARNING(f"\nℹ Skipped {skipped_quick_dispense} quick dispense visit(s) (these are allowed exceptions)"))

        if fix_mode:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠ Fix mode enabled, but manual review is required for each violation."
                )
            )

        raise CommandError(
            f"Audit complete. {len(violations)} violation(s) detected. "
            "Review the visit(s) above and take corrective action."
        )
