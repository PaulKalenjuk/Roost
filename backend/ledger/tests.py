"""Regression tests for the Airbnb ledger.

Run with::

    DB_ENGINE=sqlite python manage.py test ledger
"""
from decimal import Decimal
import base64
import datetime as dt
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import apportionment, depreciation, reports
from . import pdf as pdf_renderer
from .coverage import coverage_for
from .importers import airbnb_csv, airbnb_pdf
from .models import (
    Asset,
    Category,
    DepreciationEntry,
    EarningsSummary,
    Expense,
    ImportBatch,
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
from .services import asset_extract, bill_extract, expense_extract


def _weasyprint_available():
    try:
        import weasyprint  # noqa: F401

        return True
    except Exception:  # pragma: no cover - environment dependent
        return False

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

    def test_import_creates_period_summary_with_nights(self):
        airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        summary = EarningsSummary.objects.get()
        self.assertEqual(summary.financial_year, "FY2025-26")
        self.assertEqual(summary.nights_booked, 10)
        self.assertEqual(summary.avg_night_stay, Decimal("4.10"))
        self.assertEqual(summary.gross_earnings, Decimal("100.00"))
        self.assertEqual(summary.total_earnings, Decimal("97.00"))

    def test_reimport_overwrites_the_period_summary(self):
        airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        updated = SAMPLE_REPORT_TEXT.replace("Nights booked\n10", "Nights booked\n14")
        airbnb_pdf.import_report(updated, self.listing)
        self.assertEqual(EarningsSummary.objects.count(), 1)  # not duplicated
        self.assertEqual(EarningsSummary.objects.get().nights_booked, 14)

    def test_api_lists_and_edits_nights(self):
        airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        summary = EarningsSummary.objects.get()
        user = User.objects.create_user("nights", "n@example.com", "unused-pw")
        client = APIClient()
        client.force_login(user)

        listed = client.get(f"/api/earnings-summaries/?listing={self.listing.id}")
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(len(listed.json()["results"]), 1)
        self.assertEqual(listed.json()["results"][0]["financial_year"], "FY2025-26")

        patched = client.patch(
            f"/api/earnings-summaries/{summary.id}/",
            {"nights_booked": 149},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(EarningsSummary.objects.get().nights_booked, 149)

    def test_manual_nights_entry_without_an_import(self):
        """A financial year can be recorded by hand, with no PDF at all."""
        user = User.objects.create_user("manual", "m@example.com", "unused-pw")
        client = APIClient()
        client.force_login(user)

        response = client.post(
            "/api/earnings-summaries/",
            {
                "listing": self.listing.id,
                "period_start": "2025-07-01",
                "period_end": "2026-06-30",
                "nights_booked": 149,
                "avg_night_stay": "4.10",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["financial_year"], "FY2025-26")

        summary_id = response.json()["id"]
        deleted = client.delete(f"/api/earnings-summaries/{summary_id}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(EarningsSummary.objects.filter(pk=summary_id).exists())

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


class IncomeReportRetentionTests(BaseLedgerTestCase):
    """The earnings-report PDF is kept with the import — one copy per batch."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)

    def test_import_from_a_path_keeps_the_pdf(self):
        pdf = Path(self.tmp) / "FY2025-26 earnings.pdf"
        pdf.write_bytes(b"%PDF-1.4 the real thing")
        with mock.patch.object(airbnb_pdf, "extract_text", return_value=SAMPLE_REPORT_TEXT):
            batch = airbnb_pdf.import_report(str(pdf), self.listing, filename=str(pdf))

        self.assertTrue(batch.report_file)
        self.assertTrue(batch.report_file.name.startswith("income_reports/"))
        self.assertEqual(
            Path(batch.report_file.path).read_bytes(), b"%PDF-1.4 the real thing"
        )
        self.assertEqual(MonthlyEarnings.objects.count(), 2)
        self.assertIn("pdf retained", batch.log)

    def test_uploaded_pdf_is_kept_with_the_batch(self):
        upload = SimpleUploadedFile(
            "report.pdf", b"%PDF-1.4 upload", content_type="application/pdf"
        )
        with mock.patch.object(airbnb_pdf, "extract_text", return_value=SAMPLE_REPORT_TEXT):
            batch = airbnb_pdf.import_report(upload, self.listing, filename=upload.name)

        self.assertEqual(Path(batch.report_file.path).read_bytes(), b"%PDF-1.4 upload")

    def test_reimport_keeps_every_batch_but_still_overwrites_months(self):
        for name in ("first.pdf", "second.pdf"):
            upload = SimpleUploadedFile(
                name, f"%PDF-1.4 {name}".encode(), content_type="application/pdf"
            )
            with mock.patch.object(
                airbnb_pdf, "extract_text", return_value=SAMPLE_REPORT_TEXT
            ):
                airbnb_pdf.import_report(upload, self.listing, filename=name)

        self.assertEqual(ImportBatch.objects.count(), 2)
        self.assertEqual(ImportBatch.objects.exclude(report_file="").count(), 2)
        self.assertEqual(MonthlyEarnings.objects.count(), 2)  # months overwritten

    def test_text_import_keeps_no_file(self):
        """A text-only import (how the parser tests call it) has nothing to keep."""
        batch = airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        self.assertFalse(batch.report_file)


class IncomePdfApiRetentionTests(BaseLedgerTestCase):
    """The import endpoint hands back a link to the retained PDF (login required)."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        user = User.objects.create_user("importer", "i@example.com", "unused-pw")
        self.client.force_login(user)

    def _import(self):
        upload = SimpleUploadedFile(
            "report.pdf", b"%PDF-1.4 api", content_type="application/pdf"
        )
        with mock.patch(
            "ledger.importers.airbnb_pdf.extract_text", return_value=SAMPLE_REPORT_TEXT
        ):
            return self.client.post(
                "/api/income/import-pdf/",
                {"file": upload, "listing": self.listing.id},
            )

    def test_response_links_to_the_retained_pdf(self):
        response = self._import()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIn("income_reports/", body["report_url"])
        # DRF turns the file into an absolute URL when a request is in context.
        media_path = urlsplit(body["report_file"]).path
        self.assertTrue(media_path.startswith("/media/income_reports/"))
        stored = Path(self.tmp).joinpath(media_path[len("/media/"):])
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), b"%PDF-1.4 api")

    def test_media_link_requires_login(self):
        body = self._import().json()
        self.client.logout()
        media_path = urlsplit(body["report_file"]).path
        self.assertEqual(self.client.get(media_path).status_code, 403)

    def test_imports_endpoint_lists_the_batch_and_its_file(self):
        self._import()
        response = self.client.get(
            f"/api/imports/?listing={self.listing.id}&source=airbnb_pdf"
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertIn("income_reports/", rows[0]["report_url"])


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


# A real (tiny) PNG — WeasyPrint measures the real pixels, so fake bytes won't do.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAJUlEQVR4nGPUr439z0ABYKJE86gB"
    "EMDEQCFgGjWAYTQMGCgPAwB/zAIo4FJbbwAAAABJRU5ErkJggg=="
)


class PropertyImageTests(BaseLedgerTestCase):
    """A property image is stored per property and printed on the FY report PDF."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        user = User.objects.create_user("imgs", "img@example.com", "unused-pw")
        self.api = APIClient()
        self.api.force_login(user)

    def _upload(self):
        upload = SimpleUploadedFile("house.png", ONE_PIXEL_PNG, content_type="image/png")
        response = self.api.patch(
            f"/api/properties/{self.prop.id}/",
            {"name": self.prop.name, "image": upload},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_upload_lands_in_media_and_is_exposed_as_a_url(self):
        body = self._upload()
        self.prop.refresh_from_db()
        self.assertTrue(self.prop.image.name.startswith("properties/"))
        self.assertTrue(Path(self.tmp).joinpath(self.prop.image.name).exists())
        self.assertIn("properties/", body["image_url"])

    def test_report_html_embeds_the_image_when_set(self):
        self._upload()
        self.prop.refresh_from_db()
        report, owners = reports.owner_reports(self.prop, "FY2025-26", True)
        html = pdf_renderer.render_report_html(report, owners, image=self.prop.image)
        # Embedded (self-contained HTML), inside the top-right head block.
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("class='prop-logo'", html)
        self.assertIn("class='doc-head'", html)

    def test_report_html_has_no_image_when_unset(self):
        report, _owners = reports.owner_reports(self.prop, "FY2025-26", False)
        html = pdf_renderer.render_report_html(report, image=self.prop.image)
        # The CSS rule is always present; the element and data URI must not be.
        self.assertNotIn("class='prop-logo'", html)
        self.assertNotIn("data:image", html)

    def test_unreadable_image_file_is_ignored(self):
        """A vanished file must not break rendering."""
        self._upload()
        self.prop.refresh_from_db()
        Path(self.tmp).joinpath(self.prop.image.name).unlink()
        report, _owners = reports.owner_reports(self.prop, "FY2025-26", False)
        html = pdf_renderer.render_report_html(report, image=self.prop.image)
        self.assertNotIn("class='prop-logo'", html)

    def test_clear_image_removes_the_stored_file(self):
        self._upload()
        self.prop.refresh_from_db()
        stored = Path(self.tmp).joinpath(self.prop.image.name)
        response = self.api.post(f"/api/properties/{self.prop.id}/clear-image/")
        self.assertEqual(response.status_code, 200, response.content)
        self.prop.refresh_from_db()
        self.assertFalse(self.prop.image)
        self.assertFalse(stored.exists())
        self.assertIsNone(response.json()["image_url"])

    @unittest.skipUnless(_weasyprint_available(), "weasyprint not installed")
    def test_pdf_puts_the_image_in_the_top_right(self):
        import pdfplumber

        self._upload()
        response = self.api.get(
            f"/api/reports/fy/pdf/?property={self.prop.id}&fy=FY2025-26"
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        with pdfplumber.open(io.BytesIO(response.content)) as doc:
            page = doc.pages[0]
            self.assertTrue(page.images, "expected the logo to be drawn")
            logo = page.images[0]
            self.assertGreater(logo["x0"], page.width / 2)  # right-hand half
            self.assertLess(logo["x1"], page.width)  # inside the margin
            # NB: "top" is measured down from the page top; the y0/y1 pair is
            # bottom-up PDF space, so y0 is near the *bottom*.
            self.assertLess(logo["top"], page.height / 4)  # near the top
            self.assertLess(logo["x1"] - logo["x0"], 60 / 25.4 * 72)  # <= 60mm wide


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

    def test_upload_receipt_against_an_asset(self):
        asset = Asset.objects.create(
            property=self.prop,
            name="Reverse-cycle aircon",
            purchase_date=dt.date(2025, 8, 1),
            cost=Decimal("2000.00"),
            effective_life_years=Decimal("10.00"),
        )
        upload = SimpleUploadedFile(
            "aircon.pdf", b"%PDF-1.4 fake", content_type="application/pdf"
        )
        response = self.client.post(
            "/api/receipts/",
            {"property": self.prop.id, "asset": asset.id, "file": upload,
             "original_name": "aircon.pdf"},
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(asset.receipts.count(), 1)
        self.assertEqual(asset.receipts.first().original_name, "aircon.pdf")


class AssetExtractionTests(TestCase):
    """Receipt → asset fields, with the effective life flagged as an estimate."""

    def test_no_text_layer_reports_needs_ocr(self):
        with mock.patch.object(asset_extract, "extract_pdf_text", return_value=""):
            data = asset_extract.extract_asset(b"x", filename="scan.pdf")
        self.assertTrue(data["needs_ocr"])

    @override_settings(DEEPSEEK_KEY="***")
    def test_extract_coerces_fields_and_flags_estimate(self):
        fake = {
            "name": "Reverse-cycle air conditioner",
            "supplier": "Harvey Norman",
            "cost": "$2,499.00",
            "gst_amount": "227.18",
            "purchase_date": "12/08/2025",
            "asset_kind": "plant_equipment",
            "effective_life_years": "10",
            "suggested_method": "diminishing_value",
            "currency": "aud",
            "notes": "Mitsubishi 7kW",
        }
        with mock.patch.object(asset_extract, "extract_pdf_text", return_value="text"), \
             mock.patch.object(asset_extract, "call_json", return_value=fake):
            data = asset_extract.extract_asset(b"x", filename="receipt.pdf")
        self.assertEqual(data["name"], "Reverse-cycle air conditioner")
        self.assertEqual(data["supplier"], "Harvey Norman")
        self.assertEqual(data["cost"], "2499.00")
        self.assertEqual(data["gst_amount"], "227.18")
        self.assertEqual(data["purchase_date"], "2025-08-12")
        self.assertEqual(data["asset_kind"], "plant_equipment")
        self.assertEqual(data["effective_life_years"], "10")
        self.assertTrue(data["effective_life_is_estimate"])
        self.assertEqual(data["suggested_method"], "diminishing_value")
        self.assertEqual(data["currency"], "AUD")

    def test_invalid_kind_and_absurd_life_are_discarded(self):
        fake = {"asset_kind": "spaceship", "effective_life_years": "500"}
        with mock.patch.object(asset_extract, "extract_pdf_text", return_value="t"), \
             mock.patch.object(asset_extract, "call_json", return_value=fake):
            data = asset_extract.extract_asset(b"x")
        self.assertIsNone(data["asset_kind"])
        self.assertIsNone(data["effective_life_years"])
        self.assertFalse(data["effective_life_is_estimate"])

    def test_extract_endpoint_requires_login(self):
        response = self.client.post("/api/assets/extract/")
        self.assertIn(response.status_code, (401, 403))


class ExpenseExtractionTests(TestCase):
    """Receipt → ad-hoc expense fields, with a category hint matched locally."""

    def test_no_text_layer_reports_needs_ocr(self):
        with mock.patch.object(expense_extract, "extract_pdf_text", return_value=""):
            data = expense_extract.extract_expense(b"x", filename="scan.pdf")
        self.assertTrue(data["needs_ocr"])

    def test_extract_coerces_fields(self):
        fake = {
            "vendor": "Woolworths",
            "description": "Cleaning supplies and bin liners",
            "amount": "$57.40",
            "gst_amount": "5.22",
            "date": "03/09/2025",
            "category_hint": "Cleaning",
            "currency": "aud",
            "notes": "includes mop",
        }
        with mock.patch.object(expense_extract, "extract_pdf_text", return_value="text"), \
             mock.patch.object(expense_extract, "call_json", return_value=fake):
            data = expense_extract.extract_expense(b"x", filename="receipt.pdf")
        self.assertEqual(data["vendor"], "Woolworths")
        self.assertEqual(data["amount"], "57.40")
        self.assertEqual(data["gst_amount"], "5.22")
        self.assertEqual(data["date"], "2025-09-03")
        self.assertEqual(data["category_hint"], "Cleaning")
        self.assertEqual(data["currency"], "AUD")

    def _login(self):
        user = User.objects.create_user("expuser", "e@example.com", "unused-pw")
        api_client = APIClient()
        api_client.force_login(user)
        return api_client

    def test_endpoint_matches_existing_category(self):
        Category.objects.create(name="Cleaning", kind=Category.KIND_OPERATING)
        fake = {"vendor": "Woolworths", "amount": "57.40", "category_hint": "cleaning"}
        client = self._login()
        with mock.patch.object(expense_extract, "extract_pdf_text", return_value="t"), \
             mock.patch.object(expense_extract, "call_json", return_value=fake):
            response = client.post(
                "/api/expenses/extract/",
                {"file": SimpleUploadedFile("r.pdf", b"%PDF-1.4")},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["category_name"], "Cleaning")
        self.assertEqual(body["category"], Category.objects.get().id)

    def test_endpoint_leaves_category_unset_when_no_match(self):
        Category.objects.create(name="Insurance", kind=Category.KIND_OPERATING)
        fake = {"vendor": "Bunnings", "amount": "30.00", "category_hint": "hardware"}
        client = self._login()
        with mock.patch.object(expense_extract, "extract_pdf_text", return_value="t"), \
             mock.patch.object(expense_extract, "call_json", return_value=fake):
            response = client.post(
                "/api/expenses/extract/",
                {"file": SimpleUploadedFile("r.pdf", b"%PDF-1.4")},
                format="multipart",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn("category", response.json())

    def test_extract_endpoint_requires_login(self):
        response = self.client.post("/api/expenses/extract/")
        self.assertIn(response.status_code, (401, 403))


class AssetImageAndDeleteTests(BaseLedgerTestCase):
    """Asset photo upload, and deleting an asset cleans up after itself."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        user = User.objects.create_user("assetuser", "a@example.com", "unused-pw")
        self.api = APIClient()
        self.api.force_login(user)

    @staticmethod
    def _png(name="ac.png"):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return SimpleUploadedFile(name, png, content_type="image/png")

    def _create(self, **extra):
        payload = {
            "property": self.prop.id,
            "name": "Reverse-cycle aircon",
            "purchase_date": "2025-08-01",
            "cost": "2000.00",
            "effective_life_years": "10",
        }
        payload.update(extra)
        response = self.api.post("/api/assets/", payload, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_create_asset_with_image_exposes_url(self):
        body = self._create(image=self._png())
        asset = Asset.objects.get()
        self.assertTrue(asset.image.name.startswith("assets/"))
        self.assertTrue(Path(self.tmp).joinpath(asset.image.name).exists())
        self.assertIn("/media/assets/", body["image_url"])

    def test_edit_keeps_image_when_not_supplied(self):
        body = self._create(image=self._png())
        original = Asset.objects.get().image.name
        response = self.api.patch(
            f"/api/assets/{body['id']}/", {"name": "Aircon v2"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        asset = Asset.objects.get()
        self.assertEqual(asset.name, "Aircon v2")
        self.assertEqual(asset.image.name, original)

    def test_edit_replaces_image(self):
        body = self._create(image=self._png("old.png"))
        self.assertTrue(Asset.objects.get().image.name.endswith("old.png"))
        response = self.api.patch(
            f"/api/assets/{body['id']}/", {"image": self._png("new.png")}, format="multipart"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Asset.objects.get().image.name.endswith("new.png"))

    def test_edit_can_set_estimate_flag(self):
        body = self._create()
        self.assertFalse(body["effective_life_is_estimate"])
        response = self.api.patch(
            f"/api/assets/{body['id']}/",
            {"effective_life_is_estimate": True, "effective_life_years": "10"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Asset.objects.get().effective_life_is_estimate)

    def test_delete_asset_removes_receipts_and_their_files(self):
        asset = Asset.objects.create(
            property=self.prop, name="X", purchase_date=dt.date(2025, 8, 1),
            cost=Decimal("10.00"),
        )
        receipt = Receipt.objects.create(
            property=self.prop, asset=asset, original_name="r.pdf",
            file=SimpleUploadedFile("r.pdf", b"%PDF-1.4 fake"),
        )
        path = Path(self.tmp) / receipt.file.name
        self.assertTrue(path.exists())

        response = self.api.delete(f"/api/assets/{asset.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(Receipt.objects.filter(pk=receipt.pk).exists())
        self.assertFalse(path.exists(), "receipt file was left on disk")


class AdhocExpenseEditTests(BaseLedgerTestCase):
    """Ad-hoc expenses can be edited and deleted; receipts go with them."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        self.cat = Category.objects.create(name="Cleaning", kind=Category.KIND_OPERATING)
        user = User.objects.create_user("expedit", "e@example.com", "unused-pw")
        self.api = APIClient()
        self.api.force_login(user)

    def _expense(self, **kwargs):
        defaults = dict(property=self.prop, category=self.cat, amount=Decimal("50.00"))
        defaults.update(kwargs)
        return Expense.objects.create(**defaults)

    def test_patch_updates_fields(self):
        expense = self._expense()
        response = self.api.patch(
            f"/api/expenses/{expense.id}/",
            {"amount": "75.50", "vendor": "Woolworths", "apportionment": "area"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("75.50"))
        self.assertEqual(expense.vendor, "Woolworths")
        # 25% let share of the 200/50 m2 property
        self.assertEqual(expense.deductible_amount, Decimal("18.88"))

    def test_patch_clears_custom_percentage(self):
        expense = self._expense(
            apportionment=Category.APPORTION_CUSTOM,
            apportionment_pct=Decimal("0.4000"),
        )
        self.assertEqual(expense.deductible_amount, Decimal("20.00"))
        response = self.api.patch(
            f"/api/expenses/{expense.id}/",
            {"apportionment": "none", "apportionment_pct": None},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        expense.refresh_from_db()
        self.assertIsNone(expense.apportionment_pct)
        self.assertEqual(expense.deductible_amount, Decimal("50.00"))

    def test_delete_expense_removes_receipts_and_files(self):
        expense = self._expense()
        receipt = Receipt.objects.create(
            property=self.prop, expense=expense, original_name="r.pdf",
            file=SimpleUploadedFile("r.pdf", b"%PDF-1.4 fake"),
        )
        path = Path(self.tmp) / receipt.file.name
        self.assertTrue(path.exists())

        response = self.api.delete(f"/api/expenses/{expense.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Expense.objects.filter(pk=expense.pk).exists())
        self.assertFalse(Receipt.objects.filter(pk=receipt.pk).exists())
        self.assertFalse(path.exists(), "receipt file was left on disk")


class AreaNightsApportionmentTests(BaseLedgerTestCase):
    """'By floor area × nights booked' — area share scaled by occupancy."""

    def setUp(self):
        super().setUp()
        # 56/281-style property: 50 m² of 200 m² => 25% area share.
        self.prop.total_floor_area_sqm = Decimal("200.00")
        self.prop.rental_floor_area_sqm = Decimal("50.00")
        self.prop.save()
        self.cat = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)

    def _expense(self, amount="1000.00", date="2026-03-01"):
        return Expense.objects.create(
            property=self.prop, category=self.cat, date=date, amount=Decimal(amount),
            apportionment=Category.APPORTION_AREA_NIGHTS,
        )

    def _nights(self, nights, label="FY2025-26", start="2025-07-01", end="2026-06-30"):
        EarningsSummary.objects.create(
            listing=self.listing, financial_year=label,
            period_start=dt.date.fromisoformat(start),
            period_end=dt.date.fromisoformat(end), nights_booked=nights,
        )

    def test_area_times_nights(self):
        # FY2025-26 has 365 days; 146 nights => 40% occupancy.
        self._nights(146)
        expense = self._expense()
        # 25% area × 40% nights = 10% => 100.00 of 1000.00
        self.assertEqual(expense.deductible_amount, Decimal("100.00"))

    def test_falls_back_to_area_when_no_nights_recorded(self):
        expense = self._expense()
        # 25% area only => 250.00
        self.assertEqual(expense.deductible_amount, Decimal("250.00"))

    def test_nights_are_capped_at_the_whole_year(self):
        self._nights(500)  # more nights than days in the year
        expense = self._expense()
        self.assertEqual(expense.deductible_amount, Decimal("250.00"))

    def test_uses_the_expense_date_financial_year(self):
        self._nights(73, label="FY2025-26", start="2025-07-01", end="2026-06-30")
        # 73/365 => 20% occupancy in FY2025-26
        in_fy = self._expense(date="2026-03-01")
        self.assertEqual(in_fy.deductible_amount, Decimal("50.00"))
        # An expense dated in FY2026-27 has no nights recorded => area only.
        next_fy = self._expense(date="2026-09-01")
        self.assertEqual(next_fy.deductible_amount, Decimal("250.00"))

    def test_explanation_matches_the_maths(self):
        self._nights(146)
        expense = self._expense()
        text = apportionment.explain(expense)
        self.assertIn("25.00% area", text)
        self.assertIn("40.00% nights", text)
        self.assertIn("10.00%", text)

    def test_monthly_summaries_are_summed(self):
        self._nights(73, label="FY2025-26", start="2025-07-01", end="2025-09-30")
        self._nights(73, label="FY2025-26", start="2025-10-01", end="2025-12-31")
        expense = self._expense()
        self.assertEqual(expense.deductible_amount, Decimal("100.00"))


class ReportPdfTests(BaseLedgerTestCase):
    """The FY report renders to HTML (and PDF when the renderer is present)."""

    def setUp(self):
        super().setUp()
        airbnb_pdf.import_report(SAMPLE_REPORT_TEXT, self.listing)
        cat = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)
        Expense.objects.create(
            property=self.prop, category=cat, date="2026-03-01",
            amount=Decimal("400.00"), apportionment=Category.APPORTION_AREA,
        )
        Asset.objects.create(
            property=self.prop, name="Aircon", purchase_date="2025-08-01",
            cost=Decimal("2000.00"), effective_life_years=Decimal("10.00"),
            method=Asset.METHOD_DIMINISHING, business_use_pct=Decimal("0.2500"),
        )

    def test_html_renderer_includes_the_key_sections(self):
        report, owners = reports.owner_reports(self.prop, "FY2025-26", True)
        html = pdf_renderer.render_report_html(report, owners, show_working=True)
        self.assertIn("Roost — rental report", html)
        self.assertIn("FY2025-26", html)
        self.assertIn("Net rental result", html)
        self.assertIn("Expenses — working", html)
        self.assertIn("opening", html)  # depreciation working text

    def test_html_renderer_hides_working_when_asked(self):
        report, owners = reports.owner_reports(self.prop, "FY2025-26", True)
        html = pdf_renderer.render_report_html(report, owners, show_working=False)
        self.assertNotIn("Expenses — working", html)

    def test_subtotals_are_not_page_footers(self):
        """A <tfoot> repeats on every page a table spans; ours must not.

        Regression: when the depreciation table rolled over a page, "Total
        depreciation" was printed again on the continuation page (and once more
        for every further page the table covered).
        """
        report, owners = reports.owner_reports(self.prop, "FY2025-26", True)
        html = pdf_renderer.render_report_html(report, owners)
        # The subtotal group must not be laid out as a repeating page footer…
        self.assertIn("tfoot { display: table-row-group; }", html)
        # …and each subtotal row is emitted exactly once.
        self.assertEqual(html.count("Total depreciation"), 1)
        self.assertEqual(html.count("Total claimable"), 1)
        self.assertEqual(html.count("Net income ("), 1)

    @unittest.skipUnless(_weasyprint_available(), "weasyprint not installed")
    def test_subtotal_appears_once_when_the_table_spans_pages(self):
        """End-to-end: a multi-page depreciation table keeps one subtotal."""
        import pdfplumber

        for i in range(90):
            Asset.objects.create(
                property=self.prop,
                name=f"Appliance {i:02d}",
                purchase_date="2025-08-01",
                cost=Decimal("1200.00"),
                effective_life_years=Decimal("5.00"),
                method=Asset.METHOD_DIMINISHING,
                business_use_pct=Decimal("0.2500"),
            )
        report, _owners = reports.owner_reports(self.prop, "FY2025-26", True)
        payload = pdf_renderer.render_report_pdf(report)

        with pdfplumber.open(io.BytesIO(payload)) as doc:
            pages = [page.extract_text() or "" for page in doc.pages]
        text = "\n".join(pages)
        self.assertGreater(len(pages), 1, "expected the report to span pages")
        # Once for the whole table — on its last page, not once per page.
        self.assertEqual(text.count("Total depreciation"), 1)
        self.assertIn("Total depreciation", pages[-1])

    def test_working_text_explains_the_calculation(self):
        report, _ = reports.owner_reports(self.prop, "FY2025-26", True)
        item = report["expenses"]["items"][0]
        self.assertIn("400.00", item["working"])
        self.assertIn("25.00%", item["working"])
        line = report["depreciation"]["lines"][0]
        self.assertIn("365 days", line["working"])
        self.assertIn("business use 25.00%", line["working"])

    def test_pdf_endpoint_requires_login(self):
        response = self.client.get(f"/api/reports/fy/pdf/?property={self.prop.id}")
        self.assertIn(response.status_code, (401, 403))

    @unittest.skipUnless(_weasyprint_available(), "weasyprint not installed")
    def test_pdf_endpoint_returns_a_pdf(self):
        user = User.objects.create_user("pdfuser", "p@example.com", "unused-pw")
        client = APIClient()
        client.force_login(user)
        response = client.get(
            f"/api/reports/fy/pdf/?property={self.prop.id}&fy=FY2025-26&owners=1&working=1"
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("attachment", response["Content-Disposition"])


class ReceiptsZipTests(BaseLedgerTestCase):
    """The receipt bundle: grouped by category, with a manifest."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tmp)
        override.enable()
        self.addCleanup(override.disable)
        user = User.objects.create_user("zipuser", "z@example.com", "unused-pw")
        self.api = APIClient()
        self.api.force_login(user)

    def _everything(self):
        cleaning = Category.objects.create(name="Cleaning", kind=Category.KIND_OPERATING)
        expense = Expense.objects.create(
            property=self.prop, category=cleaning, date="2026-03-01",
            amount=Decimal("50.00"), description="Supplies",
        )
        Receipt.objects.create(
            property=self.prop, expense=expense, original_name="clean.pdf",
            file=SimpleUploadedFile("clean.pdf", b"pdf-bytes"),
        )

        electricity = Category.objects.create(name="Electricity", kind=Category.KIND_UTILITY)
        utility = UtilityType.objects.create(
            property=self.prop, name="Electricity", category=electricity
        )
        UtilityBill.objects.create(
            utility_type=utility, bill_date=dt.date(2026, 1, 15),
            period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 3, 31),
            amount=Decimal("300.00"),
            attachment=SimpleUploadedFile("agl.pdf", b"bill-bytes"),
        )

        asset = Asset.objects.create(
            property=self.prop, name="Aircon", purchase_date=dt.date(2025, 8, 1),
            cost=Decimal("2000.00"), effective_life_years=Decimal("10.00"),
        )
        depreciation.recompute_schedule(asset)
        Receipt.objects.create(
            property=self.prop, asset=asset, original_name="aircon.pdf",
            file=SimpleUploadedFile("aircon.pdf", b"asset-bytes"),
        )

    def test_zip_groups_receipts_by_category_and_includes_a_manifest(self):
        self._everything()
        response = self.api.get(
            f"/api/reports/fy/receipts/?property={self.prop.id}&fy=FY2025-26"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment", response["Content-Disposition"])

        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        self.assertIn("Cleaning/clean.pdf", names)
        self.assertIn("Electricity/agl.pdf", names)
        self.assertIn("Depreciating assets/aircon.pdf", names)
        self.assertIn("manifest.csv", names)
        self.assertEqual(archive.read("Cleaning/clean.pdf"), b"pdf-bytes")

        manifest = archive.read("manifest.csv").decode()
        self.assertIn("Cleaning/clean.pdf", manifest)
        self.assertIn("Supplies", manifest)
        self.assertIn("Electricity/agl.pdf", manifest)
        self.assertIn("depreciating asset", manifest)

    def test_receipts_outside_the_year_are_excluded(self):
        other = Category.objects.create(name="Insurance", kind=Category.KIND_OPERATING)
        expense = Expense.objects.create(
            property=self.prop, category=other, date="2027-03-01", amount=Decimal("10.00")
        )
        Receipt.objects.create(
            property=self.prop, expense=expense, original_name="later.pdf",
            file=SimpleUploadedFile("later.pdf", b"x"),
        )
        response = self.api.get(
            f"/api/reports/fy/receipts/?property={self.prop.id}&fy=FY2025-26"
        )
        # Only FY2025-26 has nothing, the receipt is dated FY2026-27.
        self.assertEqual(response.status_code, 404)

    def test_404_when_nothing_to_bundle(self):
        response = self.api.get(
            f"/api/reports/fy/receipts/?property={self.prop.id}&fy=FY2025-26"
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_file_is_noted_instead_of_breaking_the_export(self):
        """A receipt row whose file has vanished must not 500 the whole ZIP."""
        cat = Category.objects.create(name="Supplies", kind=Category.KIND_OPERATING)
        expense = Expense.objects.create(
            property=self.prop, category=cat, date="2026-03-01", amount=Decimal("25.00")
        )
        receipt = Receipt.objects.create(
            property=self.prop, expense=expense, original_name="gone.pdf",
            file=SimpleUploadedFile("gone.pdf", b"bye"),
        )
        Path(self.tmp).joinpath(receipt.file.name).unlink()  # simulate loss

        response = self.api.get(
            f"/api/reports/fy/receipts/?property={self.prop.id}&fy=FY2025-26"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        self.assertNotIn("Supplies/gone.pdf", archive.namelist())
        manifest = archive.read("manifest.csv").decode()
        self.assertIn("file missing", manifest)

    def test_earnings_report_pdfs_are_bundled_in_their_own_folder(self):
        ImportBatch.objects.create(
            source="airbnb_pdf",
            listing=self.listing,
            filename="airbnb-earnings-FY2025-26.pdf",
            period_start=dt.date(2025, 7, 1),
            period_end=dt.date(2026, 6, 30),
            report_file=SimpleUploadedFile("report.pdf", b"report-bytes"),
        )
        response = self.api.get(
            f"/api/reports/fy/receipts/?property={self.prop.id}&fy=FY2025-26"
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        self.assertIn("Earnings reports/airbnb-earnings-FY2025-26.pdf", names)
        self.assertEqual(
            archive.read("Earnings reports/airbnb-earnings-FY2025-26.pdf"),
            b"report-bytes",
        )
        manifest = archive.read("manifest.csv").decode()
        self.assertIn("Earnings reports/airbnb-earnings-FY2025-26.pdf", manifest)
        self.assertIn("earnings report", manifest)

    def test_earnings_report_outside_the_year_is_excluded(self):
        ImportBatch.objects.create(
            source="airbnb_pdf",
            listing=self.listing,
            filename="next-year.pdf",
            period_start=dt.date(2026, 7, 1),
            period_end=dt.date(2027, 6, 30),
            report_file=SimpleUploadedFile("next.pdf", b"next"),
        )
        response = self.api.get(
            f"/api/reports/fy/receipts/?property={self.prop.id}&fy=FY2025-26"
        )
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        response = self.client.get(f"/api/reports/fy/receipts/?property={self.prop.id}")
        self.assertIn(response.status_code, (401, 403))


class CategoryApiTests(BaseLedgerTestCase):
    """Categories can be renamed; in-use ones refuse deletion with a clear message."""

    def setUp(self):
        super().setUp()
        user = User.objects.create_user("catuser", "c@example.com", "unused-pw")
        self.api = APIClient()
        self.api.force_login(user)

    def test_rename_category(self):
        category = Category.objects.create(name="Electrcity", kind=Category.KIND_UTILITY)
        response = self.api.patch(
            f"/api/categories/{category.id}/", {"name": "Electricity"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Category.objects.get().name, "Electricity")

    def test_rename_keeps_existing_expenses_attached(self):
        category = Category.objects.create(name="Mortgage", kind=Category.KIND_MORTGAGE)
        expense = Expense.objects.create(
            property=self.prop, category=category, amount=Decimal("10.00")
        )
        response = self.api.patch(
            f"/api/categories/{category.id}/", {"name": "Loan interest"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        expense.refresh_from_db()
        self.assertEqual(expense.category_id, category.id)
        self.assertEqual(expense.category.name, "Loan interest")

    def test_in_use_category_cannot_be_deleted(self):
        category = Category.objects.create(name="Mortgage", kind=Category.KIND_MORTGAGE)
        Expense.objects.create(property=self.prop, category=category, amount=Decimal("10.00"))
        response = self.api.delete(f"/api/categories/{category.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("in use", response.json()["detail"])
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())

    def test_unused_category_can_be_deleted(self):
        category = Category.objects.create(name="Unused")
        response = self.api.delete(f"/api/categories/{category.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())


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

    def test_editing_utility_type_reapportions_existing_bills(self):
        bill = self._bill(amount=Decimal("400.00"))
        # 25% let share => 100.00
        self.assertEqual(bill.expense.deductible_amount, Decimal("100.00"))

        self.ut.apportionment = Category.APPORTION_NONE
        self.ut.save()

        bill.expense.refresh_from_db()
        self.assertEqual(bill.expense.apportionment, Category.APPORTION_NONE)
        self.assertEqual(bill.expense.deductible_amount, Decimal("400.00"))

    def test_deleting_utility_type_removes_bills_and_expenses(self):
        bill = self._bill()
        expense_id = bill.expense_id
        response = self.client.delete(f"/api/utility-types/{self.ut.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(UtilityBill.objects.filter(pk=bill.pk).exists())
        self.assertFalse(
            Expense.objects.filter(pk=expense_id).exists(),
            "cascade left an orphaned expense",
        )
