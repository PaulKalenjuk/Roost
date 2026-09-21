"""Read a purchase receipt/invoice and populate asset-register fields.

Same two-step approach as utility bills (`bill_extract`):

1. ``pdfplumber`` gets the text layer (scans are detected and reported).
2. DeepSeek returns JSON — including an **estimated effective life**, because the
   ATO's effective-life schedule is fiddly and most people don't have it to hand.

The estimate is surfaced to the UI as ``effective_life_is_estimate: true`` so it
can be shown as "verify this" rather than silently trusted for a tax claim.
"""
import datetime as dt
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings

from ..models import Asset
from .bill_extract import (
    BillExtractionUnavailable,
    _coerce_date,
    _coerce_decimal,
    call_json,
    extract_pdf_text,
)

SYSTEM_PROMPT = (
    "You extract structured data from purchase receipts and invoices for an "
    "Australian residential rental property's asset register. Reply with a single "
    "JSON object and nothing else. Use null for anything you cannot determine. "
    "Amounts are numbers with no currency symbols; dates are ISO 8601 (YYYY-MM-DD)."
)

USER_TEMPLATE = """Today's date is {today}. From the receipt text below, extract:
- name: a short asset description (e.g. "Reverse-cycle air conditioner")
- supplier: the merchant/vendor name
- cost: the total amount paid (a number)
- gst_amount: GST included in the cost, if the receipt shows it (a number or null)
- purchase_date: the date of purchase
- asset_kind: one of plant_equipment, capital_works, other
- effective_life_years: your best estimate of the asset's effective life in years
  for ATO decline-in-value purposes, based on the asset type (a number)
- suggested_method: diminishing_value or prime_cost
- currency: 3-letter code (default AUD)
- notes: useful details like model, serial number or specs (or null)

Receipt text:
---
{text}
---
"""

VALID_KINDS = {k for k, _ in Asset.KIND_CHOICES}
VALID_METHODS = {k for k, _ in Asset.METHOD_CHOICES}


def _coerce_years(value):
    """Effective life in years: positive number, sane upper bound."""
    years = _coerce_decimal(value)
    if years is None:
        return None
    try:
        years = Decimal(years)
    except (InvalidOperation, TypeError):
        return None
    if years <= 0 or years > 100:
        return None
    return years


def extract_asset(source, filename=""):
    """Extract asset fields from a receipt PDF. Returns a JSON-friendly dict."""
    text = extract_pdf_text(source)
    if not text:
        return {
            "needs_ocr": True,
            "message": "No text layer found (probably a scan/photo). Enter the details "
                       "manually, or OCR it first.",
            "filename": filename,
        }

    data = call_json(
        SYSTEM_PROMPT, USER_TEMPLATE.format(today=dt.date.today().isoformat(), text=text)
    )

    kind = (data.get("asset_kind") or "").strip().lower()
    method = (data.get("suggested_method") or "").strip().lower()
    years = _coerce_years(data.get("effective_life_years"))

    result = {
        "name": (data.get("name") or "").strip() or None,
        "supplier": (data.get("supplier") or "").strip() or None,
        "cost": _coerce_decimal(data.get("cost")),
        "gst_amount": _coerce_decimal(data.get("gst_amount")),
        "purchase_date": _coerce_date(data.get("purchase_date")),
        "asset_kind": kind if kind in VALID_KINDS else None,
        "effective_life_years": years,
        "effective_life_is_estimate": years is not None,
        "suggested_method": method if method in VALID_METHODS else None,
        "currency": (data.get("currency") or "AUD").strip().upper()[:3],
        "notes": (data.get("notes") or "").strip() or None,
        "extracted_by": f"deepseek:{settings.DEEPSEEK_MODEL}",
        "raw_text_chars": len(text),
        "filename": filename,
    }
    return json.loads(json.dumps(result, default=str))
