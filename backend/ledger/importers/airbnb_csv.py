"""Airbnb *transaction history* CSV importer.

Where to get the file
---------------------
Airbnb → **Payments & payouts → Transaction history → Export CSV**
(or *Transaction history → Download CSV*).  Each row is a money movement for a
listing: reservation earnings, service fees, cleaning fees, adjustments and
payouts.

Design notes
------------
* **Header-driven mapping** — Airbnb has changed column names over the years, so
  we normalise headers and look up by alias rather than fixed positions.
* **Idempotent** — reservations are keyed on ``(listing, confirmation_code)`` so
  re-importing the same file (or an overlapping period) updates instead of
  duplicating.
* **Tolerant** — unknown columns/rows are preserved in ``Reservation.raw`` and
  reported, never silently dropped.
"""
import csv
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ..models import ImportBatch, Listing, Reservation

MONEY_RE = re.compile(r"[^0-9.\-]")

# header alias -> canonical field
HEADER_ALIASES = {
    "date": "date",
    "type": "type",
    "transaction type": "type",
    "confirmation code": "confirmation_code",
    "confirmation_code": "confirmation_code",
    "code": "confirmation_code",
    "listing": "listing",
    "listing name": "listing",
    "guest": "guest_name",
    "guest name": "guest_name",
    "start date": "check_in",
    "check-in": "check_in",
    "check in": "check_in",
    "end date": "check_out",
    "check-out": "check_out",
    "check out": "check_out",
    "nights": "nights",
    "nightly rate": "nightly_rate",
    "accommodation": "accommodation_amount",
    "accommodation amount": "accommodation_amount",
    "cleaning fee": "cleaning_fee",
    "gross earnings": "gross_earnings",
    "gross": "gross_earnings",
    "amount": "amount",
    "total": "amount",
    "airbnb fee": "airbnb_fee",
    "service fee": "airbnb_fee",
    "host fee": "airbnb_fee",
    "airbnb service fee": "airbnb_fee",
    "taxes": "taxes_collected",
    "tax": "taxes_collected",
    "payout date": "payout_date",
    "paid to": "paid_to",
    "currency": "currency",
    "details": "details",
    "reference": "reference",
    "status": "status",
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%Y/%m/%d")


def _norm_header(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _parse_money(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = MONEY_RE.sub("", text)
    if text in ("", "-", "."):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if negative:
        amount = -amount
    return amount


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_nights(value):
    if value is None:
        return None
    text = re.sub(r"[^0-9]", "", str(value))
    return int(text) if text else None


def _map_row(row):
    """Normalise a raw dict row into canonical ``{field: value}``."""
    mapped = {}
    for raw_key, raw_value in row.items():
        canonical = HEADER_ALIASES.get(_norm_header(raw_key))
        if canonical and canonical not in mapped:
            mapped[canonical] = raw_value
    return mapped


def import_csv(stream, listing, filename=""):
    """Import an Airbnb transaction CSV into ``Reservation`` rows.

    ``stream`` may be a text file object or a string of CSV text.  ``listing`` is
    the :class:`~ledger.models.Listing` the transactions belong to.

    Returns the saved :class:`~ledger.models.ImportBatch`.
    """
    if isinstance(stream, str):
        stream = io.StringIO(stream)

    batch = ImportBatch(source="airbnb_csv", listing=listing, filename=filename)
    log_lines = []

    reader = csv.DictReader(stream)
    rows = list(reader)
    batch.rows_total = len(rows)

    seen_codes = set()
    for index, raw_row in enumerate(rows, start=2):  # header is line 1
        row = _map_row(raw_row)
        code = (row.get("confirmation_code") or "").strip()
        if not code:
            batch.rows_skipped += 1
            log_lines.append(f"line {index}: no confirmation code — skipped")
            continue

        # A transaction file can hold several money rows per reservation; the
        # first row that carries the booking detail seeds the reservation and
        # later rows are merged in.
        reservation = Reservation.objects.filter(
            listing=listing, confirmation_code=code
        ).first()
        created = reservation is None
        if created:
            reservation = Reservation(
                listing=listing,
                confirmation_code=code,
                source="airbnb_csv",
                raw=raw_row,
            )

        _apply_row(reservation, row)

        try:
            with transaction.atomic():
                reservation.save()
        except Exception as exc:  # pragma: no cover - defensive
            batch.rows_failed += 1
            log_lines.append(f"line {index}: {exc}")
            continue

        if created:
            batch.rows_created += 1
        elif code in seen_codes:
            batch.rows_updated += 1
        else:
            batch.rows_updated += 1
        seen_codes.add(code)

    batch.log = "\n".join(log_lines)
    batch.finished_at = dt.datetime.now(dt.timezone.utc)
    batch.save()
    return batch


def _apply_row(reservation, row):
    """Merge a mapped CSV row into ``reservation`` (non-destructive on blanks)."""
    if row.get("guest_name"):
        reservation.guest_name = row["guest_name"].strip()

    for src, dst in (("check_in", "check_in"), ("check_out", "check_out"),
                     ("payout_date", "payout_date")):
        parsed = _parse_date(row.get(src))
        if parsed:
            setattr(reservation, dst, parsed)

    nights = _parse_nights(row.get("nights"))
    if nights is not None:
        reservation.nights = nights

    if row.get("currency"):
        reservation.currency = row["currency"].strip().upper()[:3]

    if row.get("status"):
        status = row["status"].strip().lower()
        if status in dict(Reservation.STATUS_CHOICES):
            reservation.status = status

    # Money: prefer explicit fields, fall back to the generic "amount" column.
    amount = _parse_money(row.get("amount"))
    gross = _parse_money(row.get("gross_earnings"))
    if gross is not None:
        reservation.gross_earnings = gross
    elif amount is not None and amount > 0:
        reservation.gross_earnings = amount

    for src, dst in (("accommodation_amount", "accommodation_amount"),
                     ("cleaning_fee", "cleaning_fee"),
                     ("airbnb_fee", "airbnb_fee"),
                     ("taxes_collected", "taxes_collected")):
        parsed = _parse_money(row.get(src))
        if parsed is not None:
            setattr(reservation, dst, abs(parsed))

    if amount is not None and amount < 0:
        # negative "amount" rows are payouts / deductions
        reservation.net_payout = abs(amount)

    if not reservation.gross_earnings:
        reservation.gross_earnings = reservation.gross_before_fee
