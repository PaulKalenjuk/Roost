"""Depreciation (decline in value) schedules.

Implements the ATO's two methods for *plant & equipment*:

* **Prime cost** — straight line: ``cost / effective_life`` per full year.
* **Diminishing value** — ``base_value × (1 / effective_life) × 365/365`` on the
  opening undeducted value.

Both are pro-rated for the first year by the number of days the asset was held
(ATO requires pro-rating in the year of acquisition and disposal).

These helpers are pure functions returning plain data; ``recompute_schedule``
persists them as :class:`~ledger.models.DepreciationEntry` rows.
"""
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

from . import fiscal

CENTS = Decimal("0.01")


def _q(value):
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def _held_dates(asset):
    """Return ``(held_from, disposed_or_None)`` as real dates."""
    return fiscal.to_date(asset.purchase_date), fiscal.to_date(asset.disposed_date)


def schedule_for_fy(asset, label):
    """Compute one FY's depreciation for an asset.

    Returns a dict ``{financial_year, opening_value, deduction, closing_value,
    days_held, method}`` or ``None`` if the asset was not held in that FY.
    """
    from .models import Asset

    if asset.cost is None:
        return None

    fy_start, fy_end = fiscal.fy_bounds(label)

    held_from, disposed = _held_dates(asset)
    if held_from is None:
        return None
    held_to = disposed or fy_end
    if held_from > fy_end or (disposed and disposed < fy_start):
        return None

    # Opening value = cost less depreciation already deducted in prior FYs
    # (principal component).  We recompute from scratch each time for integrity.
    opening = Decimal(asset.cost)
    for prior_label in _fys_before(label, asset.purchase_date):
        prior = _deduction_only(asset, prior_label, opening)
        if prior is None:
            continue
        opening -= prior
    if opening < 0:
        opening = Decimal("0")

    days = fiscal.days_held_in_fy(held_from, held_to, label)
    if days <= 0:
        return None

    if asset.low_value_pool:
        # Low-value pool: 18.75% in the year of allocation, 37.5% thereafter.
        # We approximate with the first-year rate for the allocation year only
        # when it is the FY of purchase, else the ongoing rate.
        rate = Decimal("0.1875") if fiscal.fy_label(held_from) == label else Decimal("0.375")
        deduction = opening * rate
    elif asset.method == Asset.METHOD_PRIME and asset.effective_life_years:
        annual = Decimal(asset.cost) / Decimal(asset.effective_life_years)
        deduction = annual * Decimal(days) / Decimal("365")
    elif asset.effective_life_years:
        annual = opening * (Decimal("1") / Decimal(asset.effective_life_years))
        deduction = annual * Decimal(days) / Decimal("365")
    else:
        return None

    deduction *= Decimal(asset.business_use_pct)
    deduction = _q(deduction)
    if deduction > opening:
        deduction = _q(opening)

    closing = _q(opening - deduction)
    return {
        "financial_year": label,
        "opening_value": _q(opening),
        "deduction": deduction,
        "closing_value": closing,
        "days_held": days,
        "method": asset.method,
    }


def _deduction_only(asset, label, opening):
    """Deduction for ``label`` given an ``opening`` value (used for roll-forward)."""
    from .models import Asset

    fy_start, fy_end = fiscal.fy_bounds(label)
    held_from, disposed = _held_dates(asset)
    if held_from is None:
        return None
    held_to = disposed or fy_end
    days = fiscal.days_held_in_fy(held_from, held_to, label)
    if days <= 0:
        return None
    if asset.low_value_pool:
        rate = Decimal("0.375")
        return _q(opening * rate)
    if asset.method == Asset.METHOD_PRIME and asset.effective_life_years:
        annual = Decimal(asset.cost) / Decimal(asset.effective_life_years)
    elif asset.effective_life_years:
        annual = opening * (Decimal("1") / Decimal(asset.effective_life_years))
    else:
        return None
    return _q(annual * Decimal(days) / Decimal("365") * Decimal(asset.business_use_pct))


def _fys_before(label, purchase_date):
    """Yield FY labels from purchase up to (excluding) ``label``."""
    purchase_date = fiscal.to_date(purchase_date)
    start_label = fiscal.fy_label(purchase_date)
    if start_label >= label:
        return
    year = int(start_label[2:6])
    end_year = int(label[2:6])
    while year < end_year:
        yield f"FY{year}-{str(year + 1)[2:]}"
        year += 1


def recompute_schedule(asset):
    """Rebuild all :class:`DepreciationEntry` rows for an asset.

    Returns the list of saved entries.
    """
    from .models import DepreciationEntry

    asset.depreciation_entries.all().delete()

    purchase = fiscal.to_date(asset.purchase_date)
    disposed = fiscal.to_date(asset.disposed_date)
    start = fiscal.fy_label(purchase)
    end = fiscal.fy_label(disposed or dt.date.today())

    year = int(start[2:6])
    end_year = int(end[2:6])
    entries = []
    while year <= end_year:
        label = f"FY{year}-{str(year + 1)[2:]}"
        row = schedule_for_fy(asset, label)
        if row:
            entries.append(
                DepreciationEntry(
                    asset=asset,
                    financial_year=row["financial_year"],
                    method=row["method"],
                    opening_value=row["opening_value"],
                    deduction=row["deduction"],
                    closing_value=row["closing_value"],
                    days_held=row["days_held"],
                )
            )
        year += 1

    return DepreciationEntry.objects.bulk_create(entries)
