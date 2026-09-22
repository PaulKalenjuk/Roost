"""Bundle the receipt files behind a financial-year report into a ZIP.

Layout::

    FY2025-26/
      Cleaning/            <- one folder per expense category
        woolworths-2026-03-01.pdf
      Electricity/         <- utility bills use the utility's category
        agl-bill-2026-01.pdf
      Depreciating assets/ <- receipts for assets depreciated in the year
        aircon-invoice.pdf
      Earnings reports/    <- the Airbnb PDFs the income figures came from
        airbnb-earnings-FY2025-26.pdf
      manifest.csv
      report.pdf           <- if the PDF renderer is available

Files are de-duplicated by name so two receipts from the same vendor don't
overwrite each other.
"""
import csv
import io
import os
import re
import zipfile

from . import fiscal


def _safe(name):
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]+", "-", (name or "").strip())
    return cleaned.strip(" .-") or "Uncategorised"


def _bill_in_fy(bill, start, end):
    """A bill belongs to the FY if its date falls in it, or its period overlaps."""
    dated = bill.bill_date or bill.period_end or bill.period_start
    if dated and start <= dated <= end:
        return True
    return bool(
        bill.period_start
        and bill.period_end
        and bill.period_start <= end
        and bill.period_end >= start
    )


def _report_in_fy(batch, start, end):
    """An import belongs to the FY if the report period overlaps it.

    Falls back to the import date for rows with no period (e.g. a partial or
    hand-made record).
    """
    if batch.period_start and batch.period_end:
        return batch.period_start <= end and batch.period_end >= start
    dated = (batch.period_start or batch.period_end or batch.created_at.date())
    return start <= dated <= end


class _Collector:
    def __init__(self, zip_file):
        self.zip = zip_file
        self.used = set()
        self.rows = []
        self.added = 0
        self.missing = 0

    def add(self, folder, filename, file_field, row):
        if not file_field:
            return
        folder = _safe(folder)
        name = f"{folder}/{_safe(os.path.basename(filename or file_field.name)) or 'file'}"
        counter = 2
        while name in self.used:
            stem, ext = os.path.splitext(name)
            name = f"{stem} ({counter}){ext}"
            counter += 1
        self.used.add(name)

        # A receipt row can outlive its file (manual cleanup, storage move).
        # Don't let one missing file break the whole export — note it instead.
        try:
            with file_field.open("rb") as handle:
                payload = handle.read()
        except (FileNotFoundError, OSError, ValueError):
            self.missing += 1
            self.rows.append({**row, "file": name, "status": "file missing"})
            return

        self.zip.writestr(name, payload)
        self.added += 1
        self.rows.append({**row, "file": name, "status": "ok"})


def build_receipts_zip(property_obj, label, report=None, owners=None, show_working=False):
    """Return ZIP bytes, or ``None`` when there is nothing to include."""
    from .models import Asset, DepreciationEntry, Expense, ImportBatch, UtilityBill

    start, end = fiscal.fy_bounds(label)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        collector = _Collector(archive)

        # Ad-hoc + utility expenses recorded in the year.
        expenses = (
            Expense.objects.filter(property=property_obj, date__range=(start, end))
            .select_related("category")
            .prefetch_related("receipts")
            .order_by("date")
        )
        for expense in expenses:
            for receipt in expense.receipts.all():
                collector.add(
                    expense.category.name,
                    receipt.original_name or receipt.file.name,
                    receipt.file,
                    {
                        "category": expense.category.name,
                        "date": expense.date,
                        "description": expense.description or expense.vendor,
                        "amount": expense.amount,
                        "claimable": expense.deductible_amount,
                        "source": "expense",
                    },
                )

        # Utility bill attachments (they use the utility's category).
        bills = UtilityBill.objects.filter(
            utility_type__property=property_obj
        ).select_related("utility_type__category")
        for bill in bills:
            if not _bill_in_fy(bill, start, end):
                continue
            collector.add(
                bill.utility_type.category.name,
                bill.attachment.name if bill.attachment else "",
                bill.attachment,
                {
                    "category": bill.utility_type.category.name,
                    "date": bill.bill_date or bill.period_end,
                    "description": f"{bill.utility_type.name} bill",
                    "amount": bill.amount,
                    "claimable": bill.claimable_amount,
                    "source": "utility bill",
                },
            )

        # Receipts for assets depreciated in the year.
        asset_ids = (
            DepreciationEntry.objects.filter(
                asset__property=property_obj, financial_year=label
            )
            .values_list("asset_id", flat=True)
            .distinct()
        )
        for asset in Asset.objects.filter(id__in=list(asset_ids)).prefetch_related("receipts"):
            for receipt in asset.receipts.all():
                collector.add(
                    "Depreciating assets",
                    receipt.original_name or receipt.file.name,
                    receipt.file,
                    {
                        "category": "Depreciating assets",
                        "date": asset.purchase_date,
                        "description": asset.name,
                        "amount": asset.cost,
                        "claimable": "",
                        "source": "depreciating asset",
                    },
                )

        # The Airbnb earnings-report PDFs the income figures came from.
        for batch in (
            ImportBatch.objects.filter(listing__property=property_obj)
            .select_related("listing")
            .order_by("period_start", "created_at")
        ):
            if not batch.report_file or not _report_in_fy(batch, start, end):
                continue
            collector.add(
                "Earnings reports",
                batch.filename or os.path.basename(batch.report_file.name),
                batch.report_file,
                {
                    "category": "Earnings reports",
                    "date": batch.period_start or batch.created_at.date(),
                    "description": (
                        f"{batch.listing.name} earnings report"
                        if batch.listing
                        else "Airbnb earnings report"
                    ),
                    "amount": "",
                    "claimable": "",
                    "source": "earnings report",
                },
            )

        if not collector.rows:
            return None

        # Manifest, then the report itself (if we can render one).
        manifest = io.StringIO()
        writer = csv.writer(manifest)
        writer.writerow(
            ["file", "status", "category", "date", "description", "amount",
             "claimable", "source"]
        )
        for row in sorted(collector.rows, key=lambda r: r["file"]):
            writer.writerow(
                [
                    row["file"],
                    row.get("status", "ok"),
                    row["category"],
                    row["date"] or "",
                    row["description"] or "",
                    row["amount"],
                    row["claimable"],
                    row["source"],
                ]
            )
        if collector.missing:
            writer.writerow(
                ["", f"{collector.missing} receipt file(s) referenced but missing",
                 "", "", "", "", "", ""]
            )
        archive.writestr("manifest.csv", manifest.getvalue())

        if report is not None:
            try:
                from . import pdf

                archive.writestr(
                    "report.pdf",
                    pdf.render_report_pdf(
                        report,
                        owners or [],
                        show_working=show_working,
                        image=property_obj.image,
                    ),
                )
            except Exception:  # pragma: no cover - renderer optional
                pass

    return buffer.getvalue()
