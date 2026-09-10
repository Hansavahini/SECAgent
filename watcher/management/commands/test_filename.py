from django.core.management.base import BaseCommand

from watcher.services.ticker_resolver import TickerResolver
from watcher.services.filing_downloader import FilingDownloader


class Command(BaseCommand):
    def handle(self, *args, **options):
        identifier = input("Enter ticker/CIK: ").strip()

        company = TickerResolver().resolve(identifier)

        accession_number = "0000063908-26-000067"
        file_type = "EX-99.1"
        sequence = "2"

        filename = FilingDownloader().get_filename(
            company["cik"],
            accession_number,
            file_type,
            sequence,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Exact SEC filename: {filename}"
            )
        )