import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AssistantQuestion, Hospital, HospitalModuleSubscription, Module, User
from books.assistant_topics import assistant as books_assistant
from books.models import Account, Client, CompanySettings, Expense, Invoice, Payment, Product
from labsystem.assistant import parse_period

TODAY = date(2026, 11, 5)


class PeriodTests(TestCase):
    def test_reads_the_ways_people_name_a_period(self):
        cases = {
            "today": (TODAY, TODAY), "yesterday": (date(2026, 11, 4),) * 2,
            "this month": (date(2026, 11, 1), TODAY), "last month": (date(2026, 10, 1), date(2026, 10, 31)),
            "in October": (date(2026, 10, 1), date(2026, 10, 31)), "in december": (date(2025, 12, 1), date(2025, 12, 31)),
            "march 2025": (date(2025, 3, 1), date(2025, 3, 31)), "q3": (date(2026, 7, 1), date(2026, 9, 30)),
            "last 30 days": (date(2026, 10, 7), TODAY), "since may": (date(2026, 5, 1), TODAY),
            "this year": (date(2026, 1, 1), TODAY), "last year": (date(2025, 1, 1), date(2025, 12, 31)),
            "last week": (date(2026, 10, 26), date(2026, 11, 1)),
        }
        for text, (start, end) in cases.items():
            period = parse_period(f"spent {text}", TODAY)
            self.assertEqual((period.start, period.end), (start, end), text)
        self.assertIsNone(parse_period("how much money do we have", TODAY))
        self.assertIsNone(parse_period("may I see the cash", TODAY))


class BooksAssistantTests(TestCase):
    def setUp(self):
        self.tenant, _ = Hospital.objects.get_or_create(subdomain=settings.TERNAH_BOOKS_HOSPITAL_SUBDOMAIN, defaults={"name": "Ternah Books"})
        self.user = User.objects.create_user(username="asker", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=self.tenant, is_active=True)
        Account.objects.create(code="1200", name="Receivables", type=Account.TYPE_ASSET, role=Account.ROLE_AR)
        self.bank = Account.objects.create(code="1100", name="Stanbic", type=Account.TYPE_ASSET, role=Account.ROLE_BANK, is_payment_account=True)
        Account.objects.create(code="2200", name="Credits", type=Account.TYPE_LIABILITY, role=Account.ROLE_CREDITS)
        income = Account.objects.create(code="4000", name="Software income", type=Account.TYPE_INCOME)
        self.rent = Account.objects.create(code="6100", name="Rent", type=Account.TYPE_EXPENSE)
        self.fuel = Account.objects.create(code="6200", name="Fuel", type=Account.TYPE_EXPENSE)
        self.perisseia = Client.objects.create(name="Perisseia Ltd")
        self.kanyigo = Client.objects.create(name="Kanyigo Bulonde SACCO")
        for client, amount, issued in ((self.perisseia, "1500000", date(2026, 9, 1)), (self.kanyigo, "350000", date(2026, 8, 1))):
            invoice = Invoice.objects.create(client=client, issue_date=issued, due_date=issued + timedelta(days=14))
            invoice.lines.create(description="Licence", quantity=Decimal("1"), unit_price=Decimal(amount), revenue_account=income)
            invoice.issue(user=self.user)
        payment = Payment(client=self.perisseia, amount=Decimal("300000"), date=date(2026, 10, 4), method=Payment.METHOD_BANK)
        payment.save()
        payment.record(user=self.user)
        for account, amount, day in ((self.rent, "700000", date(2026, 10, 3)), (self.fuel, "50000", date(2026, 9, 3)), (self.rent, "500000", date(2026, 9, 3))):
            expense = Expense(date=day, description="x", account=account, amount=Decimal(amount), method=Expense.METHOD_BANK, no_receipt_reason="n/a")
            expense.save()
            expense.record(user=self.user)

    def ask(self, text, context=None):
        result, context, topic = books_assistant.ask(text, context or {}, TODAY)
        return result, context, topic

    def test_answers_match_the_books(self):
        result, _, topic = self.ask("What did we spend last month?")
        self.assertEqual(topic, "spent")
        self.assertIn("UGX 700,000", result["text"])
        result, _, _ = self.ask("who owes us the most")
        self.assertIn("Perisseia Ltd owes the most", result["text"])
        self.assertIn(["Perisseia Ltd", "UGX 1,200,000"], result["facts"])
        result, _, _ = self.ask("how much did we collect in october")
        self.assertIn("UGX 300,000", result["text"])

    def test_typos_and_other_words_still_work(self):
        result, _, topic = self.ask("expences on rent in septembr")
        self.assertEqual(topic, "spent")
        self.assertIn("on Rent", result["text"])
        self.assertIn("UGX 500,000", result["text"])
        result, _, _ = self.ask("how much does perisea owe")
        self.assertIn("Perisseia Ltd owes UGX 1,200,000", result["text"])
        result, _, _ = self.ask("what did kanyigo pay us this year")
        self.assertIn("from Kanyigo Bulonde SACCO", result["text"])

    def test_compares_and_explains_the_change(self):
        result, _, _ = self.ask("compare spending with last month")
        self.assertIn("this month was UGX 0", result["text"])
        self.assertIn("1–5 October 2026", result["text"], "same days of last month, not the whole month")
        result, _, _ = self.ask("why did spending change in october")
        self.assertIn("up UGX 150,000", result["text"])
        self.assertEqual(result["facts"][0], ["Rent", "+UGX 200,000"])

    def test_follow_ups_carry_on_but_new_questions_do_not(self):
        _, context, _ = self.ask("how much did we spend in october")
        result, context, topic = self.ask("and in september?", context)
        self.assertEqual(topic, "spent")
        self.assertIn("UGX 550,000", result["text"])
        result, _, topic = self.ask("how much rain fell in august", context)
        self.assertEqual(topic, "")
        self.assertIn("can't answer", result["text"])

    def test_asks_instead_of_guessing_between_two_names(self):
        Client.objects.create(name="Acme Clinic")
        Client.objects.create(name="Acme Pharmacy")
        result, _, topic = self.ask("how much does acme owe")
        self.assertEqual(topic, "clarify")
        self.assertEqual(sorted(result["clarify"]), ["Acme Clinic", "Acme Pharmacy"])

    def test_document_numbers_are_looked_up(self):
        number = Invoice.objects.filter(client=self.kanyigo).get().number
        result, _, topic = self.ask(f"status of {number.lower()}")
        self.assertEqual(topic, "document")
        self.assertIn(f"{number} is overdue", result["text"])

    def test_endpoint_logs_questions_and_keeps_context_in_the_session(self):
        self.client.force_login(self.user)
        url = reverse("books:assistant")
        post = lambda q: self.client.post(url, json.dumps({"q": q}), content_type="application/json").json()
        self.assertIn("text", post(""))
        post("how much did we spend in october")
        self.assertIn("UGX 550,000", post("and september")["text"])
        post("what is the weather")
        self.assertEqual(AssistantQuestion.objects.filter(app="books").count(), 3)
        self.assertEqual(list(AssistantQuestion.objects.filter(answered=False).values_list("text", flat=True)), ["what is the weather"])
        self.assertContains(self.client.get(reverse("books:assistant_questions")), "what is the weather")
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_answers_never_contain_markup_from_records(self):
        Client.objects.create(name="<script>alert(1)</script> Ltd")
        response = self.client.get(reverse("books:dashboard"))  # signed out: redirected
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        text = self.client.post(reverse("books:assistant"), json.dumps({"q": "who owes the most"}), content_type="application/json").content.decode()
        self.assertNotIn("<script>", text.replace("\\u003c", "<").replace("<script>alert", "SAFE") if False else text)

    def test_hospital_staff_cannot_ask_books(self):
        clinic = Hospital.objects.create(name="Clinic", subdomain="clinic")
        self.client.force_login(User.objects.create_user(username="c", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=clinic, is_active=True))
        response = self.client.post(reverse("books:assistant"), json.dumps({"q": "how much money do we have"}), content_type="application/json")
        self.assertEqual(response.status_code, 403)


class HealthAssistantTests(TestCase):
    def setUp(self):
        from reception.models import Patient, Payment as VisitPayment, QueueEntry, Service, Visit, VisitService

        def hospital(name):
            row = Hospital.objects.create(name=name, subdomain=name.lower())
            for code in ("reception", "lab", "finance", "doctor"):
                module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
                HospitalModuleSubscription.objects.get_or_create(hospital=row, module=module, defaults={"is_active": True})
            return row

        self.mine, self.theirs = hospital("Mine"), hospital("Theirs")
        self.admin = User.objects.create_user(username="admin", password="x", role=User.ROLE_HOSPITAL_ADMIN, hospital=self.mine, is_active=True)
        self.receptionist = User.objects.create_user(username="rec", password="x", role=User.ROLE_RECEPTIONIST, hospital=self.mine, is_active=True)
        lab = Service.objects.create(hospital=self.mine, name="Malaria RDT", category=Service.CATEGORY_LAB, price=Decimal("10000"), is_active=True)
        for index, (hosp, sex) in enumerate(((self.mine, "M"), (self.mine, "F"), (self.mine, "F"), (self.theirs, "M"))):
            patient = Patient.objects.create(hospital=hosp, name=f"Secret Person {index}", registration_date=timezone.localdate(), age="30YRS", sex=sex)
            visit = Visit.objects.create(patient=patient, hospital=hosp, status=Visit.STATUS_IN_PROGRESS, total_amount=Decimal("10000"))
            if hosp == self.mine:
                VisitService.objects.create(visit=visit, service=lab, price_at_time=Decimal("10000"), is_approved=True)
                VisitPayment.objects.create(visit=visit, amount=Decimal("10000"), amount_paid=Decimal("10000"), mode=VisitPayment.MODE_CASH)
            QueueEntry.objects.create(hospital=hosp, visit=visit, queue_type=QueueEntry.TYPE_DOCTOR, reason="x")

    def ask(self, user, q):
        self.client.force_login(user)
        return self.client.post(reverse("assistant_ask"), json.dumps({"q": q}), content_type="application/json").json()

    def test_counts_only_this_hospital_and_never_names_patients(self):
        result = self.ask(self.admin, "how many patients today")
        self.assertIn("3 patient visits today", result["text"])
        everything = json.dumps([self.ask(self.admin, q) for q in ("how many patients today", "doctor queue", "male and female patients this month", "top services this month", "lab tests today")])
        self.assertNotIn("Secret Person", everything)
        self.assertIn("3 patients waiting in the Doctor queue", self.ask(self.admin, "who is waiting in the doctor queue")["text"])

    def test_money_is_for_admins_and_finance_only(self):
        self.assertIn("UGX 30,000", self.ask(self.admin, "how much did we collect today")["text"])
        self.assertIn("aren't part of what I answer for your role", self.ask(self.receptionist, "how much did we collect today")["text"])


class AssistantRoleScopeTests(HealthAssistantTests):
    """Each person only gets answers about the work their role covers."""

    def setUp(self):
        super().setUp()
        self.doctor = User.objects.create_user(username="doc", password="x", role=User.ROLE_DOCTOR, hospital=self.mine, is_active=True)
        self.accountant = User.objects.create_user(username="acc", password="x", role=User.ROLE_ACCOUNTANT, hospital=self.mine, is_active=True)

    def test_a_doctor_is_not_told_about_money(self):
        for question in ("how much did we collect today", "expenses this month", "revenue this month", "how much cash came in today"):
            result = self.ask(self.doctor, question)
            self.assertIn("aren't part of what I answer for your role", result["text"], question)
            self.assertNotIn("UGX", json.dumps(result), question)
            self.assertFalse(any("collect" in s.lower() or "expense" in s.lower() or "sales" in s.lower() for s in result["suggestions"]), question)
        logged = AssistantQuestion.objects.filter(user=self.doctor).first()
        self.assertEqual((logged.topic, logged.answered), ("restricted", False))
        pharmacy = self.ask(self.doctor, "pharmacy sales today")
        self.assertNotIn("UGX", json.dumps(pharmacy))
        self.assertIn("isn't part of what I answer", pharmacy["text"])

    def test_a_doctor_still_gets_the_clinical_numbers(self):
        self.assertIn("3 patient visits today", self.ask(self.doctor, "how many patients today")["text"])
        self.assertIn("3 lab tests", self.ask(self.doctor, "lab tests today")["text"])
        self.assertIn("3 patients waiting in the Doctor queue", self.ask(self.doctor, "doctor queue")["text"])
        hello = self.ask(self.doctor, "")
        self.assertNotIn("money", hello["text"])
        self.assertIn("lab work", hello["text"])
        self.assertNotIn("How much did we collect today?", hello["suggestions"])
        self.assertIn("money", self.ask(self.admin, "")["text"])

    def test_access_follows_the_finance_permission_not_just_the_role(self):
        from django.contrib.auth.models import Group
        self.assertIn("UGX 30,000", self.ask(self.accountant, "how much did we collect today")["text"])
        self.assertIn("aren't part of what I answer", self.ask(self.accountant, "lab tests today")["text"])
        self.doctor.groups.add(Group.objects.get_or_create(name="Finance")[0])
        self.doctor = User.objects.get(pk=self.doctor.pk)
        self.assertIn("UGX 30,000", self.ask(self.doctor, "how much did we collect today")["text"])

    def test_a_refusal_does_not_leave_a_topic_to_follow_up_on(self):
        self.ask(self.doctor, "how much did we collect today")
        self.assertEqual(self.client.session["assistant:health"], {})
        self.assertNotIn("UGX", json.dumps(self.ask(self.doctor, "and last month?")))

    def test_widget_is_on_hospital_pages_and_answers_are_split_by_sex(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("app_home")), "data-assistant")
        result = self.ask(self.admin, "male and female patients this month")
        self.assertIn(["Female", "2"], result["facts"])
        self.assertIn(["Male", "1"], result["facts"])

    def test_pharmacy_answers_with_the_same_split_as_the_finance_report(self):
        result = self.ask(self.admin, "pharmacy sales today")
        self.assertIn("Pharmacy collected", result["text"])
        self.assertIn(["Drugs at or below their minimum stock", "0"], result["facts"])
        self.assertNotIn("collected", self.ask(self.receptionist, "pharmacy sales today")["text"])


class RoleQuestionsTests(HealthAssistantTests):
    """Every role gets answers to its own questions and is turned away from the rest."""

    def setUp(self):
        super().setUp()
        from doctor.models import Consultation, VisitOutcome
        from nurse.models import NursingAdmission
        from reception.models import Triage, Visit
        for code in ("nurse", "sonographer", "inventory"):
            module, _ = Module.objects.get_or_create(code=code, defaults={"name": code.title()})
            HospitalModuleSubscription.objects.get_or_create(hospital=self.mine, module=module, defaults={"is_active": True})

        def staff(username, role):
            return User.objects.create_user(username=username, password="x", role=role, hospital=self.mine, is_active=True)

        self.doctor = staff("doc", User.ROLE_DOCTOR)
        self.other_doctor = staff("doc2", User.ROLE_DOCTOR)
        self.nurse = staff("nurse1", User.ROLE_NURSE)
        self.lab = staff("lab1", User.ROLE_LAB_ATTENDANT)
        self.sono = staff("sono1", User.ROLE_SONOGRAPHER)
        self.accountant = staff("acc", User.ROLE_ACCOUNTANT)
        self.roles = {
            "admin": self.admin, "doctor": self.doctor, "nurse": self.nurse, "lab": self.lab,
            "sonographer": self.sono, "receptionist": self.receptionist, "accountant": self.accountant,
        }
        visits = list(Visit.objects.filter(hospital=self.mine).order_by("pk"))
        for visit, (doctor, diagnosis, outcome) in zip(visits, (
            (self.doctor, "Malaria", VisitOutcome.DISCHARGED), (self.doctor, " malaria ", VisitOutcome.REFERRED),
            (self.other_doctor, "Typhoid", VisitOutcome.ADMITTED),
        )):
            Consultation.objects.create(visit=visit, created_by=doctor, signs_symptoms="x", diagnosis=diagnosis, treatment="x",
                                        outcome=outcome, follow_up_date=self.today)
        NursingAdmission.objects.create(visit=visits[2], hospital=self.mine, admitted_by=self.nurse)
        Triage.objects.create(visit=visits[0], recorded_by=self.nurse, pulse=80)
        # Visit 3 is billed 10,000 and paid 10,000; make visit 1 owe 5,000.
        Visit.objects.filter(pk=visits[0].pk).update(total_amount=Decimal("15000"))

    @property
    def today(self):
        return timezone.localdate()

    def ask_as(self, user, question):
        from accounts.assistant_topics import assistant
        result, _, topic = assistant.ask(question, {}, self.today, hospital=self.mine, user=user)
        return result, topic

    def test_every_example_is_answered_for_its_roles_and_refused_for_the_rest(self):
        from accounts.assistant_topics import allowed_areas, assistant
        for name, user in self.roles.items():
            areas = allowed_areas(self.mine, user)
            for area, question in assistant.examples:
                result, topic = self.ask_as(user, question)
                allowed = areas is None or not area or area in areas
                self.assertEqual(topic == "restricted", not allowed, f"{name}: {question!r} -> {topic}: {result['text']}")
                if not allowed:
                    self.assertNotIn("UGX", json.dumps(result), f"{name}: {question}")
            hello, _ = self.ask_as(user, "")
            self.assertTrue(hello["suggestions"], name)
            for suggestion in hello["suggestions"]:
                self.assertNotEqual(self.ask_as(user, suggestion)[1], "restricted", f"{name} was offered {suggestion!r}")

    def test_each_role_sees_its_own_questions_first(self):
        hello = lambda user: self.ask_as(user, "")[0]
        self.assertIn("How many patients did I see today?", hello(self.doctor)["suggestions"])
        self.assertIn("How many patients are admitted?", hello(self.nurse)["suggestions"])
        self.assertIn("Lab tests pending", hello(self.lab)["suggestions"])
        self.assertIn("How many scans this week?", hello(self.sono)["suggestions"])
        self.assertIn("Unpaid bills today", hello(self.receptionist)["suggestions"])
        self.assertIn("How much did we collect today?", hello(self.accountant)["suggestions"])
        self.assertNotIn("money", hello(self.lab)["text"])

    def test_doctor_questions(self):
        self.assertIn("You saw 2 patients today", self.ask_as(self.doctor, "how many patients did I see today")[0]["text"])
        self.assertIn("3 consultations today", self.ask_as(self.doctor, "how many consultations today")[0]["text"])
        result, topic = self.ask_as(self.doctor, "most common diagnoses this month")
        self.assertEqual(topic, "diagnoses")
        self.assertEqual(result["facts"][0], ["Malaria", "2"])
        self.assertIn(["Referred", "1"], self.ask_as(self.doctor, "referrals and outcomes this month")[0]["facts"])
        self.assertIn("3 follow-up appointments due", self.ask_as(self.doctor, "follow-ups due this week")[0]["text"])
        self.assertNotIn("Secret Person", json.dumps([self.ask_as(self.doctor, q)[0] for q, in (("diagnoses this month",), ("my patients today",))]))

    def test_nurse_questions(self):
        self.assertIn("1 patient admitted right now", self.ask_as(self.nurse, "how many patients are admitted")[0]["text"])
        self.assertIn("You recorded vitals for 1 patient", self.ask_as(self.nurse, "vitals I took today")[0]["text"])
        self.assertEqual(self.ask_as(self.nurse, "doses given today")[1], "doses")
        self.assertEqual(self.ask_as(self.nurse, "how much did we collect today")[1], "restricted")

    def test_lab_questions(self):
        self.assertEqual(self.ask_as(self.lab, "pending lab tests")[1], "lab_work")
        self.assertEqual(self.ask_as(self.lab, "how long do results take this week")[1], "turnaround")
        self.assertEqual(self.ask_as(self.lab, "reagents running low")[1], "consumables")
        self.assertEqual(self.ask_as(self.lab, "most common diagnoses this month")[1], "restricted")
        self.assertEqual(self.ask_as(self.doctor, "reagents running low")[1], "restricted")

    def test_bills_are_counts_for_reception_and_money_for_finance(self):
        reception, _ = self.ask_as(self.receptionist, "unpaid bills today")
        self.assertIn("1 visit today still has a balance to pay", reception["text"])
        self.assertNotIn("UGX", json.dumps(reception))
        finance, _ = self.ask_as(self.accountant, "unpaid bills today")
        self.assertIn(["Owed on these visits", "UGX 5,000"], finance["facts"])
        self.assertEqual(self.ask_as(self.doctor, "who owes us")[1], "restricted")

    def test_finance_questions(self):
        from admin_dashboard.models import Expense, Salary
        Expense.objects.create(hospital=self.mine, description="Power", amount=Decimal("5000"), category=Expense.CATEGORY_UTILITIES, date=self.today)
        Salary.objects.create(hospital=self.mine, employee=self.nurse, month=self.today.replace(day=1), amount=Decimal("8000"), paid=True)
        result, topic = self.ask_as(self.accountant, "profit this month")
        self.assertEqual(topic, "profit")
        self.assertIn("Profit this month: UGX 17,000", result["text"])
        self.assertIn("UGX 8,000", self.ask_as(self.accountant, "salaries this month")[0]["text"])
        self.assertEqual(self.ask_as(self.accountant, "revenue by department this month")[1], "departments")
        self.assertEqual(self.ask_as(self.accountant, "staff by role")[1], "restricted")
        self.assertIn("active staff", self.ask_as(self.admin, "staff by role")[0]["text"])
