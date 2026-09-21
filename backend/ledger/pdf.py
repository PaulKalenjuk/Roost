"""Render a financial-year report as a PDF.

HTML is built here (easy to test and to tweak) and handed to WeasyPrint, which
is imported lazily so the rest of the app works even if the PDF stack isn't
installed — callers get :class:`PdfUnavailable` instead of an ImportError.
"""
from django.utils.html import escape


class PdfUnavailable(Exception):
    """Raised when the PDF renderer isn't available in this environment."""


def _money(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _pct(value):
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 10pt; color: #1f2328; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
h2 { font-size: 12pt; margin: 6mm 0 2mm; border-bottom: 1px solid #d8d8d4; padding-bottom: 1mm; }
h3 { font-size: 11pt; margin: 4mm 0 1mm; }
.meta { color: #6b7280; font-size: 9pt; margin: 0 0 4mm; }
table { width: 100%; border-collapse: collapse; margin-bottom: 3mm; }
th, td { text-align: left; padding: 1.2mm 2mm; border-bottom: 0.4pt solid #e3e3df; }
th { font-size: 8.5pt; text-transform: uppercase; letter-spacing: .03em; color: #6b7280; }
td.num, th.num { text-align: right; }
tfoot td { font-weight: bold; border-top: 0.8pt solid #b9b9b4; }
.total { font-size: 13pt; font-weight: bold; margin-top: 4mm; }
.working { font-size: 8pt; color: #4b5563; }
.owner { border: 0.5pt solid #d8d8d4; border-radius: 2mm; padding: 2mm 3mm; margin-bottom: 3mm; }
.small { font-size: 9pt; color: #6b7280; }
.gap { color: #b42318; }
.footer { margin-top: 6mm; font-size: 8pt; color: #9ca3af; }
"""


def render_report_html(report, owners=None, show_working=False):
    """Build the report as an HTML string."""
    parts = []
    add = parts.append

    add(f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>")
    add("<h1>Roost — rental report</h1>")
    period = report.get("period") or (None, None)
    add(
        "<p class='meta'>"
        f"Property: <strong>{escape(str(report['property']))}</strong> · "
        f"Financial year: <strong>{escape(report['financial_year'])}</strong> · "
        f"{period[0] or '?'} → {period[1] or '?'} · "
        f"Let share: {_pct(report.get('let_share'))} · "
        f"GST registered: {'yes' if report.get('gst_registered') else 'no'}"
        "</p>"
    )

    # --- Income ---
    income = report["income"]
    add("<h2>Income</h2><table><thead><tr>"
        "<th>Month</th><th class='num'>Gross</th><th class='num'>Service fees</th>"
        "<th class='num'>Net</th></tr></thead><tbody>")
    for month in income["months"]:
        add(
            "<tr>"
            f"<td>{escape(str(month['month'])[:7])}</td>"
            f"<td class='num'>{_money(month['gross_earnings'])}</td>"
            f"<td class='num'>{_money(month['service_fees'])}</td>"
            f"<td class='num'>{_money(month['total_earnings'])}</td>"
            "</tr>"
        )
    add("</tbody><tfoot><tr>"
        f"<td>Net income ({income['nights_booked']} nights booked)</td>"
        f"<td class='num'>{_money(income['gross_earnings'])}</td>"
        f"<td class='num'>{_money(income['service_fees'])}</td>"
        f"<td class='num'>{_money(income['total_earnings'])}</td>"
        "</tr></tfoot></table>")

    # --- Expenses ---
    expenses = report["expenses"]
    add("<h2>Expenses</h2><table><thead><tr>"
        "<th>Category</th><th class='num'>Amount</th><th class='num'>Claimable</th>"
        "</tr></thead><tbody>")
    for name, values in sorted(expenses["by_category"].items()):
        add(
            "<tr>"
            f"<td>{escape(name)}</td>"
            f"<td class='num'>{_money(values['amount'])}</td>"
            f"<td class='num'>{_money(values['deductible'])}</td>"
            "</tr>"
        )
    add("</tbody><tfoot><tr>"
        f"<td>Total claimable</td>"
        f"<td class='num'>{_money(expenses['total_amount'])}</td>"
        f"<td class='num'>{_money(expenses['total_deductible'])}</td>"
        "</tr></tfoot></table>")

    if show_working and expenses["items"]:
        add("<h3>Expenses — working</h3><table><thead><tr>"
            "<th>Date</th><th>Category</th><th>Description</th>"
            "<th class='num'>Amount</th><th>Basis</th><th class='num'>Claimable</th>"
            "</tr></thead><tbody>")
        for item in expenses["items"]:
            add(
                "<tr>"
                f"<td>{item['date']}</td>"
                f"<td>{escape(item['category'])}</td>"
                f"<td>{escape(item['description'] or '—')}</td>"
                f"<td class='num'>{_money(item['amount'])}</td>"
                f"<td>{escape(item['apportionment'])}"
                f"<div class='working'>{escape(item.get('working', ''))}</div></td>"
                f"<td class='num'>{_money(item['deductible_amount'])}</td>"
                "</tr>"
            )
        add("</tbody></table>")

    for note in expenses.get("notes", []):
        add(f"<p class='small'>Apportionment: {escape(note)}</p>")

    # --- Depreciation ---
    depreciation = report["depreciation"]
    add("<h2>Depreciation</h2><table><thead><tr>"
        "<th>Asset</th><th>Method</th><th class='num'>Opening</th>"
        "<th class='num'>Business use</th><th class='num'>Deduction</th>"
        "</tr></thead><tbody>")
    for line in depreciation["lines"]:
        add(
            "<tr>"
            f"<td>{escape(line['asset'])}"
            + (f"<div class='working'>{escape(line.get('working', ''))}</div>" if show_working else "")
            + "</td>"
            f"<td>{escape(line['method'])}</td>"
            f"<td class='num'>{_money(line['opening_value'])}</td>"
            f"<td class='num'>{_pct(line['business_use_pct'])}</td>"
            f"<td class='num'>{_money(line['deduction'])}</td>"
            "</tr>"
        )
    if not depreciation["lines"]:
        add("<tr><td colspan='5' class='small'>No depreciation entries.</td></tr>")
    add("</tbody><tfoot><tr>"
        f"<td colspan='4'>Total depreciation</td>"
        f"<td class='num'>{_money(depreciation['total_deduction'])}</td>"
        "</tr></tfoot></table>")

    add(f"<p class='total'>Net rental result: {_money(report['net_rental_result'])}</p>")

    # --- Per owner ---
    if owners:
        add("<h2>Per-owner breakdown</h2>")
        for owner in owners:
            add("<div class='owner'>")
            add(
                f"<h3>{escape(owner['owner'])} — {_pct(owner['share'])}</h3>"
                "<table><tbody>"
                f"<tr><td>Income (net)</td><td class='num'>{_money(owner['income']['total_earnings'])}</td></tr>"
            )
            for name, values in sorted(owner["expenses_by_category"].items()):
                add(
                    f"<tr><td class='small'>&nbsp;&nbsp;— {escape(name)}</td>"
                    f"<td class='num'>{_money(values['deductible'])}</td></tr>"
                )
            add(
                f"<tr><td>Expenses (claimable)</td><td class='num'>{_money(owner['expenses_deductible'])}</td></tr>"
                f"<tr><td>Depreciation</td><td class='num'>{_money(owner['depreciation'])}</td></tr>"
                f"<tr><td><strong>Net rental result</strong></td>"
                f"<td class='num'><strong>{_money(owner['net_rental_result'])}</strong></td></tr>"
                "</tbody></table></div>"
            )

    add(
        f"<p class='footer'>Generated by Roost · {report['financial_year']} · "
        f"{'including' if show_working else 'summary'} calculations</p>"
    )
    add("</body></html>")
    return "".join(parts)


def render_report_pdf(report, owners=None, show_working=False):
    """Return PDF bytes for the report."""
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover - environment dependent
        raise PdfUnavailable(f"PDF rendering is unavailable: {exc}") from exc

    html = render_report_html(report, owners=owners, show_working=show_working)
    return HTML(string=html).write_pdf()
