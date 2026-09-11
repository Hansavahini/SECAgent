from django.core.management.base import BaseCommand

from watcher.services.ticker_file_reader import (
    TickerFileError,
    TickerFileReader,
)
from watcher.services.ticker_processor import TickerProcessor


class Command(BaseCommand):
    help = "Run the SEC filing watcher using the configured ticker file"

    def handle(self, *args, **options):
        try:
            tickers = TickerFileReader().read()
        except TickerFileError as exc:
            self.stderr.write(
                self.style.ERROR(str(exc))
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Watcher started: {len(tickers)} ticker(s)"
            )
        )

        processor = TickerProcessor()

        total_downloaded = 0
        total_skipped = 0
        total_failed = 0
        processed = 0
        invalid = 0

        for index, ticker in enumerate(tickers, start=1):
            self.stdout.write("")
            self.stdout.write(
                f"[{index}/{len(tickers)}] Processing {ticker}..."
            )

            try:
                result = processor.process(ticker)

            except ValueError as exc:
                invalid += 1

                self.stderr.write(
                    self.style.WARNING(
                        f"{ticker}: skipped - {exc}"
                    )
                )

                continue

            except Exception as exc:
                total_failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"{ticker}: processing failed - {exc}"
                    )
                )

                continue

            processed += 1

            total_downloaded += result["downloaded"]
            total_skipped += result["skipped"]
            total_failed += result["failed"]

            self.stdout.write(
                self.style.SUCCESS(
                    f"{ticker}: "
                    f"discovered={result['discovered']} "
                    f"downloaded={result['downloaded']} "
                    f"skipped={result['skipped']} "
                    f"failed={result['failed']}"
                )
            )

            for form, form_result in result["forms"].items():
                self.stdout.write(
                    f"  {form}: "
                    f"discovered={form_result['discovered']} "
                    f"downloaded={form_result['downloaded']} "
                    f"skipped={form_result['skipped']} "
                    f"failed={form_result['failed']}"
                )

                for error in form_result["errors"]:
                    self.stderr.write(
                        self.style.WARNING(
                            f"    {error}"
                        )
                    )

        self.stdout.write("")
        self.stdout.write("=" * 60)

        self.stdout.write(
            self.style.SUCCESS(
                "Watcher complete"
            )
        )

        self.stdout.write(
            f"Tickers in file: {len(tickers)}"
        )

        self.stdout.write(
            f"Successfully processed: {processed}"
        )

        self.stdout.write(
            f"Invalid/skipped tickers: {invalid}"
        )

        self.stdout.write(
            f"Documents downloaded: {total_downloaded}"
        )

        self.stdout.write(
            f"Duplicates skipped: {total_skipped}"
        )

        self.stdout.write(
            f"Failures: {total_failed}"
        )