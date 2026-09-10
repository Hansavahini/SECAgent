from django.core.management.base import BaseCommand
from watcher.services.ticker_resolver import TickerResolver


class Command(BaseCommand):
    def handle(self, *args, **options):
        identifier = input("Enter ticker/CIK: ")

        company = TickerResolver().resolve(identifier)

        self.stdout.write(f"Ticker: {company['ticker']}")
        self.stdout.write(f"CIK: {company['cik']}")
        self.stdout.write(f"Name: {company['name']}")