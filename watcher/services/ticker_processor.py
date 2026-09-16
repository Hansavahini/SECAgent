from pathlib import Path

from watcher.services.download_registry import DownloadRegistry
from watcher.services.filing_discovery import FilingDiscovery
from watcher.services.filing_downloader import FilingDownloader
from watcher.services.notification_service import FilingNotificationService
from watcher.services.ticker_resolver import TickerResolver

from watcher.knowledge_base.ingestion.filing_indexing_service import (
    FilingIndexingService,
)
from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.knowledge_base.summarization.filing_summary_service import (
    FilingSummaryService,
)


class TickerProcessor:
    """
    Process one ticker completely before moving to the next ticker.

    Processing order:
        8-K
        10-K
        10-Q

    Duplicate identity remains:
        CIK + accession number + sequence

    Existing behavior remains authoritative:
        discover
        -> duplicate check
        -> download
        -> register
        -> optional indexing

    New behavior:
        when automatic indexing is enabled and indexing succeeds,
        generate/reuse the existing FilingSummaryService summary,
        let that service persist its existing PostgreSQL cache,
        then email that same summary.

    Email failures are isolated and never undo successful SEC, database,
    indexing, or summary work.
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
        registration_service=None,
        indexing_service=None,
        summary_service=None,
        notification_service=None,
        auto_index=False,
    ):
        self.resolver = (
            resolver
            or TickerResolver()
        )

        self.discovery = (
            discovery
            or FilingDiscovery()
        )

        self.downloader = (
            downloader
            or FilingDownloader()
        )

        self.registry = (
            registry
            or DownloadRegistry()
        )

        self.registration_service = (
            registration_service
            or FilingRegistrationService()
        )

        self.auto_index = bool(
            auto_index
        )

        self.indexing_service = (
            indexing_service
        )

        if (
            self.auto_index
            and self.indexing_service is None
        ):
            self.indexing_service = (
                FilingIndexingService()
            )

        self.summary_service = (
            summary_service
        )

        if (
            self.auto_index
            and self.summary_service is None
        ):
            self.summary_service = (
                FilingSummaryService()
            )

        self.notification_service = (
            notification_service
            or FilingNotificationService()
        )

    @staticmethod
    def _print_new_filing(
        *,
        ticker,
        form_type,
        filing_date,
        accession_number,
        sequence,
        filename,
        saved_path,
        source_url,
    ):
        print()
        print("=" * 70)
        print("NEW SEC FILING DETECTED")
        print("=" * 70)
        print(f"Ticker       : {ticker}")
        print(f"Form         : {form_type}")
        print(f"Filing Date  : {filing_date}")
        print(f"Accession    : {accession_number}")
        print(f"Sequence     : {sequence}")
        print(f"Filename     : {filename}")
        print(f"Saved Path   : {saved_path}")
        print(f"SEC URL      : {source_url}")
        print("DOWNLOAD     : SUCCESS")
        print("=" * 70)

    def _send_summary_email(
        self,
        *,
        summary_result,
        filename,
        saved_path,
        source_url,
    ):
        """
        Email the exact summary already returned by FilingSummaryService.

        FilingSummaryService has already persisted/reused the PostgreSQL
        FilingSummaryCache before this method is called.
        """
        summary_text = str(
            summary_result.summary
            or ""
        ).strip()

        if not summary_text:
            print(
                "EMAIL        : NOT SENT "
                "(generated summary is empty)"
            )
            return False

        try:
            email_sent = (
                self.notification_service
                .send_new_filing_notification(
                    ticker=summary_result.ticker,
                    form_type=summary_result.form,
                    filename=filename,
                    filing_date=summary_result.filing_date,
                    accession_number=(
                        summary_result.accession_number
                    ),
                    local_path=saved_path,
                    sec_url=source_url,
                    summary_text=summary_text,
                )
            )
        except Exception as exc:
            # Defensive isolation. notification_service itself should
            # already catch SMTP failures, but email must never break
            # the existing processing flow.
            print(
                "EMAIL        : FAILED "
                f"({exc})"
            )
            return False

        if email_sent:
            print(
                "EMAIL        : SENT SUCCESSFULLY"
            )

            recipient = getattr(
                self.notification_service,
                "recipient",
                "",
            )

            if recipient:
                print(
                    f"Recipient    : {recipient}"
                )

            return True

        print(
            "EMAIL        : NOT SENT "
            "(check SMTP configuration/logs)"
        )
        return False

    def process(self, ticker):
        """
        Process all supported forms for one ticker.

        Returns the existing summary dictionary.

        Invalid ticker resolution is allowed to raise so the outer
        watcher command can log it and continue with the next ticker.
        """

        company = self.resolver.resolve(
            ticker
        )

        resolved_ticker = company[
            "ticker"
        ]

        cik = company[
            "cik"
        ]

        summary = {
            "ticker": resolved_ticker,
            "cik": cik,
            "name": company.get(
                "name",
                "",
            ),
            "discovered": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "indexed": 0,
            "index_failed": 0,
            "forms": {},
        }

        for form in self.FORMS:
            form_summary = {
                "discovered": 0,
                "downloaded": 0,
                "skipped": 0,
                "failed": 0,
                "indexed": 0,
                "index_failed": 0,
                "errors": [],
            }

            summary[
                "forms"
            ][form] = form_summary

            # Preserve existing behavior: create the required folder
            # even when no filings exist.
            directory = (
                self.downloader
                .get_download_dir(
                    ticker=resolved_ticker,
                    form=form,
                )
            )

            directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            try:
                filings = (
                    self.discovery
                    .list_filings(
                        cik=cik,
                        form=form,
                    )
                )

            except Exception as exc:
                form_summary[
                    "failed"
                ] += 1

                summary[
                    "failed"
                ] += 1

                form_summary[
                    "errors"
                ].append(
                    f"Discovery failed: {exc}"
                )

                continue

            form_summary[
                "discovered"
            ] = len(filings)

            summary[
                "discovered"
            ] += len(filings)

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
                    # Resolve exact SEC document first so its sequence
                    # is known before duplicate checking.
                    base_url, document = (
                        self.downloader
                        .resolve_document(
                            cik=cik,
                            accession_number=(
                                accession_number
                            ),
                            file_type=form,
                            hint_filename=(
                                primary_document
                            ),
                        )
                    )

                    sequence = str(
                        document.get(
                            "sequence"
                        )
                        or ""
                    ).strip()

                    if not sequence:
                        raise ValueError(
                            "SEC document sequence "
                            "could not be resolved"
                        )

                    # Preserve existing duplicate protection.
                    if self.registry.is_downloaded(
                        cik,
                        accession_number,
                        sequence,
                    ):
                        form_summary[
                            "skipped"
                        ] += 1

                        summary[
                            "skipped"
                        ] += 1

                        continue

                    file_type = (
                        document.get(
                            "type"
                        )
                        or form
                    )

                    original_filename = (
                        document[
                            "filename"
                        ]
                    )

                    local_filename = (
                        self.downloader
                        .build_filename(
                            form=form,
                            file_type=file_type,
                            filing_date=filing_date,
                            original_filename=(
                                original_filename
                            ),
                        )
                    )

                    # Only genuinely new filings reach this download.
                    result = (
                        self.downloader
                        .download(
                            cik=cik,
                            accession_number=(
                                accession_number
                            ),
                            file_type=file_type,
                            sequence=sequence,
                            local_filename=(
                                local_filename
                            ),
                            hint_filename=(
                                primary_document
                            ),
                            document=document,
                            base_url=base_url,
                            ticker=resolved_ticker,
                            form=form,
                        )
                    )

                    downloaded_sequence = str(
                        result.get(
                            "sequence"
                        )
                        or sequence
                    ).strip()

                    # Preserve existing semantics: mark downloaded only
                    # after the final file has successfully been written.
                    self.registry.mark_downloaded(
                        cik,
                        accession_number,
                        downloaded_sequence,
                    )

                    form_summary[
                        "downloaded"
                    ] += 1

                    summary[
                        "downloaded"
                    ] += 1

                    saved_path = str(
                        result[
                            "path"
                        ]
                    )

                    saved_filename = (
                        Path(
                            saved_path
                        ).name
                    )

                    resolved_form_type = str(
                        document.get(
                            "type"
                        )
                        or form
                    ).strip()

                    source_url = str(
                        result.get(
                            "url"
                        )
                        or ""
                    ).strip()

                    # Show the exact new file immediately.
                    # Email is NOT sent here.
                    self._print_new_filing(
                        ticker=resolved_ticker,
                        form_type=resolved_form_type,
                        filing_date=filing_date,
                        accession_number=(
                            accession_number
                        ),
                        sequence=(
                            downloaded_sequence
                        ),
                        filename=saved_filename,
                        saved_path=saved_path,
                        source_url=source_url,
                    )

                    # Preserve existing KB/PostgreSQL registration flow.
                    try:
                        registered_filing = (
                            self.registration_service
                            .register(
                                ticker=(
                                    resolved_ticker
                                ),
                                cik=cik,
                                company_name=(
                                    company.get(
                                        "name",
                                        "",
                                    )
                                ),
                                form=form,
                                accession_number=(
                                    accession_number
                                ),
                                sequence=(
                                    downloaded_sequence
                                ),
                                filing_date=(
                                    filing_date
                                ),
                                primary_document=(
                                    primary_document
                                ),
                                local_path=(
                                    result[
                                        "path"
                                    ]
                                ),
                                source_url=(
                                    result[
                                        "url"
                                    ]
                                ),
                            )
                        )

                        print(
                            "REGISTRATION : SUCCESS"
                        )

                    except Exception as exc:
                        form_summary[
                            "errors"
                        ].append(
                            "Knowledge-base registration "
                            "failed for "
                            f"{accession_number}: "
                            f"{exc}"
                        )

                        print(
                            "REGISTRATION : FAILED "
                            f"({exc})"
                        )

                        # Preserve existing behavior: a successful SEC
                        # download remains successful even if KB
                        # registration fails.
                        continue

                    # Preserve existing optional automatic indexing.
                    if (
                        self.auto_index
                        and self.indexing_service
                        is not None
                    ):
                        try:
                            (
                                self.indexing_service
                                .index_filing(
                                    registered_filing
                                )
                            )

                            form_summary[
                                "indexed"
                            ] += 1

                            summary[
                                "indexed"
                            ] += 1

                            print(
                                "INDEXING     : SUCCESS"
                            )

                        except Exception as exc:
                            form_summary[
                                "index_failed"
                            ] += 1

                            summary[
                                "index_failed"
                            ] += 1

                            form_summary[
                                "errors"
                            ].append(
                                "Knowledge-base indexing "
                                "failed for "
                                f"{accession_number}: "
                                f"{exc}"
                            )

                            print(
                                "INDEXING     : FAILED "
                                f"({exc})"
                            )

                            # Preserve isolation: download and
                            # registration remain successful.
                            continue

                        # Summary generation is new orchestration, but it
                        # uses your EXISTING FilingSummaryService exactly.
                        if self.summary_service is None:
                            print(
                                "SUMMARY      : NOT AVAILABLE"
                            )
                            print(
                                "EMAIL        : NOT SENT"
                            )
                            print("=" * 70)
                            print()
                            continue

                        try:
                            summary_result = (
                                self.summary_service
                                .summarize_filing(
                                    registered_filing.id
                                )
                            )

                            # summarize_filing() returns only after its
                            # existing PostgreSQL cache save/reuse path.
                            print(
                                "SUMMARY      : GENERATED SUCCESSFULLY"
                            )
                            print(
                                "POSTGRESQL   : SUMMARY STORED/REUSED"
                            )

                        except Exception as exc:
                            form_summary[
                                "errors"
                            ].append(
                                "Filing summary failed for "
                                f"{accession_number}: "
                                f"{exc}"
                            )

                            # Do not change existing index counters and
                            # do not undo successful work.
                            print(
                                "SUMMARY      : FAILED "
                                f"({exc})"
                            )
                            print(
                                "EMAIL        : NOT SENT"
                            )
                            print("=" * 70)
                            print()
                            continue

                        # New side effect only: email the exact same
                        # summary returned by the existing service.
                        self._send_summary_email(
                            summary_result=(
                                summary_result
                            ),
                            filename=(
                                saved_filename
                            ),
                            saved_path=(
                                saved_path
                            ),
                            source_url=(
                                source_url
                            ),
                        )

                        print("=" * 70)
                        print()

                    elif self.auto_index:
                        print(
                            "INDEXING     : NOT AVAILABLE"
                        )
                        print(
                            "SUMMARY      : NOT GENERATED"
                        )
                        print(
                            "EMAIL        : NOT SENT"
                        )
                        print("=" * 70)
                        print()

                    else:
                        # Preserve the meaning of the existing
                        # auto_index flag. We do not silently change
                        # your old behavior.
                        print(
                            "INDEXING     : DISABLED"
                        )
                        print(
                            "SUMMARY      : NOT GENERATED"
                        )
                        print(
                            "EMAIL        : NOT SENT"
                        )
                        print("=" * 70)
                        print()

                except Exception as exc:
                    form_summary[
                        "failed"
                    ] += 1

                    summary[
                        "failed"
                    ] += 1

                    form_summary[
                        "errors"
                    ].append(
                        f"{accession_number}: "
                        f"{exc}"
                    )

                    # One bad filing must not stop remaining filings.
                    continue

        return summary
