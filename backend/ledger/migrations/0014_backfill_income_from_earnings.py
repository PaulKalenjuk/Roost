"""Backfill income types/entries from existing Airbnb monthly earnings.

Before the generic income model, Airbnb income lived only in ``MonthlyEarnings``.
This creates one ``IncomeType`` per listing that has earnings and copies each
month across as an ``IncomeEntry`` so reports show the same income as before.
"""
import calendar

from django.db import migrations


def backfill(apps, schema_editor):
    Listing = apps.get_model("ledger", "Listing")
    MonthlyEarnings = apps.get_model("ledger", "MonthlyEarnings")
    IncomeType = apps.get_model("ledger", "IncomeType")
    IncomeEntry = apps.get_model("ledger", "IncomeEntry")

    for listing in Listing.objects.all():
        months = list(MonthlyEarnings.objects.filter(listing=listing))
        if not months:
            continue
        income_type = IncomeType.objects.filter(listing=listing).first()
        if income_type is None:
            income_type = IncomeType.objects.filter(
                property_id=listing.property_id, name=listing.name
            ).first()
            if income_type is None:
                income_type = IncomeType.objects.create(
                    property_id=listing.property_id,
                    name=listing.name,
                    frequency="monthly",
                    listing=listing,
                )
            elif not income_type.listing_id:
                income_type.listing = listing
                income_type.save()
        for row in months:
            month = row.month
            last_day = calendar.monthrange(month.year, month.month)[1]
            IncomeEntry.objects.update_or_create(
                income_type=income_type,
                period_start=month,
                period_end=month.replace(day=last_day),
                defaults={
                    "amount": row.total_earnings,
                    "gross_amount": row.gross_earnings,
                    "gst_amount": 0,
                    "received": True,
                    "source": "airbnb_pdf",
                    "external_id": f"airbnb_pdf:{month:%Y-%m}",
                },
            )


def unbackfill(apps, schema_editor):
    IncomeEntry = apps.get_model("ledger", "IncomeEntry")
    IncomeEntry.objects.all().delete()
    IncomeType = apps.get_model("ledger", "IncomeType")
    IncomeType.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0013_incometype_incomeentry_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
