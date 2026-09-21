"""Floor-area / nights-booked apportionment helpers.

For a short-stay let inside a residence, the ATO-accepted way to claim a share of
*shared* running costs is to reflect both:

* how much of the property is used for the income-producing activity (floor
  area), and
* how much of the year it was actually used that way (nights booked).

So ``AREA_NIGHTS`` multiplies the two:

    deductible = amount × area share × (nights booked ÷ days in the FY)

An explicit per-expense override, or the plain floor-area method, is still
available.  Everything falls back sensibly when data is missing.
"""
import datetime as dt
from decimal import Decimal

from . import fiscal

ONE = Decimal("1")


def area_share(property_obj, listing=None):
    """Return the floor-area (or overridden let) share, or ``None`` if unknown."""
    if property_obj is None:
        return None

    if listing is not None and getattr(listing, "area_share_override", None):
        return listing.area_share_override

    return property_obj.let_share


def nights_booked(property_obj, label, listing=None):
    """Total nights booked in ``label`` (summed across the FY's summaries)."""
    from .models import EarningsSummary

    if property_obj is None or not label:
        return None
    qs = EarningsSummary.objects.filter(
        listing__property=property_obj, financial_year=label
    )
    if listing is not None:
        qs = qs.filter(listing=listing)
    total = sum((summary.nights_booked or 0) for summary in qs)
    return total or None


def nights_share(property_obj, label, listing=None):
    """Fraction of the financial year the let was booked (capped at 1)."""
    nights = nights_booked(property_obj, label, listing)
    if not nights:
        return None
    days = fiscal.fy_days(label)
    if days <= 0:
        return None
    share = Decimal(nights) / Decimal(days)
    return share if share < ONE else ONE


def _fy_label_for(date_value):
    return fiscal.fy_label(fiscal.to_date(date_value) or dt.date.today())


def resolve_share(expense):
    """Resolve the deductible fraction for an ``Expense``.

    Order of precedence per method:
      * ``none``   → 1 (fully deductible)
      * ``custom`` → the stored percentage
      * ``area``   → the property's floor-area share
      * ``area_nights`` → floor area × nights booked for the expense's FY
    Missing data never silently inflates the claim: it falls back to the
    narrower basis (or 100% only when nothing at all is configured).
    """
    if expense.apportionment == expense.APPORTION_NONE:
        return ONE

    if expense.apportionment == expense.APPORTION_CUSTOM:
        return expense.apportionment_pct if expense.apportionment_pct is not None else ONE

    if expense.apportionment == expense.APPORTION_AREA:
        share = area_share(expense.property, expense.listing)
        return share if share is not None else ONE

    if expense.apportionment == expense.APPORTION_AREA_NIGHTS:
        base = area_share(expense.property, expense.listing)
        label = _fy_label_for(expense.date)
        nights = nights_share(expense.property, label, expense.listing)
        if base is None and nights is None:
            return ONE
        if base is None:
            return nights
        if nights is None:
            return base
        return base * nights

    return ONE


def explain(expense):
    """Human-readable basis for an expense, e.g. for reports.

    ``"25.00% area × 40.00% nights (FY2025-26) = 10.00%"``
    """
    if expense.apportionment != expense.APPORTION_AREA_NIGHTS:
        return ""
    base = area_share(expense.property, expense.listing)
    label = _fy_label_for(expense.date)
    nights = nights_share(expense.property, label, expense.listing)
    if base is None and nights is None:
        return "no let share or nights data — claimed 100%"
    if nights is None:
        return f"{base * 100:.2f}% area · no nights booked recorded for {label}"
    if base is None:
        return f"{nights * 100:.2f}% nights ({label}) · no floor area set"
    return (
        f"{base * 100:.2f}% area × {nights * 100:.2f}% nights ({label}) "
        f"= {base * nights * 100:.2f}%"
    )
