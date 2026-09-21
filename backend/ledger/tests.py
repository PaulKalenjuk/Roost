"""Regression tests for the Airbnb ledger.

Run with::

    DB_ENGINE=sqlite python manage.py test ledger
"""
from decimal import Decimal

from django.test import TestCase

from . import depreciation, reports
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
    Reservation,
)

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
