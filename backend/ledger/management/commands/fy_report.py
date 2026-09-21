"""``python manage.py fy_report --property <id|name> --fy FY2025-26 [--owners]``"""
from django.core.management.base import BaseCommand, CommandError

from ledger import fiscal, reports
from ledger.models import Property


class Command(BaseCommand):
    help = "Print a per-financial-year rental report (optionally per owner)."

    def add_arguments(self, parser):
        parser.add_argument("--property", required=True, help="Property id or name.")
        parser.add_argument(
            "--fy",
            required=True,
            help="Financial year label, e.g. FY2025-26. Use 'current' for today's FY.",
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
        prop = self._resolve_property(options["property"])
        label = options["fy"]
        if label.lower() == "current":
            import datetime as dt

            label = fiscal.fy_label(dt.date.today())

        report, owner_views = reports.owner_reports(
            prop, label, options["recompute_depreciation"]
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
