"""``python manage.py import_airbnb_csv <file> --listing <id|name>``"""
from django.core.management.base import BaseCommand, CommandError

from ledger.importers import airbnb_csv
from ledger.models import Listing


class Command(BaseCommand):
    help = "Import an Airbnb transaction-history CSV into reservations."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the Airbnb CSV file.")
        parser.add_argument(
            "--listing",
            required=True,
            help="Listing id or exact listing name the transactions belong to.",
        )

    def handle(self, *args, **options):
        listing = self._resolve_listing(options["listing"])
        with open(options["path"], newline="", encoding="utf-8-sig") as handle:
            batch = airbnb_csv.import_csv(handle, listing, filename=options["path"])

        self.stdout.write(
            self.style.SUCCESS(
                "Imported {total} rows for '{listing}': "
                "{created} created, {updated} updated, {skipped} skipped, {failed} failed."
            ).format(
                total=batch.rows_total,
                listing=listing.name,
                created=batch.rows_created,
                updated=batch.rows_updated,
                skipped=batch.rows_skipped,
                failed=batch.rows_failed,
            )
        )
        if batch.log:
            self.stdout.write(batch.log)

    def _resolve_listing(self, value):
        if value.isdigit():
            listing = Listing.objects.filter(pk=int(value)).first()
            if listing:
                return listing
        listing = Listing.objects.filter(name=value).first()
        if not listing:
            raise CommandError(
                f"No listing matching {value!r}. Create it in the admin first."
            )
        return listing
