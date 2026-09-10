from django.core.management.base import BaseCommand
from watcher.services.ticker_resolver import TickerResolver
from watcher.services.sec_client import SECClient


class Command(BaseCommand):
    def handle(self, *args, **options):
        identifier = input("Enter ticker/CIK: ").strip()

        company = TickerResolver().resolve(identifier)

        cik = company["cik"]
        accession = "0000063908-26-000067"

        accession_clean = accession.replace("-", "")

        url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession_clean}/index.json"
        )

        data = SECClient().get(url).json()

        for item in data["directory"]["item"]:
            self.stdout.write(
                f"{item['name']} | {item.get('type', '')}"
            )