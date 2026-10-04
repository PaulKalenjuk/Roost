"""Read the highlighted lines of a *photographed* receipt and guess its category.

The capture flow (see :class:`ledger.models.UnallocatedExpense`) stores a photo
plus normalised highlight boxes drawn over the lines that belong to the expense.
To "process" one we:

1. **Crop** each highlighted box out of the photo (Pillow, EXIF-orientation
   applied) and **stack** the crops into a single tall image, so the vision model
   only sees — and only reads — the lines the user marked. With no highlights we
   fall back to the whole photo.
2. **Read** each marked line (description + price) with a vision model.
3. **Add the line prices up in Python** — model arithmetic is never trusted.
4. **Classify** the purchase with the text model, using the property's existing
   expenses as examples so the guess matches how the user actually files things.

Everything degrades gracefully: without a configured key the caller gets
:class:`BillExtractionUnavailable` (HTTP 503) and the UI falls back to manual
entry.
"""
import datetime as dt
import io
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from PIL import Image, ImageOps

from .bill_extract import (
    BillExtractionUnavailable,
    _coerce_date,
    _coerce_decimal,
    call_json,
    call_vision_json,
)

VISION_SYSTEM = (
    "You read line items from photos of shopping receipts for an Australian "
    "residential rental property's expense ledger. Reply with a single JSON "
    "object and nothing else. Amounts are numbers with no currency symbols; "
    "dates are ISO 8601 (YYYY-MM-DD). Use null for anything you cannot read — "
    "never invent a value."
)

VISION_USER = """The image stacks {n} highlighted region(s) from ONE receipt, top to bottom, separated by thin white gaps. Read every product/service line inside each region.

Return JSON in exactly this shape:
{{
  "regions": [
    {{"index": 1, "lines": [{{"description": "Bread", "amount": 3.50}}]}}
  ],
  "vendor": "the shop/supplier name, or null",
  "date": "the purchase date, or null",
  "gst_amount": "GST included in the highlighted lines, or null"
}}

Rules:
- "amount" is the money value for that line — the price paid (the rightmost number on the line).
- One entry per product/service line, in the order printed.
- Ignore totals, subtotals, payment and change rows, UNLESS the whole receipt is a single region (then include the final total as a line).
- Keep descriptions short (a few words).

Highlighted regions:
---
(count: {n})
---
"""

CLASSIFY_SYSTEM = (
    "You categorise expenses for an Australian residential rental property's "
    "ledger. Reply with a single JSON object and nothing else."
)

CLASSIFY_USER = """A host bought something for their short-stay rental. Pick the single best expense category for it.

Vendor: {vendor}
Highlighted line items:
{lines}

Available categories:
{categories}

How this property's existing expenses are already filed (description/vendor -> category):
{examples}

Return JSON: {{"category_hint": "<the exact name of one category above, or null>", "confidence": <a number 0..1>, "rationale": "<one short sentence>"}}
Prefer the most specific existing category. If the items fit the "guess from history" pattern above, follow it.
"""


def _normalise_box(box):
    """Validate/clamp one ``{x,y,w,h}`` box (fractions of the image, 0..1)."""
    if not isinstance(box, dict):
        return None
    try:
        x = float(box["x"])
        y = float(box["y"])
        w = float(box["w"])
        h = float(box["h"])
    except (KeyError, TypeError, ValueError):
        return None
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.0), 1.0 - x)
    h = min(max(h, 0.0), 1.0 - y)
    if w <= 0.001 or h <= 0.001:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def normalise_highlights(highlights):
    """Filter and clamp a highlight list; drops junk boxes."""
    out = []
    for box in highlights or []:
        clean = _normalise_box(box)
        if clean is not None:
            out.append(clean)
    return out


def crop_regions(image, highlights, pad_frac=0.008, min_width=700):
    """Crop each highlighted box out of ``image`` (PIL). Whole image if none.

    Narrow crops are upscaled so small printed text stays legible to the model.
    """
    width, height = image.size
    boxes = normalise_highlights(highlights)
    if not boxes:
        whole = image.copy()
        whole.thumbnail((1400, 2000))
        return [whole]

    crops = []
    for box in boxes:
        left = int(round((box["x"] - pad_frac) * width))
        top = int(round((box["y"] - pad_frac) * height))
        right = int(round((box["x"] + box["w"] + pad_frac) * width))
        bottom = int(round((box["y"] + box["h"] + pad_frac) * height))
        left = max(0, min(left, width - 1))
        top = max(0, min(top, height - 1))
        right = max(left + 1, min(right, width))
        bottom = max(top + 1, min(bottom, height))
        crop = image.crop((left, top, right, bottom))
        if crop.width < min_width:
            scale = min_width / crop.width
            crop = crop.resize(
                (min_width, max(1, int(crop.height * scale))), Image.LANCZOS
            )
        crops.append(crop)
    return crops


def stack_regions(images, gap=16):
    """Stack crops vertically (scaled to a common width) with white gaps."""
    if not images:
        return None
    if len(images) == 1:
        return images[0]
    width = max(im.width for im in images)
    scaled = []
    for im in images:
        if im.width != width:
            scale = width / im.width
            im = im.resize((width, max(1, int(im.height * scale))), Image.LANCZOS)
        scaled.append(im)
    height = sum(im.height for im in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for im in scaled:
        canvas.paste(im, (0, y))
        y += im.height + gap
    return canvas


def _load_image(source):
    """Open a photo (path/file/FieldFile) as an oriented RGB ``Image``."""
    if hasattr(source, "read"):
        data = source.read()
        if hasattr(source, "seek"):
            try:
                source.seek(0)
            except (OSError, ValueError):
                pass
        source = io.BytesIO(data)
    image = Image.open(source)
    image = ImageOps.exif_transpose(image)  # honour phone camera rotation
    return image.convert("RGB")


def _encode_jpeg(image, quality=88):
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def read_highlights(image, highlights):
    """Crop/stack the highlighted lines and read them with the vision model."""
    n = len(normalise_highlights(highlights)) or 1
    with _load_image(image) as img:
        sheet = stack_regions(crop_regions(img, highlights))
        jpeg = _encode_jpeg(sheet)

    data = call_vision_json(
        VISION_SYSTEM, VISION_USER.format(n=n), [jpeg]
    )

    lines = []
    regions = data.get("regions") or []
    if isinstance(regions, dict):
        regions = [regions]
    for region in regions:
        if not isinstance(region, dict):
            continue
        index = region.get("index")
        region_lines = region.get("lines") or []
        if isinstance(region_lines, dict):
            region_lines = [region_lines]
        for line in region_lines:
            if not isinstance(line, dict):
                continue
            description = (line.get("description") or "").strip() or None
            amount = _coerce_decimal(line.get("amount"))
            if description is None and amount is None:
                continue
            lines.append(
                {
                    "region": index,
                    "description": description,
                    "amount": str(amount) if amount is not None else None,
                }
            )

    total = Decimal("0.00")
    for line in lines:
        if line["amount"] is not None:
            total += Decimal(line["amount"])

    result = {
        "lines": lines,
        "amount": str(total.quantize(Decimal("0.01"))) if lines else None,
        "gst_amount": _coerce_decimal(data.get("gst_amount")),
        "vendor": (data.get("vendor") or "").strip() or None,
        "date": _coerce_date(data.get("date")),
        "regions": regions,
        "highlight_count": n,
        "extracted_by": f"vision:{settings.DEEPSEEK_VISION_MODEL}",
    }
    return json.loads(json.dumps(result, default=str))


def classify(vendor, lines, category_names=(), examples=()):
    """Guess the expense category, using the property's own history as examples."""
    line_text = "\n".join(
        f"- {ln.get('description') or 'item'}: {ln.get('amount') or '?'}"
        for ln in lines
    ) or "(nothing read)"
    example_text = "\n".join(
        f"- {e.get('description') or e.get('vendor') or 'expense'} -> {e.get('category')}"
        for e in examples
    ) or "(no existing expenses yet)"
    names = [n for n in category_names if n]

    data = call_json(
        CLASSIFY_SYSTEM,
        CLASSIFY_USER.format(
            vendor=vendor or "(unknown)",
            lines=line_text,
            categories=", ".join(names) if names else "(none defined yet)",
            examples=example_text,
        ),
    )
    confidence = _coerce_decimal(data.get("confidence"))
    if confidence is not None and confidence > 1:
        confidence = confidence / 100 if confidence <= 100 else None
    return {
        "category_hint": (data.get("category_hint") or "").strip() or None,
        "confidence": confidence,
        "rationale": (data.get("rationale") or "").strip() or None,
    }


def process_highlights(image, highlights, category_names=(), examples=()):
    """Full pipeline: read the highlighted lines, sum them, guess the category."""
    read = read_highlights(image, highlights)
    classification = classify(
        read.get("vendor"),
        read.get("lines") or [],
        category_names=category_names,
        examples=examples,
    )
    result = {
        **read,
        "category_hint": classification["category_hint"],
        "confidence": classification["confidence"],
        "rationale": classification["rationale"],
        "today": dt.date.today().isoformat(),
    }
    return json.loads(json.dumps(result, default=str))
