"""Print a rental report.

``python manage.py fy_report --property <id|name> --fy FY2025-26 [--owners]``
``python manage.py fy_report --property <id|name> --quarter FY2025-26-Q3``

``--fy`` accepts ``current`` / ``previous``; ``--quarter`` overrides ``--fy``.
"""
from django.core.management.base import BaseCommand, CommandError

from ledger import fiscal, reports
from ledger.models import Property


class Command(BaseCommand):
    help = "Print a per-financial-year rental report (optionally per owner)."

    def add_arguments(self, parser):
        parser.add_argument("--property", required=True, help="Property id or name.")
        parser.add_argument(
            "--fy",
            help=("Financial year label, e.g. FY2025-26. Use 'current' for today's FY, "
                  "'previous' for the year just ended."),
        )
        parser.add_argument(
            "--quarter",
            help=("Quarter label, e.g. FY2025-26-Q3. Use 'current' / 'previous'. "
                  "Overrides --fy when set."),
        )
        parser.add_argument(
            "--owners", action="store_true", help="Also print per-owner breakdowns."
        )
        parser.add_argument(
            "--recompute-depreciation",
            action="store_true",
            help="Rebuild depreciation schedules before reporting.",
        )

    def handle(self, *args, **options):
        import datetime as dt

        prop = self._resolve_property(options["property"])
        period_type = "year"
        label = options["fy"]
        if options["quarter"]:
            period_type = "quarter"
            label = options["quarter"]
            if label.lower() == "current":
                label = fiscal.current_quarter_label()
            elif label.lower() == "previous":
                label = fiscal.previous_quarter_label()
        elif not label or label.lower() == "current":
            label = fiscal.fy_label(dt.date.today())
        elif label.lower() == "previous":
            label = fiscal.previous_fy_label()

        report, owner_views = reports.owner_reports(
            prop, label, options["recompute_depreciation"], period_type=period_type
        )
        self.stdout.write(reports.format_report(report))

        if options["owners"]:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("PER-OWNER BREAKDOWN")
            self.stdout.write("=" * 60)
            if not owner_views:
                self.stdout.write(
                    self.style.WARNING("No ownership shares configured for this property.")
                )
            for view in owner_views:
                self.stdout.write(reports.format_owner_report(view))
                self.stdout.write("")

    def _resolve_property(self, value):
        if str(value).isdigit():
            prop = Property.objects.filter(pk=int(value)).first()
            if prop:
                return prop
        prop = Property.objects.filter(name=value).first()
        if not prop:
            raise CommandError(f"No property matching {value!r}.")
        return prop
