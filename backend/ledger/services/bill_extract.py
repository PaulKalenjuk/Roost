"""Read a utility bill from an uploaded PDF and pull out the key fields.

Two steps:

1. **Text extraction** — ``pdfplumber`` gets the text layer from the PDF.  This
   works for bills downloaded from a supplier portal or emailed as a PDF.
   Scanned/photographed bills have no text layer; we detect that and report
   ``needs_ocr`` rather than guessing.
2. **Field extraction** — the text is sent to DeepSeek (OpenAI-compatible chat
   completions) with a strict JSON-only instruction.  We validate the response
   and coerce the values, so a hallucinated amount can't silently corrupt data.

The function degrades gracefully: with no ``DEEPSEEK_KEY`` configured it raises
``BillExtractionUnavailable`` and callers fall back to manual entry.
"""
import base64
import datetime as dt
import json
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from django.conf import settings

SYSTEM_PROMPT = (
    "You extract structured data from utility bills for an Australian rental "
    "property ledger. Reply with a single JSON object and nothing else. Use null "
    "for any field you cannot determine. Amounts are numbers (no currency "
    "symbols). Dates are ISO 8601 (YYYY-MM-DD)."
)

USER_TEMPLATE = """Today's date is {today}. Extract the following from the bill text below:
- amount: the total amount payable (a number)
- gst_amount: GST included in the amount, if stated (a number or null)
- bill_date: the date the bill was issued
- period_start / period_end: the service period the bill covers
- supplier: the utility provider's name
- utility_hint: one of electricity, gas, water, internet, phone, waste, other
- currency: 3-letter code (default AUD)

Bill text:
---
{text}
---
"""


class BillExtractionUnavailable(Exception):
    """Raised when DeepSeek isn't configured or the response is unusable."""


def extract_pdf_text(source, max_chars=12000):
    """Return extracted text for a PDF path/file-like/bytes ('' if none)."""
    import pdfplumber

    parts = []
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages[:8]:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > max_chars:
                break
    return "\n".join(parts)[:max_chars].strip()


def _coerce_decimal(value):
    if value in (None, "", "null"):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _coerce_date(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _chat_completions(payload, base_url, key):
    """POST an OpenAI-compatible chat payload and return the message content."""
    url = base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise BillExtractionUnavailable(f"DeepSeek HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover
        raise BillExtractionUnavailable(f"DeepSeek unreachable: {exc.reason}") from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:  # pragma: no cover
        raise BillExtractionUnavailable("Unexpected DeepSeek response shape") from exc


def _parse_json_object(content):
    """Parse a JSON object from model output, tolerating prose/code fences."""
    match = re.search(r"\{.*\}", content, re.S)
    try:
        return json.loads(match.group(0) if match else content)
    except json.JSONDecodeError as exc:
        raise BillExtractionUnavailable("DeepSeek did not return valid JSON") from exc


def call_json(system_prompt, user_prompt):
    """Call DeepSeek (text) and return the parsed JSON object.

    Raises :class:`BillExtractionUnavailable` when the key is missing, the call
    fails, or the response isn't usable JSON.
    """
    key = settings.DEEPSEEK_KEY
    if not key:
        raise BillExtractionUnavailable("DEEPSEEK_KEY is not configured")

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    return _parse_json_object(
        _chat_completions(payload, settings.DEEPSEEK_BASE_URL, key)
    )


def call_vision_json(
    system_prompt, user_prompt, images, model=None, base_url=None, api_key=None
):
    """Call a vision-capable chat model with one or more images.

    ``images`` is an iterable of raw image bytes (JPEG/PNG). Uses the DeepSeek
    **vision** settings, which default to the text model/base URL but can point
    at any OpenAI-compatible multimodal endpoint (e.g. a local Ollama ``/v1``).
    """
    key = api_key if api_key is not None else settings.DEEPSEEK_KEY
    if not key:
        raise BillExtractionUnavailable("DEEPSEEK_KEY is not configured")

    content = [{"type": "text", "text": user_prompt}]
    for raw in images:
        b64 = base64.b64encode(raw).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    payload = {
        "model": model or settings.DEEPSEEK_VISION_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    return _parse_json_object(
        _chat_completions(payload, base_url or settings.DEEPSEEK_VISION_BASE_URL, key)
    )


def _call_deepseek(text):
    return call_json(
        SYSTEM_PROMPT,
        USER_TEMPLATE.format(today=dt.date.today().isoformat(), text=text),
    )


def extract_bill(source, filename=""):
    """Extract bill fields from a PDF.

    Returns a dict with the coerced fields plus ``raw_text_chars`` and, when no
    text layer exists, ``needs_ocr=True``.
    """
    text = extract_pdf_text(source)
    if not text:
        return {
            "needs_ocr": True,
            "message": "No text layer found (probably a scan/photo). Enter the "
                       "amount manually, or OCR it first.",
            "filename": filename,
        }

    data = _call_deepseek(text)
    result = {
        "amount": _coerce_decimal(data.get("amount")),
        "gst_amount": _coerce_decimal(data.get("gst_amount")),
        "bill_date": _coerce_date(data.get("bill_date")),
        "period_start": _coerce_date(data.get("period_start")),
        "period_end": _coerce_date(data.get("period_end")),
        "supplier": (data.get("supplier") or "").strip() or None,
        "utility_hint": (data.get("utility_hint") or "").strip().lower() or None,
        "currency": (data.get("currency") or "AUD").strip().upper()[:3],
        "extracted_by": f"deepseek:{settings.DEEPSEEK_MODEL}",
        "raw_text_chars": len(text),
        "filename": filename,
    }
    # Keep Decimal/date objects JSON-friendly.
    return json.loads(json.dumps(result, default=str))
