from django.core.management.base import BaseCommand

from watcher.services.ticker_file_reader import (
    TickerFileError,
    TickerFileReader,
)
from watcher.services.ticker_processor import (
    TickerProcessor,
)


class Command(BaseCommand):
    help = (
        "Run the SEC filing watcher using the "
        "configured ticker file"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--auto-index",
            action="store_true",
            help=(
                "After downloading and registering a new "
                "filing, ingest, chunk, and embed it."
            ),
        )

    def handle(self, *args, **options):
        auto_index = bool(
            options.get("auto_index")
        )

        try:
            tickers = (
                TickerFileReader()
                .read()
            )

        except TickerFileError as exc:
            self.stderr.write(
                self.style.ERROR(
                    str(exc)
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Watcher started: "
                f"{len(tickers)} ticker(s)"
            )
        )

        self.stdout.write(
            "Automatic indexing: "
            + (
                "ENABLED"
                if auto_index
                else "DISABLED"
            )
        )

        processor = TickerProcessor(
            auto_index=auto_index,
        )

        total_downloaded = 0
        total_skipped = 0
        total_failed = 0

        total_indexed = 0
        total_index_failed = 0

        processed = 0
        invalid = 0

        for index, ticker in enumerate(
            tickers,
            start=1,
        ):
            self.stdout.write("")

            self.stdout.write(
                f"[{index}/{len(tickers)}] "
                f"Processing {ticker}..."
            )

            try:
                result = processor.process(
                    ticker
                )

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
                        f"{ticker}: "
                        f"processing failed - {exc}"
                    )
                )

                continue

            processed += 1

            total_downloaded += (
                result["downloaded"]
            )

            total_skipped += (
                result["skipped"]
            )

            total_failed += (
                result["failed"]
            )

            total_indexed += (
                result.get(
                    "indexed",
                    0,
                )
            )

            total_index_failed += (
                result.get(
                    "index_failed",
                    0,
                )
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"{ticker}: "
                    f"discovered="
                    f"{result['discovered']} "
                    f"downloaded="
                    f"{result['downloaded']} "
                    f"skipped="
                    f"{result['skipped']} "
                    f"failed="
                    f"{result['failed']} "
                    f"indexed="
                    f"{result.get('indexed', 0)} "
                    f"index_failed="
                    f"{result.get('index_failed', 0)}"
                )
            )

            for (
                form,
                form_result,
            ) in result["forms"].items():

                self.stdout.write(
                    f"  {form}: "
                    f"discovered="
                    f"{form_result['discovered']} "
                    f"downloaded="
                    f"{form_result['downloaded']} "
                    f"skipped="
                    f"{form_result['skipped']} "
                    f"failed="
                    f"{form_result['failed']} "
                    f"indexed="
                    f"{form_result.get('indexed', 0)} "
                    f"index_failed="
                    f"{form_result.get('index_failed', 0)}"
                )

                for error in (
                    form_result["errors"]
                ):
                    self.stderr.write(
                        self.style.WARNING(
                            f"    {error}"
                        )
                    )

        self.stdout.write("")
        self.stdout.write(
            "=" * 60
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Watcher complete"
            )
        )

        self.stdout.write(
            f"Tickers in file: "
            f"{len(tickers)}"
        )

        self.stdout.write(
            f"Successfully processed: "
            f"{processed}"
        )

        self.stdout.write(
            f"Invalid/skipped tickers: "
            f"{invalid}"
        )

        self.stdout.write(
            f"Documents downloaded: "
            f"{total_downloaded}"
        )

        self.stdout.write(
            f"Duplicates skipped: "
            f"{total_skipped}"
        )

        self.stdout.write(
            f"Failures: "
            f"{total_failed}"
        )

        self.stdout.write(
            f"Filings indexed: "
            f"{total_indexed}"
        )

        self.stdout.write(
            f"Indexing failures: "
            f"{total_index_failed}"
        )