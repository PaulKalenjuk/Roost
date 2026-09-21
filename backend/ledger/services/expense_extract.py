"""Read a receipt/invoice for an **ad-hoc expense** and populate the form.

Same pattern as bills and assets: ``pdfplumber`` text → DeepSeek JSON → coerced
values.  We also ask the model for a short ``category_hint`` and let the caller
match it against the user's existing categories, so a receipt for "Cleaning
supplies" lands on the right category when one exists.
"""
import datetime as dt
import json

from django.conf import settings

from .bill_extract import (
    BillExtractionUnavailable,
    _coerce_date,
    _coerce_decimal,
    call_json,
    extract_pdf_text,
)

SYSTEM_PROMPT = (
    "You extract structured data from purchase receipts and invoices for an "
    "Australian residential rental property's expense ledger. Reply with a single "
    "JSON object and nothing else. Use null for anything you cannot determine. "
    "Amounts are numbers with no currency symbols; dates are ISO 8601 (YYYY-MM-DD)."
)

USER_TEMPLATE = """Today's date is {today}. From the receipt text below, extract:
- vendor: the merchant or supplier name
- description: a short description of what was bought
- amount: the total amount paid (a number)
- gst_amount: GST included in the amount, if the receipt shows it (a number or null)
- date: the date of purchase
- category_hint: a short category label. Prefer exactly one of these if it fits:
  {categories}
- currency: 3-letter code (default AUD)
- notes: anything else useful, or null

Receipt text:
---
{text}
---
"""


def extract_expense(source, filename="", category_names=()):
    """Extract expense fields from a receipt. Returns a JSON-friendly dict."""
    text = extract_pdf_text(source)
    if not text:
        return {
            "needs_ocr": True,
            "message": "No text layer found (probably a scan/photo). Enter the details "
                       "manually, or OCR it first.",
            "filename": filename,
        }

    names = [n for n in category_names if n]
    data = call_json(
        SYSTEM_PROMPT,
        USER_TEMPLATE.format(
            today=dt.date.today().isoformat(),
            categories=", ".join(names) if names else "(no categories defined yet)",
            text=text,
        ),
    )

    result = {
        "vendor": (data.get("vendor") or "").strip() or None,
        "description": (data.get("description") or "").strip() or None,
        "amount": _coerce_decimal(data.get("amount")),
        "gst_amount": _coerce_decimal(data.get("gst_amount")),
        "date": _coerce_date(data.get("date")),
        "category_hint": (data.get("category_hint") or "").strip() or None,
        "currency": (data.get("currency") or "AUD").strip().upper()[:3],
        "notes": (data.get("notes") or "").strip() or None,
        "extracted_by": f"deepseek:{settings.DEEPSEEK_MODEL}",
        "raw_text_chars": len(text),
        "filename": filename,
    }
    return json.loads(json.dumps(result, default=str))
