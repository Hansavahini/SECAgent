from watcher.services.download_registry import DownloadRegistry
from watcher.services.filing_discovery import FilingDiscovery
from watcher.services.filing_downloader import FilingDownloader
from watcher.services.ticker_resolver import TickerResolver


class TickerProcessor:
    """
    Process one ticker completely before moving to the next ticker.

    Processing order:
        8-K
        10-K
        10-Q

    Duplicate identity remains:
        CIK + accession number + sequence
    """

    FORMS = (
        "8-K",
        "10-K",
        "10-Q",
    )

    def __init__(
        self,
        resolver=None,
        discovery=None,
        downloader=None,
        registry=None,
    ):
        self.resolver = resolver or TickerResolver()
        self.discovery = discovery or FilingDiscovery()
        self.downloader = downloader or FilingDownloader()
        self.registry = registry or DownloadRegistry()

    def process(self, ticker):
        """
        Process all supported forms for one ticker.

        Returns a summary dictionary.

        Invalid ticker resolution is allowed to raise so the outer
        watcher command can log it and continue with the next ticker.
        """

        company = self.resolver.resolve(ticker)

        resolved_ticker = company["ticker"]
        cik = company["cik"]

        summary = {
            "ticker": resolved_ticker,
            "cik": cik,
            "name": company.get("name", ""),
            "discovered": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "forms": {},
        }

        for form in self.FORMS:
            form_summary = {
                "discovered": 0,
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "errors": [],
            }

            summary["forms"][form] = form_summary

            # Create the required folder even when no filings exist.
            directory = self.downloader.get_download_dir(
                ticker=resolved_ticker,
                form=form,
            )

            directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            try:
                filings = self.discovery.list_filings(
                    cik=cik,
                    form=form,
                )

            except Exception as exc:
                form_summary["failed"] += 1
                summary["failed"] += 1

                form_summary["errors"].append(
                    f"Discovery failed: {exc}"
                )

                continue

            form_summary["discovered"] = len(filings)
            summary["discovered"] += len(filings)

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
                    # Resolve the real SEC document first so that we know
                    # its exact sequence before checking the registry.
                    base_url, document = (
                        self.downloader.resolve_document(
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
                            "SEC document sequence could not be resolved"
                        )

                    # Existing duplicate protection remains unchanged.
                    if self.registry.is_downloaded(
                        cik,
                        accession_number,
                        sequence,
                    ):
                        form_summary["skipped"] += 1
                        summary["skipped"] += 1
                        continue

                    file_type = (
                        document.get("type")
                        or form
                    )

                    original_filename = document[
                        "filename"
                    ]

                    local_filename = (
                        self.downloader.build_filename(
                            form=form,
                            file_type=file_type,
                            filing_date=filing_date,
                            original_filename=original_filename,
                        )
                    )

                    result = self.downloader.download(
                        cik=cik,
                        accession_number=accession_number,
                        file_type=file_type,
                        sequence=sequence,
                        local_filename=local_filename,
                        hint_filename=primary_document,
                        document=document,
                        base_url=base_url,
                        ticker=resolved_ticker,
                        form=form,
                    )

                    downloaded_sequence = str(
                        result.get("sequence")
                        or sequence
                    ).strip()

                    # Mark only after the final file write succeeds.
                    self.registry.mark_downloaded(
                        cik,
                        accession_number,
                        downloaded_sequence,
                    )

                    form_summary["downloaded"] += 1
                    summary["downloaded"] += 1

                except Exception as exc:
                    form_summary["failed"] += 1
                    summary["failed"] += 1

                    form_summary["errors"].append(
                        f"{accession_number}: {exc}"
                    )

                    # One bad filing must not stop the remaining filings.
                    continue

        return summary