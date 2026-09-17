from django.core.management.base import BaseCommand
from django.db import transaction

from lab.models import DefinedOption, LabTest, ResultType, ServiceCategory
from lab.services_next import clone_lab_test
from reception.models import Service


CBC_MATCH_TERMS = ("complete blood count", "cbc", "full blood count", "fbc")
URINALYSIS_MATCH_TERMS = ("urinalysis", "urine analysis")
STOOL_MATCH_TERMS = ("stool",)

# Cloned from the real curated starter template -- these have real
# structure (parameters, ranges) worth copying.
TEMPLATE_MATCHES = (
    ("Complete Blood Count", CBC_MATCH_TERMS),
    ("Urinalysis", URINALYSIS_MATCH_TERMS),
)

# No structure to clone (free text has none), so these are just created
# directly with the right result type instead of matched to a template.
FREE_ENTRY_MATCHES = (STOOL_MATCH_TERMS,)


def _matches(service_name, terms):
    lname = service_name.lower()
    return any(term in lname for term in terms)


class Command(BaseCommand):
    help = (
        "One-time cleanup for lab services that existed before the new lab engine: "
        "links every unlinked Service(category=lab) to a LabTest so billing it actually "
        "routes into the Lab Queue, instead of sitting billed-but-unworkable behind a "
        "'not yet configured' warning. CBC and Urinalysis get matched to the real "
        "template (cloned into the hospital's own catalog if it doesn't have one yet); "
        "Stool Analysis gets a Free Entry test, matching how it already works on the live "
        "legacy engine; everything else gets a simple Positive/Negative predefined-result "
        "test, matching how most other existing services already work. Edit any of these "
        "afterward if the default doesn't fit. Safe to re-run: already-linked services are "
        "left alone."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would happen without changing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        services = (
            Service.objects.filter(category=Service.CATEGORY_LAB, lab_test_next__isnull=True)
            .select_related("hospital")
            .order_by("hospital__name", "name")
        )
        if not services.exists():
            self.stdout.write(self.style.SUCCESS("Nothing to do -- every lab service is already linked."))
            return

        matched_to_template = []
        created_free_entry = []
        created_predefined = []

        with transaction.atomic():
            for service in services:
                hospital = service.hospital
                test = self._find_template_match(service.name, hospital)
                if test is not None:
                    matched_to_template.append((hospital.name, service.name, test.name))
                elif _matches(service.name, STOOL_MATCH_TERMS):
                    test = self._get_or_create_free_entry(service, hospital)
                    created_free_entry.append((hospital.name, service.name, test.name))
                else:
                    test = self._get_or_create_predefined(service, hospital)
                    created_predefined.append((hospital.name, service.name, test.name))

                service.lab_test_next = test
                service.save(update_fields=["lab_test_next"])

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Matched to an existing template ({len(matched_to_template)}):"))
        for hosp, svc, test_name in matched_to_template:
            self.stdout.write(f'  {hosp} / "{svc}" -> {test_name}')

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created as Free Entry ({len(created_free_entry)}):"))
        for hosp, svc, test_name in created_free_entry:
            self.stdout.write(f'  {hosp} / "{svc}" -> {test_name}')

        self.stdout.write("")
        self.stdout.write(self.style.WARNING(
            f"Created a new predefined-result test ({len(created_predefined)}) -- review these:"
        ))
        for hosp, svc, test_name in created_predefined:
            self.stdout.write(f'  {hosp} / "{svc}" -> {test_name} (Positive/Negative -- edit under Lab Management if that\'s not right)')

        if dry_run:
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE("Dry run -- nothing was actually saved."))

    def _find_template_match(self, service_name, hospital):
        for template_name, terms in TEMPLATE_MATCHES:
            if not _matches(service_name, terms):
                continue
            existing = LabTest.objects.filter(hospital=hospital, name=template_name).first()
            if existing:
                return existing
            starter = LabTest.objects.filter(name=template_name, is_starter_template=True).first()
            if starter:
                return clone_lab_test(starter, hospital)
        return None

    def _get_or_create_free_entry(self, service, hospital):
        existing = LabTest.objects.filter(hospital=hospital, name=service.name).first()
        if existing:
            return existing
        category, _ = ServiceCategory.objects.get_or_create(name="General")
        return LabTest.objects.create(
            hospital=hospital, name=service.name, category=category,
            result_type=ResultType.FREE_ENTRY,
        )

    def _get_or_create_predefined(self, service, hospital):
        existing = LabTest.objects.filter(hospital=hospital, name=service.name).first()
        if existing:
            return existing
        category, _ = ServiceCategory.objects.get_or_create(name="General")
        test = LabTest.objects.create(
            hospital=hospital, name=service.name, category=category,
            result_type=ResultType.DEFINED_OPTION,
        )
        DefinedOption.objects.create(test=test, label="Positive", sort_order=1, is_abnormal=True)
        DefinedOption.objects.create(test=test, label="Negative", sort_order=2, is_abnormal=False)
        return test
