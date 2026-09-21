"""Airbnb **earnings report** PDF importer.

This parses the PDF Airbnb generates at *Payments & payouts → Earnings →*
**Download report** (the "Earnings report" for a period, e.g. a full financial
year).  It is a *summary* report: one row per calendar month, plus a per-home
and overall summary.

We therefore import **monthly totals** (:class:`~ledger.models.MonthlyEarnings`)
rather than individual reservations.  Re-importing the same (or an updated)
report **overwrites** the stored month, because Airbnb's report is the source
of truth for that month.

The parser works on extracted text, so it is easy to unit-test without a PDF:

    from ledger.importers.airbnb_pdf import parse_report_text, import_report

``import_report`` accepts either a path to a PDF or a text string.
"""
import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ..models import ImportBatch, Listing, MonthlyEarnings

MONEY = r"\$?(-?[\d,]+\.\d{2})"
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# "1 July 2025 – 30 June 2026"  (separator may be -, – or —)
PERIOD_RE = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*[-\u2013\u2014]\s*"
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
)
# "July 2025 $292.20 AUD $283.43 AUD"
MONTH_ROW_RE = re.compile(
    rf"^([A-Za-z]+)\s+(\d{{4}})\s+{MONEY}\s*AUD\s+{MONEY}\s*AUD\s*$"
)
HOST_RE = re.compile(r"Host name:\s*(.+)")
REPORT_GENERATED_RE = re.compile(r"Report generated:\s*(.+)")


def _money(text):
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _month_number(name):
    return MONTHS.get((name or "").strip().lower())


def parse_report_text(text):
    """Parse extracted earnings-report text into a structured dict.

    Returns::

        {
          "period_start": date | None,
          "period_end": date | None,
          "host_name": str,
          "report_generated": str,
          "currency": "AUD",
          "months": [ {month: date, gross_earnings, total_earnings} , ... ],
          "summary": { ... free-form aggregate values ... },
        }
    """
    result = {
        "period_start": None,
        "period_end": None,
        "host_name": "",
        "report_generated": "",
        "currency": "AUD",
        "months": [],
        "summary": {},
    }

    period = PERIOD_RE.search(text)
    if period:
        sd = int(period.group(1))
        sm = _month_number(period.group(2))
        sy = int(period.group(3))
        ed = int(period.group(4))
        em = _month_number(period.group(5))
        ey = int(period.group(6))
        if sm and em:
            result["period_start"] = dt.date(sy, sm, sd)
            result["period_end"] = dt.date(ey, em, ed)

    host = HOST_RE.search(text)
    if host:
        result["host_name"] = host.group(1).strip()

    generated = REPORT_GENERATED_RE.search(text)
    if generated:
        result["report_generated"] = generated.group(1).strip()

    for line in text.splitlines():
        row = MONTH_ROW_RE.match(line.strip())
        if not row:
            continue
        month = _month_number(row.group(1))
        year = int(row.group(2))
        gross = _money(row.group(3))
        total = _money(row.group(4))
        if month is None or gross is None:
            continue
        result["months"].append(
            {
                "month": dt.date(year, month, 1),
                "gross_earnings": gross,
                "total_earnings": total if total is not None else gross,
            }
        )

    # A few aggregate values worth keeping for audit / sanity checks.
    nights = re.search(r"Nights booked\s*\n?\s*(\d+)", text)
    if nights:
        result["summary"]["nights_booked"] = int(nights.group(1))
    avg = re.search(r"Avg\. night stay\s*\n?\s*([\d.]+)", text)
    if avg:
        result["summary"]["avg_night_stay"] = avg.group(1)
    earnings = re.search(rf"^Earnings\s+{MONEY}\s*AUD\s+{MONEY}\s*AUD\s+{MONEY}\s*AUD\s+{MONEY}\s*AUD\s+{MONEY}\s*AUD", text, re.M)
    if earnings:
        result["summary"]["totals"] = {
            "gross_earnings": str(_money(earnings.group(1))),
            "adjustments": str(_money(earnings.group(2))),
            "service_fees": str(_money(earnings.group(3))),
            "tax_withheld": str(_money(earnings.group(4))),
            "total": str(_money(earnings.group(5))),
        }

    result["months"].sort(key=lambda m: m["month"])
    return result


def extract_text(source):
    """Return text for a PDF path/bytes, or pass through a string of text."""
    if isinstance(source, str) and "\n" in source:
        return source  # already text
    import pdfplumber

    text_parts = []
    if isinstance(source, (bytes, bytearray)):
        import io

        with pdfplumber.open(io.BytesIO(source)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
    else:
        with pdfplumber.open(source) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def import_report(source, listing, filename=""):
    """Import an earnings report (PDF path/bytes, or text) for ``listing``.

    Upserts :class:`~ledger.models.MonthlyEarnings` — existing months for the
    listing are **overwritten**.  Returns the saved ``ImportBatch``.
    """
    text = extract_text(source)
    parsed = parse_report_text(text)

    batch = ImportBatch(
        source="airbnb_pdf",
        listing=listing,
        filename=filename or (source if isinstance(source, str) and "\n" not in source else ""),
        period_start=parsed["period_start"],
        period_end=parsed["period_end"],
        summary=parsed["summary"],
    )

    log_lines = []
    batch.rows_total = len(parsed["months"])
    for entry in parsed["months"]:
        gross = entry["gross_earnings"]
        total = entry["total_earnings"]
        # Airbnb's service fee is the difference between gross and net for the
        # standard fee structure; keep it explicit and positive-to-negative.
        service_fees = (gross - total) if gross is not None else Decimal("0.00")
        with transaction.atomic():
            obj, created = MonthlyEarnings.objects.update_or_create(
                listing=listing,
                month=entry["month"],
                defaults={
                    "currency": parsed["currency"],
                    "gross_earnings": gross,
                    "total_earnings": total,
                    "service_fees": service_fees,
                    "source": "airbnb_pdf",
                    "source_file": batch.filename,
                },
            )
        if created:
            batch.rows_created += 1
        else:
            batch.rows_updated += 1
        log_lines.append(f"{entry['month']:%Y-%m}: overwritten" if not created
                         else f"{entry['month']:%Y-%m}: created")

    if not parsed["months"]:
        log_lines.append("no monthly rows found — is this an 'Earnings report' PDF?")

    batch.log = "\n".join(log_lines)
    batch.finished_at = dt.datetime.now(dt.timezone.utc)
    batch.save()
    return batch
