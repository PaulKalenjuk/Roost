"""Floor-area (and custom) apportionment helpers.

The ATO-accepted method for a short-stay let in a residence: claim the
proportion of shared costs that reflects the area *exclusively* used for the
income-producing activity.  We express that as a fraction derived from the
property's floor areas, with an optional per-expense override.
"""
from decimal import Decimal

ONE = Decimal("1")


def area_share(property_obj, listing=None):
    """Return the deductible fraction for shared costs, or ``None`` if unknown.

    Uses the property's resolved let share: an explicit ``let_percentage``
    first, then the floor-area calculation.
    """
    if property_obj is None:
        return None

    if listing is not None and getattr(listing, "area_share_override", None):
        return listing.area_share_override

    return property_obj.let_share


def resolve_share(expense):
    """Resolve the deductible fraction for an ``Expense``.

    Order of precedence:
      1. explicit ``apportionment_pct`` (custom)
      2. floor-area share of the property
      3. ``1`` (fully deductible)
    """
    if expense.apportionment == expense.APPORTION_NONE:
        return ONE

    if expense.apportionment == expense.APPORTION_CUSTOM:
        return expense.apportionment_pct if expense.apportionment_pct is not None else ONE

    if expense.apportionment == expense.APPORTION_AREA:
        share = area_share(expense.property, expense.listing)
        return share if share is not None else ONE

    return ONE
