"""Bill-coverage analysis for a utility type.

Answers: *since the property was purchased, which dates have a bill covering
them, and where are the gaps?*  Pure date arithmetic — no ORM writes — so it is
cheap to call for every utility type on a page.

A bill "covers" the range ``period_start..period_end`` inclusive.  Bills without
both dates can't contribute (they're reported separately).  The window runs from
``UtilityType.coverage_start`` (falling back to the property's purchase date) up
to today, extended if a bill's period reaches further.
"""
import datetime as dt

ONE_DAY = dt.timedelta(days=1)


def _clamp(value, low, high):
    return max(low, min(high, value))


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

    intervals = []
    for bill in bills:
        if bill.period_start and bill.period_end and bill.period_start <= bill.period_end:
            intervals.append((bill.period_start, bill.period_end, bill.id))
        else:
            result["bills_missing_period"] += 1

    end = today
    if intervals:
        end = max(end, max(i[1] for i in intervals))
    if end < start:
        end = start

    # Count overlapping periods so double-ups are visible.
    ordered = sorted(i[:2] for i in intervals)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] <= previous[1]:
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
