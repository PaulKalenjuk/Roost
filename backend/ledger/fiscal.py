"""Australian financial-year helpers (1 July – 30 June)."""
import datetime as dt

_STR_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")


def to_date(value):
    """Coerce a ``date`` / ``datetime`` / ISO-ish string to a ``date``.

    Handles the case where a model attribute holds a raw string (e.g. an
    unsaved instance assigned from a form/CSV) rather than a real date.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in _STR_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def fy_label(date_obj):
    """Return the AU FY label containing ``date_obj``, e.g. ``"FY2025-26"``."""
    date_obj = to_date(date_obj)
    year = date_obj.year if date_obj.month >= 7 else date_obj.year - 1
    return f"FY{year}-{str(year + 1)[2:]}"


def fy_bounds(label):
    """Return ``(start_date, end_date)`` for an FY label like ``FY2025-26``."""
    start_year = int(label[2:6])
    return dt.date(start_year, 7, 1), dt.date(start_year + 1, 6, 30)


def fy_start_date(date_obj):
    return fy_bounds(fy_label(date_obj))[0]


def fy_end_date(date_obj):
    return fy_bounds(fy_label(date_obj))[1]


def fy_days(label):
    """Number of days in an FY (365, or 366 in a leap year)."""
    start, end = fy_bounds(label)
    return (end - start).days + 1


def days_held_in_fy(start: dt.date, end: dt.date, label: str):
    """Number of days in FY ``label`` between ``start`` and ``end`` inclusive."""
    fy_start, fy_end = fy_bounds(label)
    lo = max(start, fy_start)
    hi = min(end, fy_end)
    if hi < lo:
        return 0
    return (hi - lo).days + 1
