import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import Hospital, SubscriptionPlan
from admin_dashboard.forms import ExpenseForm, HospitalForm
from admin_dashboard.models import BankAccount, CashDrawer, InventoryBatch, InventoryItem, InventoryTransaction, MobileMoneyAccount

TEST_MEDIA_ROOT = Path(__file__).resolve().parent.parent / "test_media"
TEST_MEDIA_ROOT.mkdir(exist_ok=True)


def build_test_logo():
    buffer = tempfile.SpooledTemporaryFile()
    image = Image.new("RGBA", (8, 8), color=(29, 53, 87, 255))
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile("logo.png", buffer.read(), content_type="image/png")


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class HospitalFormTests(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="Starter",
            price_monthly="50.00",
            price_yearly="500.00",
            max_users=10,
            max_storage_mb=250,
        )

    def form_data(self, **overrides):
        data = {
            "name": "Kampala Care",
            "subdomain": "kampala-care",
            "location": "Kampala",
            "box_number": "123",
            "phone_number": "+256700000001",
            "email": "admin@kampalacare.com",
            "subscription_plan": self.plan.pk,
            "admin_username": "kampalaadmin",
            "admin_password": "StrongPass123!",
            "admin_password_confirm": "StrongPass123!",
        }
        data.update(overrides)
        return data

    def test_duplicate_admin_username_is_rejected(self):
        User = get_user_model()
        User.objects.create_user(username="kampalaadmin", password="ExistingPass123!", role=User.ROLE_HOSPITAL_ADMIN)
        form = HospitalForm(data=self.form_data())
        self.assertFalse(form.is_valid())
        self.assertIn("admin_username", form.errors)

    def test_password_mismatch_is_rejected(self):
        form = HospitalForm(data=self.form_data(admin_password_confirm="DifferentPass123!"))
        self.assertFalse(form.is_valid())
        self.assertIn("Passwords do not match.", form.non_field_errors())

    def test_weak_password_is_rejected(self):
        form = HospitalForm(data=self.form_data(admin_password="123", admin_password_confirm="123"))
        self.assertFalse(form.is_valid())
        self.assertIn("admin_password", form.errors)

    def test_email_is_optional(self):
        form = HospitalForm(data=self.form_data(email=""))
        self.assertTrue(form.is_valid(), form.errors)

    def test_missing_logo_uses_fallback_property(self):
        hospital = Hospital.objects.create(name="Fallback", subdomain="fallback")
        self.assertEqual(hospital.logo_url, static("images/default_hospital_logo.png"))


@override_settings(
    MEDIA_ROOT=TEST_MEDIA_ROOT,
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class SuperadminHospitalManagementTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.User = get_user_model()
        self.plan = SubscriptionPlan.objects.create(
            name="Growth",
            price_monthly="80.00",
            price_yearly="800.00",
            max_users=25,
            max_storage_mb=500,
        )
        self.superadmin = self.User.objects.create_user(
            username="platformowner",
            password="StrongPass123!",
            role=self.User.ROLE_SUPERADMIN,
        )
        self.client.login(username="platformowner", password="StrongPass123!")

    def payload(self, **overrides):
        data = {
            "name": "Mercy Hospital",
            "subdomain": "mercy",
            "location": "Mbarara",
            "box_number": "88",
            "phone_number": "+256700000002",
            "email": "hello@mercy.test",
            "subscription_plan": str(self.plan.pk),
            "admin_username": "mercyadmin",
            "admin_password": "StrongPass123!",
            "admin_password_confirm": "StrongPass123!",
        }
        data.update(overrides)
        return data

    def test_hospital_creation_creates_hospital_and_admin_user(self):
        payload = self.payload()
        payload["logo"] = build_test_logo()
        response = self.client.post(reverse("manage_hospitals"), data=payload)
        self.assertEqual(response.status_code, 302, response.context["form"].errors.as_json() if response.context else "")
        hospital = Hospital.objects.get(subdomain="mercy")
        admin_user = self.User.objects.get(username="mercyadmin")
        self.assertEqual(admin_user.hospital, hospital)
        self.assertEqual(admin_user.role, self.User.ROLE_HOSPITAL_ADMIN)
        self.assertTrue(hospital.logo.name.startswith("hospital_logos/"))

    def test_failed_user_creation_rolls_back_hospital(self):
        with patch("admin_dashboard.views.User.objects.create_user", side_effect=RuntimeError("boom")):
            response = self.client.post(reverse("manage_hospitals"), data=self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Hospital.objects.filter(subdomain="mercy").exists())
        self.assertContains(response, "Hospital onboarding could not be completed")

    def test_hospital_list_filters_by_name_or_subdomain(self):
        Hospital.objects.create(name="Alpha Hospital", subdomain="alpha")
        Hospital.objects.create(name="Beta Clinic", subdomain="beta-clinic")
        response = self.client.get(reverse("manage_hospitals"), {"q": "beta"})
        self.assertContains(response, "Beta Clinic")
        self.assertNotContains(response, "Alpha Hospital")

    def test_superadmin_templates_render_developer_navigation(self):
        response = self.client.get(reverse("developer_dashboard"))
        self.assertContains(response, "developer workspace")
        self.assertContains(response, reverse("manage_hospitals"))
        self.assertContains(response, reverse("view_audit_logs"))
        self.assertNotContains(response, "Hospital Management")

    def test_superadmin_home_redirects_to_developer_dashboard(self):
        response = self.client.get(reverse("app_home"))
        self.assertRedirects(response, reverse("developer_dashboard"))

    def test_django_superuser_is_normalized_to_superadmin_role(self):
        promoted = self.User.objects.create_superuser(
            username="rootowner",
            password="StrongPass123!",
            email="rootowner@example.com",
        )
        promoted.refresh_from_db()
        self.assertEqual(promoted.role, self.User.ROLE_SUPERADMIN)
        self.assertTrue(promoted.is_superuser)
        self.assertIsNone(promoted.hospital)

        self.client.force_login(promoted)
        response = self.client.get(reverse("app_home"))
        self.assertRedirects(response, reverse("developer_dashboard"))

    def test_hospital_admin_cannot_access_superadmin_dashboard(self):
        hospital = Hospital.objects.create(name="City Care", subdomain="city-care")
        admin_user = self.User.objects.create_user(
            username="cityadmin",
            password="StrongPass123!",
            role=self.User.ROLE_HOSPITAL_ADMIN,
            hospital=hospital,
        )
        self.client.force_login(admin_user)
        response = self.client.get(reverse("developer_dashboard"))
        self.assertEqual(response.status_code, 403)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ExpenseFormSourceTests(TestCase):
    def setUp(self):
        self.hospital = Hospital.objects.create(name="Source Hospital", subdomain="source-hospital")
        self.bank_account = BankAccount.objects.create(
            hospital=self.hospital,
            bank_name="Lumina Bank",
            account_name="Operations",
            account_number="001122",
            opening_balance="1000.00",
        )
        self.mobile_money_account = MobileMoneyAccount.objects.create(
            hospital=self.hospital,
            provider="MTN",
            number="0700000111",
            is_active=True,
        )
        self.cash_drawer = CashDrawer.objects.create(
            hospital=self.hospital,
            opening_balance="200.00",
        )

    def build_payload(self, **overrides):
        payload = {
            "description": "Printer toner",
            "category": "consumables",
            "amount": "45.00",
            "source": "bank_account",
            "bank_account": str(self.bank_account.pk),
            "mobile_money_account": "",
            "cash_drawer": "",
            "notes": "Urgent purchase",
        }
        payload.update(overrides)
        return payload

    def test_bank_source_requires_bank_account(self):
        form = ExpenseForm(
            data=self.build_payload(bank_account=""),
            hospital=self.hospital,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("bank_account", form.errors)

    def test_selected_source_clears_other_accounts(self):
        form = ExpenseForm(
            data=self.build_payload(
                mobile_money_account=str(self.mobile_money_account.pk),
                cash_drawer=str(self.cash_drawer.pk),
            ),
            hospital=self.hospital,
        )
        self.assertTrue(form.is_valid(), form.errors)
        expense = form.save(commit=False)
        self.assertEqual(expense.bank_account, self.bank_account)
        self.assertIsNone(expense.mobile_money_account)
        self.assertIsNone(expense.cash_drawer)


class MultiRoleNavigationTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Union Hospital", subdomain="union-hospital")
        self.user = self.User.objects.create_user(
            username="flexstaff",
            password="StrongPass123!",
            role=self.User.ROLE_RECEPTIONIST,
            hospital=self.hospital,
        )
        nurse_group, _ = Group.objects.get_or_create(name="Nurse")
        lab_group, _ = Group.objects.get_or_create(name="Lab")
        self.user.groups.add(nurse_group, lab_group)
        self.client.force_login(self.user)

    def test_multi_role_user_sees_union_of_module_navigation(self):
        response = self.client.get(reverse("reception_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("reception_dashboard"))
        self.assertContains(response, reverse("patient_list"))
        self.assertContains(response, reverse("nurse_queue"))
        self.assertContains(response, reverse("lab_queue"))
        self.assertContains(response, reverse("report_list"))
        self.assertNotContains(response, reverse("doctor_queue"))
        self.assertContains(response, "Receptionist, Nurse, Lab")


class InventoryManagementTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.hospital = Hospital.objects.create(name="Stock Hospital", subdomain="stock-hospital")
        self.admin_user = self.User.objects.create_user(
            username="stockadmin",
            password="StrongPass123!",
            role=self.User.ROLE_HOSPITAL_ADMIN,
            hospital=self.hospital,
        )
        self.client.force_login(self.admin_user)
        self.item = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Grovit Syrup",
            category=InventoryItem.CATEGORY_SYRUP,
            unit="bottle",
            base_unit="ml",
            units_per_pack="100",
            current_quantity="0",
            unit_cost="5000",
            selling_price="8000",
            reorder_level="5",
        )
        InventoryBatch.objects.create(
            item=self.item,
            batch_number="GVT-001",
            quantity="4",
            expiry_date="2027-12-31",
            unit_cost="5000",
        )
        self.item.recalculate_current_quantity()

    def test_inventory_report_downloads_csv(self):
        response = self.client.get(reverse("download_inventory_report"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn("Grovit Syrup", content)
        self.assertIn("GVT-001", content)

    def test_inventory_import_template_downloads_csv_headers(self):
        response = self.client.get(reverse("download_inventory_import_template"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        content = response.content.decode("utf-8")
        self.assertIn("name,category,unit,base_unit", content)

    def test_printable_inventory_report_renders_stock_sheet(self):
        response = self.client.get(reverse("printable_inventory_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inventory Stock Sheet")
        self.assertContains(response, "Grovit Syrup")
        self.assertContains(response, "Batch details")

    def test_inventory_report_downloads_xlsx(self):
        response = self.client.get(reverse("download_inventory_xlsx"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(response.content.startswith(b"PK"))

    def test_hospital_admin_can_restock_existing_item(self):
        response = self.client.post(
            reverse("restock_inventory_item", args=[self.item.pk]),
            {
                "quantity_received": "12",
                "unit_cost": "5200",
                "batch_number": "GVT-RESTOCK",
                "expiry_date": "2028-06-30",
                "notes": "Supplier delivery",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 16)
        self.assertEqual(self.item.unit_cost, 5200)
        batch = InventoryBatch.objects.get(item=self.item, batch_number="GVT-RESTOCK")
        self.assertEqual(batch.quantity, 12)
        transaction = InventoryTransaction.objects.get(item=self.item, transaction_type=InventoryTransaction.TYPE_RECEIVE)
        self.assertEqual(transaction.quantity, 12)

    def test_hospital_admin_can_bulk_upload_inventory_csv(self):
        csv_content = "\n".join(
            [
                "name,category,unit,base_unit,units_per_pack,strength_mg_per_unit,concentration_mg_per_ml,pack_size_ml,days_covered_per_pack,current_quantity,unit_cost,selling_price,reorder_level,is_active,opening_batch_number,opening_expiry_date",
                "Paracetamol 500mg,drug,strip,tablet,10,500,,,,24,1000,2000,3,True,PCT-001,2027-12-31",
                "Grovit Syrup,syrup,bottle,ml,100,,50,100,,6,5500,8500,5,True,GVT-NEW,2028-01-31",
            ]
        )
        upload = SimpleUploadedFile("inventory-upload.csv", csv_content.encode("utf-8"), content_type="text/csv")

        response = self.client.post(reverse("upload_inventory_bulk"), {"file": upload})

        self.assertEqual(response.status_code, 302)
        new_item = InventoryItem.objects.get(hospital=self.hospital, name="Paracetamol 500mg")
        self.assertEqual(new_item.category, InventoryItem.CATEGORY_DRUG)
        self.assertEqual(new_item.current_quantity, 24)
        self.item.refresh_from_db()
        self.assertEqual(self.item.current_quantity, 10)
        self.assertTrue(self.item.batches.filter(batch_number="GVT-NEW").exists())

    def test_manage_inventory_filters_by_search_and_stock_status(self):
        healthy_item = InventoryItem.objects.create(
            hospital=self.hospital,
            name="Paracetamol 500mg",
            category=InventoryItem.CATEGORY_DRUG,
            unit="strip",
            base_unit="tablet",
            units_per_pack="10",
            current_quantity="40",
            unit_cost="1000",
            selling_price="2000",
            reorder_level="5",
        )

        response = self.client.get(
            reverse("manage_inventory"),
            {"search": "grovit", "stock": "low"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grovit Syrup")
        self.assertContains(response, "Showing")
        self.assertEqual(list(response.context["inventory_items"]), [self.item])
        self.assertEqual(response.context["filtered_inventory_count"], 1)

    def test_manage_inventory_shows_quick_filter_chips(self):
        response = self.client.get(reverse("manage_inventory"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick filters:")
        self.assertContains(response, "Low stock only")
        self.assertContains(response, "Syrups only")
        self.assertContains(response, "Out of stock only")
        self.assertEqual(response.context["inventory_quick_filter_counts"]["syrup"], 1)
