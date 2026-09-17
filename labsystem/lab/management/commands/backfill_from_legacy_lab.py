"""
One-time backfill: converts old lab.LabReport + TestResult rows into the
reworked lab.LabOrder + LabResult + ResultValue models (formerly lab_next,
merged into this app), so historical reports survive the cutover to the new
engine instead of being stranded in a schema nobody reads anymore.

Safe to re-run — matched and skipped by legacy_report_id (LabOrder's
idempotency field).

Scope, deliberately: only converts reports linked to a real
`requested_visit_service` (the "structured" reports — one TestProfile-backed
service, one report). Reports that share the old "combined manual" report
per visit (profile=None, no requested_visit_service) are NOT converted here:
bundling several ad-hoc tests that never had individual billing lines into
separate LabOrders is a judgment call, not a mechanical one, and doing it
wrong here would be worse than leaving those reports visible in the old
report archive. They're counted and reported as skipped, never silently
dropped.

Each old TestResult row's `test` (a TestCatalog entry) is matched to a new
Parameter by name, case-insensitively — only meaningful for PARAMETER_PANEL
targets. This match can miss if parameter names drifted between the old
free-text catalog and the new authored LabTest; unmatched values are still
recorded (frozen as a plain label + value) so nothing is lost, just not
linked back to a Parameter definition.

IMPORTANT: the conversion trusts Service.lab_tests_next (admin-configured) to
decide which LabTest a legacy report's billed service maps to — NOT the
legacy report's own TestProfile. A legacy report predates the multi-test
feature, so if its service has since picked up more than one linked test,
whichever was linked first is used. Verify every lab Service is correctly
mapped before running this for real, or reports will be converted under
the wrong test.
"""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import transaction

from lab.models import (
    LabOrder,
    LabReport as LegacyLabReport,
    LabResult,
    OrderStage,
    ResultType,
    ResultValue,
    Sex,
)


def _parse_age_years(age_str):
    if not age_str:
        return None
    raw = age_str.upper().strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    value = int(digits)
    if "MTH" in raw or "MON" in raw:
        return 0
    return value


def _to_decimal(raw):
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return None


class Command(BaseCommand):
    help = "Backfill old LabReport data into the reworked lab engine. Use --dry-run first."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts without writing anything.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        converted = 0
        skipped_already_done = 0
        skipped_no_service = 0
        skipped_no_test_mapping = 0

        reports = LegacyLabReport.objects.select_related(
            "visit__hospital", "visit__patient",
        ).prefetch_related("results__test", "requested_visit_service__service__lab_tests_next")

        for report in reports:
            if LabOrder.objects.filter(legacy_report_id=report.pk).exists():
                skipped_already_done += 1
                continue

            visit_service = report.requested_visit_service
            if visit_service is None or not report.visit_id:
                skipped_no_service += 1
                continue

            # A legacy report predates the multi-test-per-service feature --
            # it was always exactly one report against one test, so take
            # whichever is linked first if the service has since picked up
            # more than one.
            test = visit_service.service.lab_tests_next.all()[:1].first()
            if test is None:
                skipped_no_test_mapping += 1
                continue

            if not dry_run:
                with transaction.atomic():
                    self._convert(report, visit_service, test)
            converted += 1

        self.stdout.write(self.style.SUCCESS(
            f"Converted: {converted} | "
            f"Skipped (no linked billed service — combined/manual reports): {skipped_no_service} | "
            f"Skipped (service not mapped to a LabTest yet): {skipped_no_test_mapping} | "
            f"Already backfilled: {skipped_already_done}"
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))

    def _convert(self, report, visit_service, test):
        patient = report.visit.patient
        order = LabOrder.objects.create(
            legacy_report_id=report.pk,
            visit_service=visit_service,
            test=test,
            hospital=report.visit.hospital,
            patient_sex={"M": Sex.MALE, "F": Sex.FEMALE}.get(patient.sex, Sex.ANY),
            patient_age_years=_parse_age_years(patient.age),
            stage=OrderStage.RELEASED if (report.sent_to_doctor or report.printed) else OrderStage.ENTERED,
            collected_at=report.sample_date,
            created_at=report.created_at,
        )

        old_results = list(report.results.all())

        if test.result_type == ResultType.PARAMETER_PANEL:
            result = LabResult.objects.create(order=order, result_type=ResultType.PARAMETER_PANEL, entered_at=report.created_at)
            params_by_name = {p.name.strip().lower(): p for p in test.parameters.all()}
            for i, old in enumerate(old_results):
                param = params_by_name.get((old.test.name or "").strip().lower())
                num = _to_decimal(old.result_value)
                ResultValue.objects.create(
                    result=result, parameter=param, label=old.test.name,
                    value_num=num, value_text="" if num is not None else (old.result_value or ""),
                    unit=old.unit, ref_display=old.reference_range, comment=old.comment,
                    sort_order=i,
                )

        elif test.result_type == ResultType.DEFINED_OPTION:
            chosen = old_results[0].result_value if old_results else ""
            LabResult.objects.create(order=order, result_type=ResultType.DEFINED_OPTION, chosen_option=chosen, entered_at=report.created_at)

        elif test.result_type == ResultType.CULTURE:
            result = LabResult.objects.create(order=order, result_type=ResultType.CULTURE, entered_at=report.created_at)
            for i, old in enumerate(old_results):
                ResultValue.objects.create(result=result, label=old.test.name, value_code=old.result_value or "", sort_order=i)

        else:  # FREE_ENTRY
            free_text = "\n".join(f"{r.test.name}: {r.result_value}" for r in old_results)
            LabResult.objects.create(order=order, result_type=ResultType.FREE_ENTRY, free_text=free_text, entered_at=report.created_at)
