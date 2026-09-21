"""Per-financial-year reporting, including per-owner breakdowns.

Everything is expressed as plain data (dicts/Decimals) so it can be rendered as
text, HTML, CSV or PDF without touching the ORM again.
"""
from decimal import Decimal

from django.db.models import Sum

from . import apportionment, fiscal
from .models import Asset, DepreciationEntry, Expense, MonthlyEarnings, Reservation

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


def _q(value):
    return Decimal(value or ZERO).quantize(CENTS)


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def fy_income(property_obj, label):
    """Income for a property in an FY, from monthly earnings (and reservations)."""
    start, end = fiscal.fy_bounds(label)
    rows = (
        MonthlyEarnings.objects.filter(
            listing__property=property_obj, month__range=(start, end)
        )
        .select_related("listing")
        .order_by("month")
    )
    agg = rows.aggregate(
        gross=Sum("gross_earnings"),
        service=Sum("service_fees"),
        tax=Sum("tax_withheld"),
        total=Sum("total_earnings"),
    )
    months = [
        {
            "month": r.month,
            "listing": r.listing.name,
            "gross_earnings": _q(r.gross_earnings),
            "service_fees": _q(r.service_fees),
            "tax_withheld": _q(r.tax_withheld),
            "total_earnings": _q(r.total_earnings),
        }
        for r in rows
    ]

    nights = (
        Reservation.objects.filter(
            listing__property=property_obj, check_in__range=(start, end)
        ).aggregate(n=Sum("nights"))["n"]
        or 0
    )

    return {
        "gross_earnings": _q(agg["gross"]),
        "service_fees": _q(agg["service"]),
        "tax_withheld": _q(agg["tax"]),
        "total_earnings": _q(agg["total"]),
        "nights_booked": nights,
        "months": months,
    }


def fy_expenses(property_obj, label):
    """Expenses for a property in an FY, grouped, with apportionment detail."""
    start, end = fiscal.fy_bounds(label)
    qs = (
        Expense.objects.filter(property=property_obj, date__range=(start, end))
        .select_related("category")
        .order_by("category__name", "date")
    )

    by_category = {}
    items = []
    notes = []
    total_amount = ZERO
    total_deductible = ZERO
    for exp in qs:
        share = exp.compute_apportionment_pct()
        share = share if share is not None else Decimal("1")
        detail = apportionment.explain(exp)
        if detail and detail not in notes:
            notes.append(detail)
        working = f"{_q(exp.amount)} × {share * 100:.2f}% = {_q(exp.deductible_amount)}"
        if detail:
            working += f" ({detail})"
        items.append(
            {
                "date": exp.date,
                "category": exp.category.name,
                "description": exp.description or exp.vendor,
                "amount": _q(exp.amount),
                "apportionment": exp.get_apportionment_display(),
                "share": share,
                "deductible_amount": _q(exp.deductible_amount),
                "detail": detail,
                "working": working,
            }
        )
        bucket = by_category.setdefault(
            exp.category.name, {"amount": ZERO, "deductible": ZERO}
        )
        bucket["amount"] += exp.amount
        bucket["deductible"] += exp.deductible_amount
        total_amount += exp.amount
        total_deductible += exp.deductible_amount

    for bucket in by_category.values():
        bucket["amount"] = _q(bucket["amount"])
        bucket["deductible"] = _q(bucket["deductible"])

    return {
        "total_amount": _q(total_amount),
        "total_deductible": _q(total_deductible),
        "by_category": by_category,
        "items": items,
        "notes": notes,
    }


def _depreciation_working(entry):
    """Plain-English calculation for one asset's FY deduction."""
    asset = entry.asset
    life = asset.effective_life_years
    days = entry.days_held or 0
    business = (asset.business_use_pct or 0) * 100
    if asset.low_value_pool:
        return (
            f"low-value pool: opening {entry.opening_value} × business use "
            f"{business:.2f}% = {entry.deduction}"
        )
    if asset.method == asset.METHOD_PRIME:
        label = f"cost {asset.cost} ÷ {life or '—'} yrs"
    else:
        label = f"opening {entry.opening_value} × (1 ÷ {life or '—'} yrs)"
    return (
        f"{label} × {days}/365 days × business use {business:.2f}% = {entry.deduction}"
    )


def fy_depreciation(property_obj, label, recompute=False):
    """Depreciation for a property in an FY, per asset."""
    import datetime as dt

    assets = Asset.objects.filter(property=property_obj)
    if recompute:
        from . import depreciation as dep

        for asset in assets:
            dep.recompute_schedule(asset)

    entries = DepreciationEntry.objects.filter(
        asset__property=property_obj, financial_year=label
    ).select_related("asset")

    lines = []
    total = ZERO
    for entry in entries:
        lines.append(
            {
                "asset": entry.asset.name,
                "method": entry.get_method_display(),
                "opening_value": _q(entry.opening_value),
                "business_use_pct": entry.asset.business_use_pct,
                "deduction": _q(entry.deduction),
                "closing_value": _q(entry.closing_value),
                "days_held": entry.days_held,
                "working": _depreciation_working(entry),
            }
        )
        total += entry.deduction

    return {"total_deduction": _q(total), "lines": lines}


# ---------------------------------------------------------------------------
# Assembled report
# ---------------------------------------------------------------------------
def fy_report(property_obj, label, recompute_depreciation=False):
    """Full FY report for a property: income, expenses, depreciation, result."""
    income = fy_income(property_obj, label)
    expenses = fy_expenses(property_obj, label)
    depreciation = fy_depreciation(property_obj, label, recompute=recompute_depreciation)

    taxable = (
        income["total_earnings"]
        - expenses["total_deductible"]
        - depreciation["total_deduction"]
    )

    return {
        "property": property_obj.name,
        "financial_year": label,
        "period": fiscal.fy_bounds(label),
        "let_share": property_obj.let_share,
        "gst_registered": property_obj.gst_registered,
        "income": income,
        "expenses": expenses,
        "depreciation": depreciation,
        "net_rental_result": _q(taxable),
    }


def owner_reports(property_obj, label, recompute_depreciation=False):
    """``fy_report`` split per owner by their ownership share.

    Returns a list of dicts, one per owner, each a copy of the report with every
    money figure multiplied by the owner's share.
    """
    report = fy_report(property_obj, label, recompute_depreciation)
    ownerships = property_obj.ownerships.select_related("owner").order_by("owner__name")

    results = []
    for own in ownerships:
        share = own.share_pct
        owner_view = {
            "owner": own.owner.name,
            "owner_email": own.owner.email,
            "share": share,
            "financial_year": label,
            "income": {
                "gross_earnings": _q(report["income"]["gross_earnings"] * share),
                "service_fees": _q(report["income"]["service_fees"] * share),
                "tax_withheld": _q(report["income"]["tax_withheld"] * share),
                "total_earnings": _q(report["income"]["total_earnings"] * share),
                "nights_booked": report["income"]["nights_booked"],
            },
            "expenses_by_category": {
                name: {
                    "deductible": _q(v["deductible"] * share),
                }
                for name, v in report["expenses"]["by_category"].items()
            },
            "expenses_deductible": _q(report["expenses"]["total_deductible"] * share),
            "depreciation": _q(report["depreciation"]["total_deduction"] * share),
            "net_rental_result": _q(report["net_rental_result"] * share),
        }
        results.append(owner_view)
    return report, results


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------
def format_report(report):
    """Human-readable text rendering of an ``fy_report`` result."""
    lines = []
    lines.append(f"Roost — rental report")
    lines.append(f"Property: {report['property']}")
    lines.append(f"Financial year: {report['financial_year']} "
                 f"({report['period'][0]:%d %b %Y} – {report['period'][1]:%d %b %Y})")
    let = report["let_share"]
    lines.append(f"Let share: {let * 100:.2f}%" if let is not None else "Let share: —")
    lines.append(f"GST registered: {'yes' if report['gst_registered'] else 'no'}")
    lines.append("")

    inc = report["income"]
    lines.append("INCOME")
    for m in inc["months"]:
        lines.append(
            f"  {m['month']:%b %Y}: gross {m['gross_earnings']:>10}  "
            f"fees {m['service_fees']:>9}  net {m['total_earnings']:>10}"
        )
    lines.append(f"  Gross earnings : {inc['gross_earnings']:>10}")
    lines.append(f"  Service fees   : {inc['service_fees']:>10}")
    lines.append(f"  Tax withheld   : {inc['tax_withheld']:>10}")
    lines.append(f"  Net income     : {inc['total_earnings']:>10}   "
                 f"({inc['nights_booked']} nights)")
    lines.append("")

    exp = report["expenses"]
    lines.append("EXPENSES (claimable portion)")
    for name, v in sorted(exp["by_category"].items()):
        lines.append(f"  {name:<28} amount {v['amount']:>10}  claimable {v['deductible']:>10}")
    lines.append(f"  {'TOTAL CLAIMABLE':<28} {'':>17}  claimable {exp['total_deductible']:>10}")
    for note in exp.get("notes", []):
        lines.append(f"  apportionment: {note}")
    lines.append("")

    dep = report["depreciation"]
    lines.append("DEPRECIATION")
    for line in dep["lines"]:
        lines.append(
            f"  {line['asset']:<28} {line['method']:<20} "
            f"open {line['opening_value']:>9}  business {line['business_use_pct'] * 100:>5.1f}%  "
            f"deduction {line['deduction']:>9}"
        )
    lines.append(f"  {'TOTAL DEPRECIATION':<28} deduction {dep['total_deduction']:>9}")
    lines.append("")
    lines.append(f"NET RENTAL RESULT: {report['net_rental_result']}")
    return "\n".join(lines)


def format_owner_report(owner_view):
    lines = [
        f"Owner: {owner_view['owner']}  ({owner_view['share'] * 100:.2f}%)",
        f"  FY {owner_view['financial_year']}",
        f"  Income (net)        : {owner_view['income']['total_earnings']:>10}"
        f"  (gross {owner_view['income']['gross_earnings']})",
        f"  Expenses (claimable): {owner_view['expenses_deductible']:>10}",
        f"  Depreciation        : {owner_view['depreciation']:>10}",
        f"  Net rental result   : {owner_view['net_rental_result']:>10}",
    ]
    for name, v in sorted(owner_view["expenses_by_category"].items()):
        lines.append(f"      {name:<26} {v['deductible']:>10}")
    return "\n".join(lines)
