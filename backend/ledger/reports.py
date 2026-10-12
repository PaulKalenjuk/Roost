"""Per-period reporting (financial year *or* quarter), with per-owner breakdowns.

Everything is expressed as plain data (dicts/Decimals) so it can be rendered as
text, HTML, CSV or PDF without touching the ORM again.

Income comes from :class:`~ledger.models.IncomeEntry` rows — one per period per
income type.  Airbnb imports create those rows from the earnings report, so the
Airbnb income flows through exactly the same path as any other income source.
An entry that only partly overlaps the reporting period is prorated by days, so
a yearly figure can still appear in a quarterly report (and vice versa) without
double counting.
"""
import calendar
import datetime as dt
from decimal import Decimal

from django.db.models import Q, Sum

from . import apportionment, fiscal
from .models import (
    Asset,
    DepreciationEntry,
    Expense,
    IncomeEntry,
    Reservation,
)

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")
ONE = Decimal("1")


def _q(value):
    return Decimal(value or ZERO).quantize(CENTS)


def _period_overlap_fraction(start, end, p_start, p_end):
    """Fraction of ``[p_start, p_end]`` that falls inside ``[start, end]``."""
    if p_start is None:
        return ZERO
    if p_end is None:
        p_end = p_start
    p_end = max(p_end, p_start)
    total_days = (p_end - p_start).days + 1
    lo = max(p_start, start)
    hi = min(p_end, end)
    if hi < lo or total_days <= 0:
        return ZERO
    return Decimal((hi - lo).days + 1) / Decimal(total_days)


def _month_end(d):
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last)


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def period_income(property_obj, start, end):
    """Income for a property in ``[start, end]`` from income entries.

    Returns the headline figures (gross / fees / net / GST), a per-month table
    for monthly entries, and a per-income-type breakdown.
    """
    entries = (
        IncomeEntry.objects.filter(
            Q(period_end__gte=start) | Q(period_end__isnull=True),
            income_type__property=property_obj,
            period_start__isnull=False,
            period_start__lte=end,
        )
        .select_related("income_type", "income_type__owner")
        .order_by("period_start")
    )

    by_type = {}
    months = {}
    gross_total = ZERO
    amount_total = ZERO
    gst_total = ZERO

    for entry in entries:
        frac = _period_overlap_fraction(start, end, entry.period_start, entry.period_end)
        if frac <= 0:
            continue
        gross = (entry.resolved_gross or ZERO) * frac
        amount = (entry.amount or ZERO) * frac
        gst = (entry.gst_amount or ZERO) * frac
        gross_total += gross
        amount_total += amount
        gst_total += gst

        typ = entry.income_type
        bucket = by_type.setdefault(
            typ.id,
            {
                "income_type": typ.id,
                "name": typ.name,
                "owner": typ.owner_id,
                "owner_name": typ.owner.name if typ.owner_id else None,
                "frequency": typ.frequency,
                "frequency_label": typ.get_frequency_display(),
                "gross": ZERO,
                "amount": ZERO,
                "gst": ZERO,
            },
        )
        bucket["gross"] += gross
        bucket["amount"] += amount
        bucket["gst"] += gst

        # A clean single-month entry feeds the monthly table (as the Airbnb
        # earnings report always did).
        ps, pe = entry.period_start, entry.period_end
        if ps and pe and ps.day == 1 and pe == _month_end(ps):
            row = months.setdefault(
                ps, {"month": ps, "listing": typ.name, "gross_earnings": ZERO,
                     "service_fees": ZERO, "tax_withheld": ZERO, "total_earnings": ZERO}
            )
            row["gross_earnings"] += gross
            row["total_earnings"] += amount
            row["service_fees"] += gross - amount

    month_rows = []
    for month in sorted(months):
        row = months[month]
        month_rows.append(
            {
                "month": row["month"],
                "listing": row["listing"],
                "gross_earnings": _q(row["gross_earnings"]),
                "service_fees": _q(row["service_fees"]),
                "tax_withheld": _q(row["tax_withheld"]),
                "total_earnings": _q(row["total_earnings"]),
            }
        )

    type_rows = []
    for bucket in by_type.values():
        type_rows.append(
            {
                "income_type": bucket["income_type"],
                "name": bucket["name"],
                "owner": bucket["owner"],
                "owner_name": bucket["owner_name"],
                "frequency": bucket["frequency"],
                "frequency_label": bucket["frequency_label"],
                "gross": _q(bucket["gross"]),
                "amount": _q(bucket["amount"]),
                "gst": _q(bucket["gst"]),
            }
        )
    type_rows.sort(key=lambda r: r["name"].lower())

    nights = (
        Reservation.objects.filter(
            listing__property=property_obj, check_in__range=(start, end)
        ).aggregate(n=Sum("nights"))["n"]
        or 0
    )

    return {
        "gross_earnings": _q(gross_total),
        "service_fees": _q(gross_total - amount_total),
        "tax_withheld": ZERO,
        "total_earnings": _q(amount_total),
        "gst_collected": _q(gst_total),
        "nights_booked": nights,
        "months": month_rows,
        "by_type": type_rows,
    }


def period_expenses(property_obj, start, end):
    """Expenses for a property in ``[start, end]``, grouped, with apportionment."""
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
    gst_total = ZERO
    gst_claimable = ZERO
    for exp in qs:
        share = exp.compute_apportionment_pct()
        share = share if share is not None else ONE
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
        gst_total += exp.gst_amount or ZERO
        gst_claimable += (exp.gst_amount or ZERO) * share

    for bucket in by_category.values():
        bucket["amount"] = _q(bucket["amount"])
        bucket["deductible"] = _q(bucket["deductible"])

    return {
        "total_amount": _q(total_amount),
        "total_deductible": _q(total_deductible),
        "gst_total": _q(gst_total),
        "gst_claimable": _q(gst_claimable),
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


def period_depreciation(property_obj, start, end, recompute=False):
    """Depreciation for a property in ``[start, end]``, per asset.

    Depreciation schedules are built per financial year; for a sub-year period
    (a quarter) the FY deduction is prorated by the days the quarter overlaps
    the year, and flagged as such.
    """
    assets = Asset.objects.filter(property=property_obj)
    if recompute:
        from . import depreciation as dep

        for asset in assets:
            dep.recompute_schedule(asset)

    label = fiscal.fy_label(start)
    fy_start, fy_end = fiscal.fy_bounds(label)
    fy_days = fiscal.fy_days(label)
    lo = max(start, fy_start)
    hi = min(end, fy_end)
    if hi >= lo:
        fraction = Decimal((hi - lo).days + 1) / Decimal(fy_days)
    else:
        fraction = ZERO
    prorated = fraction < ONE

    entries = DepreciationEntry.objects.filter(
        asset__property=property_obj, financial_year=label
    ).select_related("asset")

    lines = []
    total = ZERO
    for entry in entries:
        deduction = (entry.deduction * fraction).quantize(CENTS)
        lines.append(
            {
                "asset": entry.asset.name,
                "method": entry.get_method_display(),
                "opening_value": _q(entry.opening_value),
                "business_use_pct": entry.asset.business_use_pct,
                "deduction": deduction,
                "fy_deduction": _q(entry.deduction),
                "closing_value": _q(entry.closing_value),
                "days_held": entry.days_held,
                "working": _depreciation_working(entry),
            }
        )
        total += deduction

    return {
        "total_deduction": _q(total),
        "financial_year": label,
        "prorated": prorated,
        "proration_fraction": fraction.quantize(Decimal("0.000001")),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Assembled report
# ---------------------------------------------------------------------------
def period_report(
    property_obj,
    start,
    end,
    label,
    fy_label=None,
    period_type="year",
    recompute_depreciation=False,
):
    """Full report for an arbitrary period: income, expenses, depreciation, result."""
    income = period_income(property_obj, start, end)
    expenses = period_expenses(property_obj, start, end)
    depreciation = period_depreciation(
        property_obj, start, end, recompute=recompute_depreciation
    )

    taxable = (
        income["total_earnings"]
        - expenses["total_deductible"]
        - depreciation["total_deduction"]
    )

    gst = None
    if property_obj.gst_registered:
        gst = {
            "collected": income["gst_collected"],
            "credits": expenses["gst_claimable"],
            "net": _q(income["gst_collected"] - expenses["gst_claimable"]),
        }

    return {
        "property": property_obj.name,
        "financial_year": fy_label or label,
        "period_label": label,
        "period_type": period_type,
        "period": (start, end),
        "let_share": property_obj.let_share,
        "gst_registered": property_obj.gst_registered,
        "income": income,
        "expenses": expenses,
        "depreciation": depreciation,
        "gst": gst,
        "net_rental_result": _q(taxable),
    }


def fy_report(property_obj, label, recompute_depreciation=False):
    """Full FY report for a property."""
    start, end = fiscal.fy_bounds(label)
    return period_report(
        property_obj, start, end, label=label, fy_label=label, period_type="year",
        recompute_depreciation=recompute_depreciation,
    )


def quarter_report(property_obj, label, recompute_depreciation=False):
    """Full quarter report for a property (label like ``FY2025-26-Q3``)."""
    start, end = fiscal.quarter_bounds(label)
    return period_report(
        property_obj, start, end, label=label, fy_label=label[:9], period_type="quarter",
        recompute_depreciation=recompute_depreciation,
    )


def month_report(property_obj, label, recompute_depreciation=False):
    """Full report for a calendar month (label like ``2026-10``)."""
    start, end = fiscal.month_bounds(label)
    return period_report(
        property_obj, start, end, label=label, fy_label=fiscal.fy_label(start),
        period_type="month", recompute_depreciation=recompute_depreciation,
    )


def report_for(property_obj, period_type, value, recompute_depreciation=False):
    """Dispatch to the month/quarter/year report by ``period_type``."""
    if period_type == "month":
        return month_report(property_obj, value, recompute_depreciation)
    if period_type == "quarter":
        return quarter_report(property_obj, value, recompute_depreciation)
    return fy_report(property_obj, value, recompute_depreciation)


def _income_by_owner(property_obj, start, end):
    """Income attributed per owner.

    An entry whose income type names an owner belongs to that owner in full;
    otherwise it is split across the property's owners by their ownership share.
    """
    ownerships = list(property_obj.ownerships.select_related("owner"))
    by_owner = {}

    def bucket(name, email=""):
        return by_owner.setdefault(
            name, {"owner": name, "owner_email": email, "gross": ZERO,
                   "total": ZERO, "gst": ZERO}
        )

    entries = IncomeEntry.objects.filter(
        Q(period_end__gte=start) | Q(period_end__isnull=True),
        income_type__property=property_obj,
        period_start__isnull=False,
        period_start__lte=end,
    ).select_related("income_type", "income_type__owner")
    for entry in entries:
        frac = _period_overlap_fraction(start, end, entry.period_start, entry.period_end)
        if frac <= 0:
            continue
        gross = (entry.resolved_gross or ZERO) * frac
        amount = (entry.amount or ZERO) * frac
        gst = (entry.gst_amount or ZERO) * frac
        if entry.income_type.owner_id:
            b = bucket(entry.income_type.owner.name, entry.income_type.owner.email)
            b["gross"] += gross
            b["total"] += amount
            b["gst"] += gst
        else:
            for own in ownerships:
                b = bucket(own.owner.name, own.owner.email)
                b["gross"] += gross * own.share_pct
                b["total"] += amount * own.share_pct
                b["gst"] += gst * own.share_pct
    return by_owner


def owner_reports(property_obj, label, recompute_depreciation=False, period_type="year"):
    """``*_report`` split per owner: attributed income + share of costs.

    Returns ``(report, [owner_view, ...])``.  Owners named on an income type get
    that income in full; other income is split by ownership share.  Expenses and
    depreciation are always split by ownership share.
    """
    start, end = fiscal.period_bounds(period_type, label)
    if period_type == "quarter":
        fy_label = label[:9]
    elif period_type == "month":
        fy_label = fiscal.fy_label(start)
    else:
        fy_label = label
    report = period_report(
        property_obj, start, end,
        label=label,
        fy_label=fy_label,
        period_type=period_type,
        recompute_depreciation=recompute_depreciation,
    )

    share_by_owner = {
        own.owner.name: own.share_pct
        for own in property_obj.ownerships.select_related("owner")
    }
    income_by_owner = _income_by_owner(property_obj, start, end)

    names = sorted(set(share_by_owner) | set(income_by_owner))
    results = []
    for name in names:
        share = share_by_owner.get(name, ZERO)
        income = income_by_owner.get(
            name, {"owner_email": "", "gross": ZERO, "total": ZERO, "gst": ZERO}
        )
        email = income.get("owner_email") or next(
            (o.owner.email for o in property_obj.ownerships.select_related("owner")
             if o.owner.name == name),
            "",
        )
        results.append(
            {
                "owner": name,
                "owner_email": email,
                "share": share,
                "financial_year": report["financial_year"],
                "period_label": report["period_label"],
                "income": {
                    "gross_earnings": _q(income["gross"]),
                    "total_earnings": _q(income["total"]),
                    "gst_collected": _q(income["gst"]),
                },
                "expenses_by_category": {
                    cat: {"deductible": _q(v["deductible"] * share)}
                    for cat, v in report["expenses"]["by_category"].items()
                },
                "expenses_deductible": _q(report["expenses"]["total_deductible"] * share),
                "depreciation": _q(report["depreciation"]["total_deduction"] * share),
                "net_rental_result": _q(
                    income["total"]
                    - report["expenses"]["total_deductible"] * share
                    - report["depreciation"]["total_deduction"] * share
                ),
            }
        )
    return report, results


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------
def format_report(report):
    """Human-readable text rendering of a report result."""
    lines = []
    lines.append("Roost — rental report")
    lines.append(f"Property: {report['property']}")
    lines.append(
        f"Period: {report.get('period_label', report['financial_year'])} "
        f"({report['period'][0]:%d %b %Y} – {report['period'][1]:%d %b %Y})"
    )
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
    for t in inc.get("by_type", []):
        lines.append(
            f"  {t['name']:<28} net {t['amount']:>10}"
            + (f"  ({t['owner_name']})" if t.get("owner_name") else "")
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
    if dep.get("prorated"):
        lines.append("  (prorated to the period from the financial-year schedule)")
    lines.append("")
    if report.get("gst"):
        g = report["gst"]
        lines.append("GST")
        lines.append(f"  Collected on income : {g['collected']:>10}")
        lines.append(f"  Credits on expenses : {g['credits']:>10}")
        lines.append(f"  Net GST             : {g['net']:>10}")
        lines.append("")
    lines.append(f"NET RENTAL RESULT: {report['net_rental_result']}")
    return "\n".join(lines)


def format_owner_report(owner_view):
    lines = [
        f"Owner: {owner_view['owner']}  ({owner_view['share'] * 100:.2f}%)",
        f"  Period {owner_view.get('period_label', owner_view['financial_year'])}",
        f"  Income (net)        : {owner_view['income']['total_earnings']:>10}"
        f"  (gross {owner_view['income']['gross_earnings']})",
        f"  Expenses (claimable): {owner_view['expenses_deductible']:>10}",
        f"  Depreciation        : {owner_view['depreciation']:>10}",
        f"  Net rental result   : {owner_view['net_rental_result']:>10}",
    ]
    for name, v in sorted(owner_view["expenses_by_category"].items()):
        lines.append(f"      {name:<26} {v['deductible']:>10}")
    return "\n".join(lines)
