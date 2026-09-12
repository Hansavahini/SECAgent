from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.services.download_registry import DownloadRegistry
from watcher.services.filing_discovery import FilingDiscovery
from watcher.services.filing_downloader import FilingDownloader
from watcher.services.ticker_resolver import TickerResolver


class Command(BaseCommand):
    help = (
        "Reconcile existing SEC_DOWNLOAD_DIR filings with the "
        "PostgreSQL knowledge-base registry."
    )

    FORMS = (
        "8-K",
        "10-K",
        "10-Q",
    )

    def add_arguments(self, parser):
        selection = parser.add_mutually_exclusive_group(
            required=True
        )

        selection.add_argument(
            "--ticker",
            help="Process one ticker, for example AAPL.",
        )

        selection.add_argument(
            "--all",
            action="store_true",
            help=(
                "Process all ticker directories found inside "
                "SEC_DOWNLOAD_DIR."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Limit number of ticker folders processed. "
                "Useful for testing --all safely."
            ),
        )

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write matched filings to PostgreSQL.",
        )

    def handle(self, *args, **options):
        downloader = FilingDownloader()

        tickers = self._get_tickers(
            downloader=downloader,
            ticker=options.get("ticker"),
            process_all=options["all"],
            limit=options["limit"],
        )

        apply_changes = options["apply"]

        self.stdout.write(
            f"SEC download directory: "
            f"{settings.SEC_DOWNLOAD_DIR}"
        )

        self.stdout.write(
            "Mode: "
            + ("APPLY" if apply_changes else "DRY RUN")
        )

        self.stdout.write(
            f"Ticker folders selected: {len(tickers)}"
        )

        totals = {
            "tickers_processed": 0,
            "ticker_failures": 0,
            "matched": 0,
            "registered": 0,
            "not_downloaded": 0,
            "missing": 0,
            "filing_errors": 0,
        }

        for index, ticker in enumerate(
            tickers,
            start=1,
        ):
            self.stdout.write(
                "\n"
                + "=" * 70
            )

            self.stdout.write(
                f"[{index}/{len(tickers)}] "
                f"Processing {ticker}"
            )

            try:
                result = self._process_ticker(
                    ticker=ticker,
                    apply_changes=apply_changes,
                    downloader=downloader,
                )

            except Exception as exc:
                totals["ticker_failures"] += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"{ticker}: ticker processing failed: "
                        f"{exc}"
                    )
                )

                # One bad ticker must not stop the batch.
                continue

            totals["tickers_processed"] += 1

            for key in (
                "matched",
                "registered",
                "not_downloaded",
                "missing",
                "filing_errors",
            ):
                totals[key] += result[key]

        self.stdout.write(
            "\n"
            + "=" * 70
        )

        self.stdout.write(
            "FINAL SUMMARY"
        )

        self.stdout.write(
            f"Ticker folders selected: {len(tickers)}"
        )

        self.stdout.write(
            f"Tickers processed: "
            f"{totals['tickers_processed']}"
        )

        self.stdout.write(
            f"Ticker failures: "
            f"{totals['ticker_failures']}"
        )

        self.stdout.write(
            f"Matched files: "
            f"{totals['matched']}"
        )

        self.stdout.write(
            f"Registered: "
            f"{totals['registered']}"
        )

        self.stdout.write(
            f"Registry entries not present: "
            f"{totals['not_downloaded']}"
        )

        self.stdout.write(
            f"Downloaded file not located: "
            f"{totals['missing']}"
        )

        self.stdout.write(
            f"Filing errors: "
            f"{totals['filing_errors']}"
        )

    def _get_tickers(
        self,
        *,
        downloader,
        ticker,
        process_all,
        limit,
    ):
        if limit is not None and limit <= 0:
            raise CommandError(
                "--limit must be greater than zero."
            )

        if ticker:
            return [
                ticker.strip().upper()
            ]

        if not process_all:
            raise CommandError(
                "Specify either --ticker or --all."
            )

        root = Path(
            settings.SEC_DOWNLOAD_DIR
        )

        if not root.is_dir():
            raise CommandError(
                f"SEC_DOWNLOAD_DIR does not exist: {root}"
            )

        tickers = sorted(
            entry.name.strip().upper()
            for entry in root.iterdir()
            if entry.is_dir()
        )

        if limit is not None:
            tickers = tickers[:limit]

        if not tickers:
            raise CommandError(
                f"No ticker directories found in {root}"
            )

        return tickers

    def _process_ticker(
        self,
        *,
        ticker,
        apply_changes,
        downloader,
    ):
        resolver = TickerResolver()
        discovery = FilingDiscovery()
        registry = DownloadRegistry()
        registrar = FilingRegistrationService()

        company = resolver.resolve(ticker)

        resolved_ticker = company["ticker"]
        cik = company["cik"]

        result = {
            "matched": 0,
            "registered": 0,
            "not_downloaded": 0,
            "missing": 0,
            "filing_errors": 0,
        }

        self.stdout.write(
            f"SEC download directory: "
            f"{settings.SEC_DOWNLOAD_DIR}"
        )

        for form in self.FORMS:
            try:
                filings = discovery.list_filings(
                    cik=cik,
                    form=form,
                )

            except Exception as exc:
                result["filing_errors"] += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"{form}: discovery failed: {exc}"
                    )
                )

                continue

            self.stdout.write(
                f"{form}: {len(filings)} discovered"
            )

            for filing in filings:
                accession_number = filing[
                    "accession_number"
                ]

                primary_document = filing[
                    "primary_document"
                ]

                filing_date = filing[
                    "filing_date"
                ]

                try:
                    base_url, document = (
                        downloader.resolve_document(
                            cik=cik,
                            accession_number=accession_number,
                            file_type=form,
                            hint_filename=primary_document,
                        )
                    )

                    sequence = str(
                        document.get("sequence") or ""
                    ).strip()

                    if not sequence:
                        raise ValueError(
                            "SEC document sequence "
                            "could not be resolved."
                        )

                    if not registry.is_downloaded(
                        cik,
                        accession_number,
                        sequence,
                    ):
                        result["not_downloaded"] += 1
                        continue

                    file_type = (
                        document.get("type")
                        or form
                    )

                    original_filename = document[
                        "filename"
                    ]

                    local_filename = (
                        downloader.build_filename(
                            form=form,
                            file_type=file_type,
                            filing_date=filing_date,
                            original_filename=original_filename,
                        )
                    )

                    directory = (
                        downloader.get_download_dir(
                            ticker=resolved_ticker,
                            form=form,
                        )
                    )

                    local_path = self._find_local_file(
                        downloader=downloader,
                        directory=directory,
                        local_filename=local_filename,
                        accession_number=accession_number,
                    )

                    if local_path is None:
                        result["missing"] += 1

                        self.stdout.write(
                            self.style.WARNING(
                                f"  Missing file: "
                                f"{accession_number}"
                            )
                        )

                        continue

                    result["matched"] += 1

                    self.stdout.write(
                        f"  MATCH "
                        f"{accession_number} -> "
                        f"{local_path.name}"
                    )

                    if not apply_changes:
                        continue

                    source_url = (
                        f"{base_url}/"
                        f"{document['filename']}"
                    )

                    registrar.register(
                        ticker=resolved_ticker,
                        cik=cik,
                        company_name=company.get(
                            "name",
                            "",
                        ),
                        form=form,
                        accession_number=accession_number,
                        sequence=sequence,
                        filing_date=filing_date,
                        primary_document=primary_document,
                        local_path=local_path,
                        source_url=source_url,
                    )

                    result["registered"] += 1

                except Exception as exc:
                    result["filing_errors"] += 1

                    self.stdout.write(
                        self.style.ERROR(
                            f"  {accession_number}: "
                            f"{exc}"
                        )
                    )

                    # One filing failure must not stop the ticker.
                    continue

        return result

    @staticmethod
    def _find_local_file(
        *,
        downloader,
        directory,
        local_filename,
        accession_number,
    ):
        directory = Path(directory)

        exact_path = (
            directory
            / local_filename
        )

        if exact_path.is_file():
            return exact_path

        filename = Path(
            local_filename
        )

        safe_accession = downloader._sanitize(
            accession_number
        )

        tagged_path = directory / (
            f"{filename.stem}_"
            f"{safe_accession}"
            f"{filename.suffix}"
        )

        if tagged_path.is_file():
            return tagged_path

        pattern = (
            f"{filename.stem}_"
            f"{safe_accession}_*"
            f"{filename.suffix}"
        )

        matches = sorted(
            path
            for path in directory.glob(pattern)
            if path.is_file()
        )

        if len(matches) == 1:
            return matches[0]

        return None