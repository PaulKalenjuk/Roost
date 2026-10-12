"""Australian financial-year helpers (1 July – 30 June)."""
import calendar
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


# --- Quarters ---------------------------------------------------------------
#
# Quarters are aligned to the Australian financial year (BAS periods):
#   Q1 = Jul–Sep, Q2 = Oct–Dec, Q3 = Jan–Mar, Q4 = Apr–Jun.
# A quarter label looks like ``"FY2025-26-Q3"`` (Q3 = Jan–Mar 2026).
_QUARTER_OFFSETS = {1: (7, 0), 2: (10, 0), 3: (1, 1), 4: (4, 1)}


def quarter_index(date_obj):
    """Return the 1..4 quarter (within the FY) containing ``date_obj``."""
    month = to_date(date_obj).month
    if 7 <= month <= 9:
        return 1
    if 10 <= month <= 12:
        return 2
    if 1 <= month <= 3:
        return 3
    return 4


def quarter_label(date_obj):
    """Return the quarter label containing ``date_obj``, e.g. ``FY2026-27-Q1``."""
    date_obj = to_date(date_obj)
    return f"{fy_label(date_obj)}-Q{quarter_index(date_obj)}"


def quarter_bounds(label):
    """Return ``(start_date, end_date)`` for a quarter label like ``FY2025-26-Q3``."""
    fy = label[:9]  # "FY2025-26"
    index = int(str(label).strip()[-1])
    start_year = int(fy[2:6])
    month, year_offset = _QUARTER_OFFSETS[index]
    year = start_year + year_offset
    start = dt.date(year, month, 1)
    # Last day of the 3-month block.
    if month == 1:
        end = dt.date(year, 3, 31)
    elif month == 4:
        end = dt.date(year, 6, 30)
    elif month == 7:
        end = dt.date(year, 9, 30)
    else:
        end = dt.date(year, 12, 31)
    return start, end


def quarter_days(label):
    start, end = quarter_bounds(label)
    return (end - start).days + 1


def previous_fy_label(today=None):
    """The FY before the one containing ``today`` (defaults to today)."""
    today = to_date(today) or dt.date.today()
    return fy_label(fy_start_date(today) - dt.timedelta(days=1))


def current_fy_label(today=None):
    today = to_date(today) or dt.date.today()
    return fy_label(today)


def current_quarter_label(today=None):
    today = to_date(today) or dt.date.today()
    return quarter_label(today)


def previous_quarter_label(today=None):
    """The quarter before the one containing ``today`` (defaults to today)."""
    today = to_date(today) or dt.date.today()
    start, _end = quarter_bounds(quarter_label(today))
    return quarter_label(start - dt.timedelta(days=1))


def month_label(date_obj):
    """Return the month label containing ``date_obj``, e.g. ``2026-10``."""
    date_obj = to_date(date_obj)
    return f"{date_obj.year:04d}-{date_obj.month:02d}"


def month_bounds(label):
    """Return ``(start_date, end_date)`` for a month label like ``2026-10``."""
    year, month = int(str(label)[:4]), int(str(label)[5:7])
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last)


def previous_month_label(today=None):
    """The month before the one containing ``today`` (defaults to today)."""
    today = to_date(today) or dt.date.today()
    first = today.replace(day=1)
    return month_label(first - dt.timedelta(days=1))


def period_bounds(period_type, value):
    """Resolve ``(start, end)`` for a period type (``year``/``quarter``/``month``)."""
    if period_type == "month":
        return month_bounds(value)
    if period_type == "quarter":
        return quarter_bounds(value)
    return fy_bounds(value)


def days_held_in_fy(start: dt.date, end: dt.date, label: str):
    """Number of days in FY ``label`` between ``start`` and ``end`` inclusive."""
    fy_start, fy_end = fy_bounds(label)
    lo = max(start, fy_start)
    hi = min(end, fy_end)
    if hi < lo:
        return 0
    return (hi - lo).days + 1
