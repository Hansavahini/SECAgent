from django.core.management.base import BaseCommand
from watcher.services.ticker_resolver import TickerResolver
from watcher.services.filing_search import FilingSearch


class Command(BaseCommand):
    def handle(self, *args, **options):
        identifier = input("Enter ticker/CIK: ")

        company = TickerResolver().resolve(identifier)

        self.stdout.write(
            f"Searching SEC for {company['ticker']}..."
        )

        filings = FilingSearch().search(company["cik"])

        self.stdout.write(
            f"Found {len(filings)} matching filings."
        )

        for filing in filings:
            self.stdout.write(
                f"{filing['file_date'] if 'file_date' in filing else filing['filing_date']} | "
                f"{filing['accession_number']} | "
                f"{filing['form']}"
            )