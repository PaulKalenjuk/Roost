"""``python manage.py import_airbnb_pdf <report.pdf> --listing <id|name>``"""
from django.core.management.base import BaseCommand, CommandError

from ledger.importers import airbnb_pdf
from ledger.models import Listing


class Command(BaseCommand):
    help = "Import an Airbnb earnings-report PDF and upsert monthly totals."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the Airbnb earnings-report PDF.")
        parser.add_argument(
            "--listing",
            required=True,
            help="Listing id or exact listing name the report belongs to.",
        )

    def handle(self, *args, **options):
        listing = self._resolve_listing(options["listing"])
        batch = airbnb_pdf.import_report(options["path"], listing, filename=options["path"])

        self.stdout.write(
            self.style.SUCCESS(
                "Report {period} for '{listing}': {total} months — "
                "{created} created, {updated} overwritten."
            ).format(
                period=(
                    f"{batch.period_start}–{batch.period_end}"
                    if batch.period_start
                    else "(unknown period)"
                ),
                listing=listing.name,
                total=batch.rows_total,
                created=batch.rows_created,
                updated=batch.rows_updated,
            )
        )
        if batch.summary:
            self.stdout.write(f"Summary: {batch.summary}")

    def _resolve_listing(self, value):
        if str(value).isdigit():
            listing = Listing.objects.filter(pk=int(value)).first()
            if listing:
                return listing
        listing = Listing.objects.filter(name=value).first()
        if not listing:
            raise CommandError(
                f"No listing matching {value!r}. Create it in the admin first."
            )
        return listing
