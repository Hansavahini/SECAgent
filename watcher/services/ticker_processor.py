from pathlib import Path

from watcher.services.download_registry import (
    DownloadRegistry,
)
from watcher.services.filing_discovery import (
    FilingDiscovery,
)
from watcher.services.filing_downloader import (
    FilingDownloader,
)
from watcher.services.filing_metadata_service import (
    FilingMetadataService,
)
from watcher.services.notification_service import (
    FilingNotificationService,
)
from watcher.services.ticker_resolver import (
    TickerResolver,
)
from watcher.services.filing_email_service import (
    FilingEmailService,
)
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
    Process one ticker completely before moving to the next.

    Existing flow remains:

        discover
        -> duplicate check
        -> download
        -> registry
        -> register
        -> optional indexing
        -> summary
        -> email

    EDGAR metadata handling is additive.
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
        filing_metadata_service=None,
        filing_email_service=None,
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

        # ---------------------------------------------------------
        # Existing indexing behavior.
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # Existing summary behavior.
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # Existing notification service.
        # ---------------------------------------------------------

        self.notification_service = (
            notification_service
            or FilingNotificationService()
        )

        # ---------------------------------------------------------
        # Optional EDGAR metadata coordinator.
        # ---------------------------------------------------------

        self.filing_metadata_service = (
            filing_metadata_service
            or FilingMetadataService()
        )

        # ---------------------------------------------------------
        # Email coordinator.
        #
        # Reuses the SAME notification service so all existing
        # SMTP settings / recipient configuration stay unchanged.
        # ---------------------------------------------------------

        self.filing_email_service = (
            filing_email_service
            or FilingEmailService(
                notification_service=(
                    self.notification_service
                )
            )
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

    @staticmethod
    def _print_metadata(metadata):
        if metadata.accepted_at is not None:
            print(
                "EDGAR ACCEPT : "
                f"{metadata.accepted_at_display}"
            )
        else:
            print(
                "EDGAR ACCEPT : NOT AVAILABLE"
            )

        if metadata.entry_session is not None:
            print(
                "ENTRY SESSION: "
                f"{metadata.entry_session}"
            )
        else:
            print(
                "ENTRY SESSION: NOT AVAILABLE"
            )

        if metadata.sec_item_codes:
            print(
                "SEC ITEMS    : "
                + ", ".join(
                    metadata.sec_item_codes
                )
            )

    @staticmethod
    def _print_item_verification(
        verification,
        error,
    ):
        if error:
            print(
                "ITEM VERIFY  : UNAVAILABLE "
                f"({error})"
            )
            return

        if verification is None:
            return

        print(
            "ITEM VERIFY  : "
            f"{verification.status}"
        )

        print(
            "SEC ITEMS    : "
            + (
                ", ".join(
                    verification.sec_items
                )
                or "NOT AVAILABLE"
            )
        )

        print(
            "PARSED ITEMS : "
            + (
                ", ".join(
                    verification.parsed_items
                )
                or "NOT AVAILABLE"
            )
        )

        if verification.missing_from_parser:
            print(
                "ITEM MISSING : "
                + ", ".join(
                    verification.missing_from_parser
                )
            )

        if verification.extra_in_parser:
            print(
                "ITEM EXTRA   : "
                + ", ".join(
                    verification.extra_in_parser
                )
            )

    def _send_summary_email(
        self,
        *,
        summary_result,
        filename,
        saved_path,
        source_url,
        metadata=None,
        item_verification=None,
    ):
        try:
            email_sent = (
                self.filing_email_service
                .send(
                    summary_result=(
                        summary_result
                    ),
                    filename=filename,
                    saved_path=saved_path,
                    source_url=source_url,
                    metadata=metadata,
                    item_verification=(
                        item_verification
                    ),
                )
            )

        except Exception as exc:
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

                    # ---------------------------------------------
                    # Optional EDGAR metadata.
                    # ---------------------------------------------

                    metadata = (
                        self.filing_metadata_service
                        .prepare(
                            filing=filing,
                            expected_cik=cik,
                            expected_company_name=(
                                company.get(
                                    "name",
                                    "",
                                )
                            ),
                            expected_ticker=(
                                resolved_ticker
                            ),
                        )
                    )

                    if metadata.timestamp_error:
                        print(
                            "EDGAR TIME   : UNAVAILABLE "
                            f"({metadata.timestamp_error})"
                        )

                    if metadata.session_error:
                        print(
                            "ENTRY SESSION: UNAVAILABLE "
                            f"({metadata.session_error})"
                        )
                    if metadata.company_verification_error:
                        print(
                            "COMPANY VERIFY: UNAVAILABLE "
                            f"({metadata.company_verification_error})"
                        )

                    elif metadata.company_verification is not None:
                        verification = (
                            metadata.company_verification
                        )

                        print(
                            "COMPANY VERIFY: "
                            f"{verification.status}"
                        )

                        print(
                            "SEC COMPANY   : "
                            f"{verification.sec_company_name or 'NOT AVAILABLE'}"
                        )

                        print(
                            "SEC CIK       : "
                            f"{verification.sec_cik or 'NOT AVAILABLE'}"
                        )
                    self._print_new_filing(
                        ticker=resolved_ticker,
                        form_type=(
                            resolved_form_type
                        ),
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

                    # ---------------------------------------------
                    # PostgreSQL registration.
                    # ---------------------------------------------

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
                                accepted_at=(
                                    metadata.accepted_at
                                ),
                            )
                        )

                        print(
                            "REGISTRATION : SUCCESS"
                        )

                        self._print_metadata(
                            metadata
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

                        continue

                    # ---------------------------------------------
                    # Existing automatic indexing.
                    # ---------------------------------------------

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

                            continue

                        # -----------------------------------------
                        # Structured 8-K item verification.
                        # -----------------------------------------

                        (
                            item_verification,
                            verification_error,
                        ) = (
                            self.filing_metadata_service
                            .verify_items(
                                filing=(
                                    registered_filing
                                ),
                                form=form,
                                sec_item_codes=(
                                    metadata.sec_item_codes
                                ),
                            )
                        )

                        self._print_item_verification(
                            item_verification,
                            verification_error,
                        )

                        # -----------------------------------------
                        # Existing summary behavior.
                        # -----------------------------------------

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

                            print(
                                "SUMMARY      : "
                                "GENERATED SUCCESSFULLY"
                            )

                            print(
                                "POSTGRESQL   : "
                                "SUMMARY STORED/REUSED"
                            )

                        except Exception as exc:
                            form_summary[
                                "errors"
                            ].append(
                                "Filing summary failed for "
                                f"{accession_number}: "
                                f"{exc}"
                            )

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

                        # -----------------------------------------
                        # Existing email behavior.
                        # -----------------------------------------

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
                            metadata=metadata,
                            item_verification=(
                                item_verification
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

                    # One filing failure must not stop the ticker.
                    continue

        return summary