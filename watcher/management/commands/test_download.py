from django.core.management.base import BaseCommand

from watcher.services.ticker_resolver import TickerResolver
from watcher.services.filing_search import FilingSearch
from watcher.services.filing_downloader import FilingDownloader
from watcher.services.market_filter import MarketFilter


class Command(BaseCommand):
    help = "Search and download SEC 8-K filings containing 'Market'."

    def handle(self, *args, **options):
        identifier = input("Enter ticker/CIK: ").strip()

        company = TickerResolver().resolve(identifier)

        self.stdout.write(
            f"Company: {company['name']} | "
            f"Ticker: {company['ticker']} | "
            f"CIK: {company['cik']}"
        )

        filings = FilingSearch().search(company["cik"])

        if not filings:
            self.stdout.write("No matching filings found.")
            return

        self.stdout.write(
            f"SEC search results: {len(filings)}"
        )

        downloader = FilingDownloader()
        market_filter = MarketFilter()

        for filing in filings:
            try:
                file_path = downloader.download(
                    company["cik"],
                    filing["accession_number"],
                    filing["file_type"],
                    filing["sequence"],
                )

                market_found = market_filter.contains_market(file_path)

                if market_found:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ Market found: "
                            f"{file_path.name}"
                        )
                    )
                else:
                    file_path.unlink(missing_ok=True)

                    self.stdout.write(
                        f"✗ Market not found: "
                        f"{file_path.name}"
                    )

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f"✗ Failed: "
                        f"{filing['accession_number']} - {exc}"
                    )
                )