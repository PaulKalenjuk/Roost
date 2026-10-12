"""Coverage analysis for utility types and income sources.

* **Utilities** are day-based: *since the property was purchased, which dates
  have a bill covering them, and where are the gaps?*
* **Income** is period-based: *which months / quarters / years have a figure,
  and which still need one?*

Both ignore the **current, incomplete period** — as of mid-October, an October
monthly item isn't "missing" yet, because the bill hasn't arrived.  Pure date
arithmetic — no ORM writes — so it is cheap to call for every row on a page.

A bill "covers" the range ``period_start..period_end`` inclusive.  Bills without
both dates can't contribute (they're reported separately).  The window runs from
``UtilityType.coverage_start`` (falling back to the property's purchase date) up
to today, extended if a bill's period reaches further.
"""
import datetime as dt

ONE_DAY = dt.timedelta(days=1)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _month_bounds(d):
    start = d.replace(day=1)
    if d.month == 12:
        nxt = d.replace(year=d.year + 1, month=1, day=1)
    else:
        nxt = d.replace(month=d.month + 1, day=1)
    return start, nxt - ONE_DAY


def _half_year_bounds(d):
    if d.month <= 6:
        return dt.date(d.year, 1, 1), dt.date(d.year, 6, 30)
    return dt.date(d.year, 7, 1), dt.date(d.year, 12, 31)


def period_bounds_for(frequency, date_obj):
    """The month / quarter / half-year / year block containing ``date_obj``.

    Returns ``None`` for a frequency with no fixed period (``other``).
    """
    from . import fiscal

    if frequency == "monthly":
        return _month_bounds(date_obj)
    if frequency == "quarterly":
        return fiscal.quarter_bounds(fiscal.quarter_label(date_obj))
    if frequency == "half_yearly":
        return _half_year_bounds(date_obj)
    if frequency == "yearly":
        return fiscal.fy_bounds(fiscal.fy_label(date_obj))
    return None


def income_period_bounds(frequency, date_obj):
    """Back-compat alias — the block containing ``date_obj`` for a frequency."""
    return period_bounds_for(frequency, date_obj)


def coverage_for(utility_type, today=None):
    """Return a coverage summary dict for one :class:`~ledger.models.UtilityType`."""
    today = today or dt.date.today()
    prop = utility_type.property
    bills = list(utility_type.bills.all())

    start = utility_type.coverage_start or prop.purchase_date
    result = {
        "utility_type": utility_type.id,
        "name": utility_type.name,
        "property": prop.id,
        "property_name": prop.name,
        "frequency": utility_type.frequency,
        "frequency_label": utility_type.get_frequency_display(),
        "bills": len(bills),
        "bills_missing_period": 0,
        "overlaps": 0,
        "attachment_count": sum(1 for b in bills if b.attachment),
        "computable": False,
        "reason": None,
        "start": None,
        "end": None,
        "current_period": None,
        "total_days": 0,
        "covered_days": 0,
        "gap_days": 0,
        "coverage_pct": 0.0,
        "gaps": [],
        "segments": [],
    }

    if start is None:
        result["reason"] = (
            "Set the property's purchase date (or a coverage start on this utility "
            "type) to measure coverage."
        )
        return result

    # The current, incomplete period isn't counted — a bill for it isn't due yet.
    current = period_bounds_for(utility_type.frequency, today)
    if current is not None:
        result["current_period"] = {
            "start": current[0].isoformat(),
            "end": current[1].isoformat(),
        }

    intervals = []
    for bill in bills:
        if bill.period_start and bill.period_end and bill.period_start <= bill.period_end:
            intervals.append((bill.period_start, bill.period_end, bill.id))
        else:
            result["bills_missing_period"] += 1

    end = today
    if intervals:
        end = max(end, max(i[1] for i in intervals))
    # Don't measure gaps inside the current (in-progress) period.
    if current is not None and current[0] <= today:
        end = min(end, current[0] - ONE_DAY)
    if end < start:
        end = start

    # Count overlapping periods so double-ups are visible.
    ordered = sorted(i[:2] for i in intervals)
    for previous, current_iv in zip(ordered, ordered[1:]):
        if current_iv[0] <= previous[1]:
            result["overlaps"] += 1

    # Clamp to the window, then merge contiguous/overlapping coverage.
    clamped = []
    for s, e, _ in intervals:
        s2 = _clamp(s, start, end)
        e2 = _clamp(e, start, end)
        if s2 <= e2:
            clamped.append((s2, e2))
    clamped.sort()

    merged = []
    for s, e in clamped:
        if merged and s <= merged[-1][1] + ONE_DAY:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    total_days = (end - start).days + 1
    covered_days = sum((e - s).days + 1 for s, e in merged)

    # Gaps between merged covered blocks (and before/after them).
    gaps = []
    cursor = start
    for s, e in merged:
        if s > cursor:
            gaps.append((cursor, s - ONE_DAY))
        cursor = max(cursor, e + ONE_DAY)
    if cursor <= end:
        gaps.append((cursor, end))

    segments = []
    cursor = start
    for s, e in merged:
        if s > cursor:
            segments.append(("gap", cursor, s - ONE_DAY))
        segments.append(("covered", s, e))
        cursor = e + ONE_DAY
    if cursor <= end:
        segments.append(("gap", cursor, end))

    result.update(
        {
            "computable": True,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total_days": total_days,
            "covered_days": covered_days,
            "gap_days": total_days - covered_days,
            "coverage_pct": round(covered_days / total_days * 100, 1) if total_days else 0.0,
            "gaps": [
                {"start": s.isoformat(), "end": e.isoformat(), "days": (e - s).days + 1}
                for s, e in gaps
            ],
            "segments": [
                {
                    "kind": kind,
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "days": (e - s).days + 1,
                    "width_pct": round(((e - s).days + 1) / total_days * 100, 2) if total_days else 0,
                }
                for kind, s, e in segments
            ],
        }
    )
    return result


def income_coverage(income_type, today=None):
    """Which periods for an income type have a figure, and which are missing.

    Enumerates the expected periods — each month, quarter or year from the start
    date up to today — and flags the ones with no figure yet.  The **current
    (incomplete) period** is shown but never counted as missing, so mid-October a
    monthly source doesn't nag about October.  Also returns day-proportional
    ``segments`` (covered / gap) for the timeline bar over the *completed*
    periods.
    """
    today = today or dt.date.today()
    prop = income_type.property
    entries = [e for e in income_type.entries.all() if e.period_start and e.period_end]
    undated = income_type.entries.filter(period_start__isnull=True).count()
    frequency = income_type.frequency

    result = {
        "income_type": income_type.id,
        "name": income_type.name,
        "property": prop.id,
        "property_name": prop.name,
        "owner": income_type.owner_id,
        "owner_name": income_type.owner.name if income_type.owner_id else None,
        "frequency": frequency,
        "frequency_label": income_type.get_frequency_display(),
        "entries": len(entries),
        "entries_missing_period": undated,
        "computable": False,
        "reason": None,
        "start": None,
        "end": None,
        "current_period": None,
        "total_periods": 0,
        "entered_periods": 0,
        "missing_periods": 0,
        "coverage_pct": 0.0,
        "periods": [],
        "missing": [],
        "segments": [],
    }

    start = income_type.coverage_start or prop.purchase_date
    if start is None:
        # Fall back to the earliest entry so a figure is still shown.
        starts = [e.period_start for e in entries]
        start = min(starts) if starts else None
    if start is None:
        result["reason"] = (
            "Set the property's purchase date (or a coverage start on this income "
            "type) to see which periods still need a figure."
        )
        return result

    # Build the expected periods from `start` up to today (capped so we never
    # run away if the dates are odd).
    periods = []
    cursor, _end0 = period_bounds_for(frequency, start)
    guard = 0
    while cursor <= today and guard < 600:
        _, pend = period_bounds_for(frequency, cursor)
        periods.append((cursor, pend))
        cursor = pend + ONE_DAY
        guard += 1

    def entry_for(ps, pe):
        for e in entries:
            if e.period_start <= pe and e.period_end >= ps:
                return e
        return None

    rows = []
    missing = []
    segments = []
    completed = 0
    entered = 0
    span_start = periods[0][0] if periods else None
    span_end = None
    for ps, pe in periods:
        match = entry_for(ps, pe)
        is_current = pe >= today  # the (possibly unfinished) period containing today
        row = {
            "start": ps.isoformat(),
            "end": pe.isoformat(),
            "entered": match is not None,
            "entry": match.id if match else None,
            "amount": str(match.amount) if match else None,
            "current": is_current,
        }
        rows.append(row)
        if is_current:
            result["current_period"] = {"start": row["start"], "end": row["end"]}
            continue  # never a gap, and not counted in the totals
        completed += 1
        span_end = pe
        if match is None:
            missing.append({"start": row["start"], "end": row["end"]})
            segments.append(("gap", ps, pe))
        else:
            entered += 1
            segments.append(("covered", ps, pe))

    total_days = (span_end - span_start).days + 1 if (span_start and span_end) else 0
    result.update(
        {
            "computable": True,
            "start": span_start.isoformat() if span_start else start.isoformat(),
            "end": span_end.isoformat() if span_end else start.isoformat(),
            "total_periods": completed,
            "entered_periods": entered,
            "missing_periods": completed - entered,
            "coverage_pct": round(entered / completed * 100, 1) if completed else 0.0,
            "periods": rows,
            "missing": missing,
            "segments": [
                {
                    "kind": kind,
                    "start": s.isoformat(),
                    "end": e.isoformat(),
                    "days": (e - s).days + 1,
                    "width_pct": round(((e - s).days + 1) / total_days * 100, 2) if total_days else 0,
                }
                for kind, s, e in segments
            ],
        }
    )
    return result
