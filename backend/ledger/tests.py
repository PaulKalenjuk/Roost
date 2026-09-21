"""Regression tests for the Airbnb ledger.

Run with::

    DB_ENGINE=sqlite python manage.py test ledger
"""
from decimal import Decimal
import datetime as dt
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import depreciation, reports
from .coverage import coverage_for
from .importers import airbnb_csv, airbnb_pdf
from .models import (
    Asset,
    Category,
    Expense,
    Listing,
    MonthlyEarnings,
    Owner,
    Property,
    PropertyOwnership,
    Receipt,
    Reservation,
    UtilityBill,
    UtilityType,
)
from .services import bill_extract

SAMPLE_CSV = """Date,Type,Confirmation Code,Listing,Guest,Start Date,End Date,Nights,Gross Earnings,Cleaning Fee,Service Fee,Payout Date,Amount,Currency
2026-01-05,Reservation,HMABC123,Seaside Shack,Jane Doe,2026-01-10,2026-01-14,4,900.00,120.00,135.00,2026-01-05,-885.00,AUD
2026-02-01,Reservation,HMXYZ999,Seaside Shack,John Smith,2026-02-10,2026-02-12,2,500.00,80.00,75.00,2026-02-01,-505.00,AUD
"""


class BaseLedgerTestCase(TestCase):
    def setUp(self):
        self.prop = Property.objects.create(
            name="Beach House",
            address="1 Shoreline Rd",
            total_floor_area_sqm=Decimal("200.00"),
            rental_floor_area_sqm=Decimal("50.00"),
        )
        self.listing = Listing.objects.create(
            property=self.prop, name="Seaside Shack", external_id="12345"
        )


class PropertyApportionmentTests(BaseLedgerTestCase):
    def test_rental_area_share(self):
        self.assertEqual(self.prop.rental_area_share, Decimal("0.250000"))

    def test_missing_area_returns_none(self):
        prop = Property.objects.create(name="No areas")
        self.assertIsNone(prop.rental_area_share)


class AirbnbCsvImportTests(BaseLedgerTestCase):
    def test_import_creates_reservations(self):
        batch = airbnb_csv.import_csv(SAMPLE_CSV, self.listing, filename="sample.csv")
        self.assertEqual(batch.rows_total, 2)
        self.assertEqual(batch.rows_created, 2)
        self.assertEqual(Reservation.objects.count(), 2)

        r = Reservation.objects.get(confirmation_code="HMABC123")
        self.assertEqual(r.nights, 4)
        self.assertEqual(r.gross_earnings, Decimal("900.00"))
        self.assertEqual(r.airbnb_fee, Decimal("135.00"))
        self.assertEqual(r.net_payout, Decimal("885.00"))
        self.assertEqual(str(r.check_in), "2026-01-10")

    def test_import_is_idempotent(self):
        airbnb_csv.import_csv(SAMPLE_CSV, self.listing)
        batch = airbnb_csv.import_csv(SAMPLE_CSV, self.listing)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(batch.rows_created, 0)

    def test_blank_confirmation_code_is_skipped(self):
        csv_text = "Confirmation Code,Gross Earnings\n,100.00\n"
        batch = airbnb_csv.import_csv(csv_text, self.listing)
        self.assertEqual(batch.rows_skipped, 1)
        self.assertEqual(Reservation.objects.count(), 0)


class ExpenseApportionmentTests(BaseLedgerTestCase):
    def setUp(self):
        super().setUp()
        self.cat = Category.objects.create(
            name="Electricity",
            kind=Category.KIND_UTILITY,
            default_apportionment=Category.APPORTION_AREA,
        )

    def test_area_apportionment(self):
        exp = Expense.objects.create(
            property=self.prop,
            category=self.cat,
            amount=Decimal("400.00"),
            apportionment=Category.APPORTION_AREA,
        )
        self.assertEqual(exp.deductible_amount, Decimal("100.00"))

    def test_custom_apportionment(self):
        exp = Expense.objects.create(
            property=self.prop,
            category=self.cat,
            amount=Decimal("300.00"),
            apportionment=Category.APPORTION_CUSTOM,
            apportionment_pct=Decimal("0.4000"),
        )
        self.assertEqual(exp.deductible_amount, Decimal("120.00"))

    def test_no_apportionment_is_fully_deductible(self):
        exp = Expense.objects.create(
            property=self.prop,
            category=self.cat,
            amount=Decimal("250.00"),
            apportionment=Category.APPORTION_NONE,
        )
        self.assertEqual(exp.deductible_amount, Decimal("250.00"))


class DepreciationTests(BaseLedgerTestCase):
    def test_diminishing_value_schedule(self):
        asset = Asset.objects.create(
            property=self.prop,
            name="Reverse-cycle aircon",
            purchase_date="2025-08-01",
            cost=Decimal("2000.00"),
            effective_life_years=Decimal("10.00"),
            method=Asset.METHOD_DIMINISHING,
            business_use_pct=Decimal("0.2500"),
        )
        entries = depreciation.recompute_schedule(asset)
        self.assertTrue(entries)
        self.assertEqual(entries[0].financial_year, "FY2025-26")
        self.assertGreater(entries[0].deduction, 0)
        # closing value is opening less the deduction
        self.assertEqual(
            entries[0].closing_value,
            entries[0].opening_value - entries[0].deduction,
        )
        # only one entry per FY
        self.assertEqual(
            asset.depreciation_entries.filter(financial_year="FY2025-26").count(), 1
        )


# Layout-identical to a real Airbnb earnings report, with synthetic figures.

SAMPLE_REPORT_TEXT = """888 Brannan Street
San Francisco, CA 94103
Airbnb tax ID number: 26-3051428
Host name: Test Host
User ID: 38028699
Report generated: 21 September 2026
1 July 2025 - 30 June 2026
Earnings report
Summary Gross earnings Adjustments1 Service fees2 Tax withheld3 Total (AUD)
Earnings $100.00 AUD $0.00 AUD -$3.00 AUD $0.00 AUD $97.00 AUD
Performance stats
Nights booked
10
Avg. night stay
4.1
Homes
Home Gross earnings Adjustments1 Service fees2 Tax withheld3 Total (AUD)
Test Listing $100.00 AUD $0.00 AUD -$3.00 AUD $0.00 AUD $97.00 AUD
Earnings types
Types Total (AUD)
Homes $97.00 AUD
Reporting period
Month Gross earnings Total (AUD)
July 2025 $50.00 AUD $48.50 AUD
August 2025 $50.00 AUD $48.50 AUD
"""


class AirbnbPdfImportTests(BaseLedgerTestCase):
    def test_parse_report_text(self):
        parsed = airbnb_pdf.parse_report_text(SAMPLE_REPORT_TEXT)
        self.assertEqual(str(parsed["period_start"]), "2025-07-01")
        self.assertEqual(str(parsed["period_end"]), "2026-06-30")
        self.assertEqual(parsed["host_name"], "Test Host")
        self.assertEqual(len(parsed["months"]), 2)
        self.assertEqual(str(parsed["months"][0]["month"]), "2025-07-01")
        self.assertEqual(parsed["months"][0]["gross_earnings"], Decimal("50.00"))
        self.assertEqual(parsed["months"][0]["total_earnings"], Decimal("48.50"))
        self.assertEqual(parsed["summary"]["nights_booked"], 10)

    def test_import_creates_monthly_totals(self):
        batch = airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        self.assertEqual(batch.rows_created, 2)
        self.assertEqual(MonthlyEarnings.objects.count(), 2)
        july = MonthlyEarnings.objects.get(listing=self.listing, month="2025-07-01")
        self.assertEqual(july.gross_earnings, Decimal("50.00"))
        self.assertEqual(july.service_fees, Decimal("1.50"))
        self.assertEqual(july.total_earnings, Decimal("48.50"))

    def test_reimport_overwrites_existing_month(self):
        airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        updated_text = SAMPLE_REPORT_TEXT.replace("$50.00 AUD $48.50", "$80.00 AUD $77.60")
        batch = airbnb_pdf.import_report(updated_text, self.listing)
        self.assertEqual(batch.rows_updated, 2)
        self.assertEqual(MonthlyEarnings.objects.count(), 2)  # not duplicated
        july = MonthlyEarnings.objects.get(listing=self.listing, month="2025-07-01")
        self.assertEqual(july.gross_earnings, Decimal("80.00"))


class OwnerReportTests(BaseLedgerTestCase):
    def setUp(self):
        super().setUp()
        self.alice = Owner.objects.create(name="Alice")
        self.bob = Owner.objects.create(name="Bob")
        PropertyOwnership.objects.create(property=self.prop, owner=self.alice, share_pct=Decimal("0.6000"))
        PropertyOwnership.objects.create(property=self.prop, owner=self.bob, share_pct=Decimal("0.4000"))

        airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)

        cat = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)
        Expense.objects.create(
            property=self.prop, category=cat, date="2026-03-01",
            amount=Decimal("400.00"), apportionment=Category.APPORTION_AREA,
        )

    def test_ownership_totals_to_one(self):
        self.assertEqual(self.prop.ownership_total, Decimal("1.0000"))

    def test_report_splits_by_owner(self):
        report, owners = reports.owner_reports(self.prop, "FY2025-26")
        self.assertEqual(report["income"]["total_earnings"], Decimal("97.00"))
        # 25% let share (50/200 m2) => 400 * 0.25 = 100 deductible
        self.assertEqual(report["expenses"]["total_deductible"], Decimal("100.00"))

        by_name = {o["owner"]: o for o in owners}
        self.assertEqual(by_name["Alice"]["income"]["total_earnings"], Decimal("58.20"))
        self.assertEqual(by_name["Alice"]["expenses_deductible"], Decimal("60.00"))
        self.assertEqual(by_name["Bob"]["income"]["total_earnings"], Decimal("38.80"))
        self.assertEqual(by_name["Bob"]["expenses_deductible"], Decimal("40.00"))

    def test_let_percentage_overrides_area(self):
        self.prop.let_percentage = Decimal("0.5000")
        self.prop.save()
        self.assertEqual(self.prop.let_share, Decimal("0.5000"))


class UtilityBillTests(BaseLedgerTestCase):
    def setUp(self):
        super().setUp()
        self.cat = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)
        self.ut = UtilityType.objects.create(
            property=self.prop, name="Electricity", category=self.cat,
            frequency=UtilityType.FREQ_QUARTERLY,
            apportionment=Category.APPORTION_AREA,
        )

    def test_bill_creates_linked_expense(self):
        bill = UtilityBill.objects.create(
            utility_type=self.ut, bill_date="2026-03-15", amount=Decimal("600.00")
        )
        bill.refresh_from_db()
        self.assertIsNotNone(bill.expense_id)
        self.assertEqual(bill.expense.kind, Expense.KIND_UTILITY)
        self.assertEqual(bill.expense.apportionment, Category.APPORTION_AREA)
        # 25% let share of the 200/50 m2 property => 150 claimable
        self.assertEqual(bill.expense.deductible_amount, Decimal("150.00"))

    def test_bill_update_keeps_one_expense(self):
        bill = UtilityBill.objects.create(
            utility_type=self.ut, bill_date="2026-03-15", amount=Decimal("600.00")
        )
        bill.amount = Decimal("800.00")
        bill.save()
        self.assertEqual(Expense.objects.filter(kind=Expense.KIND_UTILITY).count(), 1)
        bill.refresh_from_db()
        self.assertEqual(bill.expense.deductible_amount, Decimal("200.00"))


class BillExtractionTests(TestCase):
    def test_no_text_layer_reports_needs_ocr(self):
        with mock.patch.object(bill_extract, "extract_pdf_text", return_value=""):
            data = bill_extract.extract_bill(b"x", filename="scan.pdf")
        self.assertTrue(data["needs_ocr"])

    @override_settings(DEEPSEEK_KEY="***")
    def test_extract_coerces_fields(self):
        fake = {
            "amount": "$1,234.56",
            "gst_amount": "112.23",
            "bill_date": "15/03/2026",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "supplier": "AGL",
            "utility_hint": "Electricity",
            "currency": "aud",
        }
        with mock.patch.object(bill_extract, "extract_pdf_text", return_value="bill text"), \
             mock.patch.object(bill_extract, "_call_deepseek", return_value=fake):
            data = bill_extract.extract_bill(b"x", filename="bill.pdf")
        self.assertEqual(data["amount"], "1234.56")
        self.assertEqual(data["bill_date"], "2026-03-15")
        self.assertEqual(data["period_end"], "2026-03-31")
        self.assertEqual(data["utility_hint"], "electricity")
        self.assertEqual(data["currency"], "AUD")
        self.assertEqual(data["extracted_by"], "deepseek:deepseek-flash")


class ApiAuthTests(TestCase):
    def test_endpoints_require_login(self):
        response = self.client.get("/api/properties/")
        self.assertIn(response.status_code, (401, 403))

    def test_login_then_list(self):
        User.objects.create_user("tester", "t@example.com", "s3cret-pw")
        response = self.client.post(
            "/api/auth/login/",
            {"username": "tester", "password": "s3cret-pw"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/properties/").status_code, 200)

    def test_bad_login_rejected(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": "nobody", "password": "wrong"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class ApiBlankStringTests(TestCase):
    """A browser form sends "" for blank optional fields; DRF must not choke."""

    def setUp(self):
        User.objects.create_user("tester", "t@example.com", "s3cret-pw")
        self.client.login(username="tester", password="s3cret-pw")

    def test_blank_optional_numeric_and_date_fields_accepted(self):
        response = self.client.post(
            "/api/properties/",
            {
                "name": "Blank test",
                "purchase_date": "",
                "purchase_price": "",
                "let_percentage": "",
                "total_floor_area_sqm": "200.00",
                "rental_floor_area_sqm": "50.00",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["purchase_date"])
        self.assertIsNone(response.json()["purchase_price"])

    def test_ownership_percent_round_trip(self):
        prop = Property.objects.create(name="Owned")
        owner = Owner.objects.create(name="Alice")
        response = self.client.post(
            "/api/ownerships/",
            {"property": prop.id, "owner": owner.id, "share_pct": "0.6000"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.json()["share_pct"]), Decimal("0.6000"))

    def test_category_create_exactly_as_the_picker_sends(self):
        """The UI's inline "+ Add new category" posts this shape."""
        response = self.client.post(
            "/api/categories/",
            {"name": "Electricity", "kind": "utility", "default_apportionment": "area"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["kind"], "utility")
        self.assertEqual(response.json()["default_apportionment"], "area")

    def test_category_name_is_unique(self):
        Category.objects.create(name="Water")
        response = self.client.post(
            "/api/categories/",
            {"name": "Water", "kind": "utility", "default_apportionment": "area"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json())


class ReceiptUploadTests(BaseLedgerTestCase):
    """Receipts land under MEDIA_ROOT and are only served to logged-in users."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        user = User.objects.create_user("tester", "t@example.com", "unused-pw")
        self.client.force_login(user)

    def _upload(self):
        upload = SimpleUploadedFile(
            "receipt.pdf", b"%PDF-1.4 fake receipt", content_type="application/pdf"
        )
        response = self.client.post(
            "/api/receipts/",
            {"property": self.prop.id, "file": upload, "original_name": "receipt.pdf"},
        )
        self.assertEqual(response.status_code, 201, response.content)
        return Receipt.objects.get()

    def test_upload_lands_in_media_root(self):
        receipt = self._upload()
        self.assertTrue(receipt.file.name.startswith("receipts/"))
        self.assertTrue(Path(self.tmp).joinpath(receipt.file.name).exists())
        self.assertEqual(receipt.original_name, "receipt.pdf")

    def test_media_requires_login(self):
        receipt = self._upload()
        self.client.logout()
        response = self.client.get("/media/" + receipt.file.name)
        self.assertEqual(response.status_code, 403)

    def test_media_served_when_authenticated(self):
        receipt = self._upload()
        response = self.client.get("/media/" + receipt.file.name)
        self.assertEqual(response.status_code, 200)


class UtilityCoverageTests(BaseLedgerTestCase):
    """Coverage windows: what is billed, and where the gaps are."""

    def setUp(self):
        super().setUp()
        self.prop.purchase_date = dt.date(2025, 7, 1)
        self.prop.save()
        cat = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)
        self.ut = UtilityType.objects.create(
            property=self.prop, name="Electricity", category=cat,
            frequency=UtilityType.FREQ_QUARTERLY,
        )

    def _bill(self, start, end, amount="100.00"):
        return UtilityBill.objects.create(
            utility_type=self.ut, bill_date=end,
            period_start=dt.date.fromisoformat(start),
            period_end=dt.date.fromisoformat(end),
            amount=amount,
        )

    def test_full_coverage_has_no_gaps(self):
        self._bill("2025-07-01", "2025-09-30")
        self._bill("2025-10-01", "2025-12-31")
        result = coverage_for(self.ut, today=dt.date(2025, 12, 31))
        self.assertTrue(result["computable"])
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["coverage_pct"], 100.0)
        self.assertEqual(result["covered_days"], result["total_days"])

    def test_gap_is_detected(self):
        self._bill("2025-07-01", "2025-09-30")
        self._bill("2025-10-01", "2025-12-31")
        result = coverage_for(self.ut, today=dt.date(2026, 1, 31))
        self.assertEqual(len(result["gaps"]), 1)
        self.assertEqual(result["gaps"][0]["start"], "2026-01-01")
        self.assertEqual(result["gaps"][0]["end"], "2026-01-31")
        self.assertEqual(result["gaps"][0]["days"], 31)
        self.assertLess(result["coverage_pct"], 100.0)

    def test_overlapping_bills_counted(self):
        self._bill("2025-07-01", "2025-09-30")
        self._bill("2025-09-01", "2025-11-30")
        result = coverage_for(self.ut, today=dt.date(2025, 11, 30))
        self.assertEqual(result["overlaps"], 1)

    def test_bill_without_period_is_flagged(self):
        UtilityBill.objects.create(utility_type=self.ut, bill_date=dt.date(2025, 8, 1), amount="50.00")
        result = coverage_for(self.ut, today=dt.date(2025, 12, 31))
        self.assertEqual(result["bills_missing_period"], 1)
        self.assertEqual(result["coverage_pct"], 0.0)

    def test_not_computable_without_a_start_date(self):
        self.prop.purchase_date = None
        self.prop.save()
        result = coverage_for(self.ut)
        self.assertFalse(result["computable"])
        self.assertIn("purchase date", result["reason"])

    def test_coverage_start_overrides_purchase_date(self):
        self.ut.coverage_start = dt.date(2025, 10, 1)
        self.ut.save()
        self._bill("2025-10-01", "2025-12-31")
        result = coverage_for(self.ut, today=dt.date(2025, 12, 31))
        self.assertEqual(result["start"], "2025-10-01")
        self.assertEqual(result["coverage_pct"], 100.0)

    def test_coverage_api(self):
        self._bill("2025-07-01", "2025-12-31")
        user = User.objects.create_user("cov", "c@example.com", "unused-pw")
        self.client.force_login(user)
        response = self.client.get(f"/api/utilities/coverage/?property={self.prop.id}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["coverage"]), 1)
        self.assertEqual(payload["coverage"][0]["name"], "Electricity")


class UtilityBillEditTests(BaseLedgerTestCase):
    """Bills can be edited and deleted; the ledger expense follows along."""

    def setUp(self):
        super().setUp()
        cat = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)
        self.ut = UtilityType.objects.create(
            property=self.prop, name="Electricity", category=cat,
            frequency=UtilityType.FREQ_QUARTERLY,
        )
        user = User.objects.create_user("editor", "e@example.com", "unused-pw")
        self.client.force_login(user)

    def _bill(self, **overrides):
        defaults = dict(
            utility_type=self.ut,
            bill_date=dt.date(2025, 8, 1),
            period_start=dt.date(2025, 7, 1),
            period_end=dt.date(2025, 9, 30),
            amount=Decimal("300.00"),
        )
        defaults.update(overrides)
        return UtilityBill.objects.create(**defaults)

    def test_patch_updates_amount_and_expense(self):
        bill = self._bill()
        response = self.client.patch(
            f"/api/utility-bills/{bill.id}/",
            json.dumps({"amount": "450.00"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        bill.refresh_from_db()
        bill.expense.refresh_from_db()
        self.assertEqual(bill.amount, Decimal("450.00"))
        self.assertEqual(bill.expense.amount, Decimal("450.00"))
        # 25% let share of the 200/50 m2 property
        self.assertEqual(bill.expense.deductible_amount, Decimal("112.50"))

    def test_patch_can_clear_period_dates(self):
        bill = self._bill()
        response = self.client.patch(
            f"/api/utility-bills/{bill.id}/",
            json.dumps({"period_start": None, "period_end": None}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        bill.refresh_from_db()
        self.assertIsNone(bill.period_start)
        self.assertIsNone(bill.period_end)

    def test_multipart_blank_dates_become_none(self):
        """The browser PATCHes multipart/form-data with "" for cleared dates."""
        from django.http import QueryDict

        from .serializers import UtilityBillSerializer

        bill = self._bill()
        data = QueryDict(mutable=True)
        data.update({"period_start": "", "period_end": "", "amount": "123.45"})
        serializer = UtilityBillSerializer(bill, data=data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        bill.refresh_from_db()
        self.assertIsNone(bill.period_start)
        self.assertEqual(bill.amount, Decimal("123.45"))

    def test_delete_removes_bill_and_expense(self):
        bill = self._bill()
        expense_id = bill.expense_id
        response = self.client.delete(f"/api/utility-bills/{bill.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(UtilityBill.objects.filter(pk=bill.pk).exists())
        self.assertFalse(Expense.objects.filter(pk=expense_id).exists())
        self.assertEqual(Expense.objects.filter(kind=Expense.KIND_UTILITY).count(), 0)
