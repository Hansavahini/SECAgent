from watcher.knowledge_base.ingestion.filing_indexing_service import (
    FilingIndexingService,
)
from watcher.knowledge_base.ingestion.filing_registration import (
    FilingRegistrationService,
)
from watcher.knowledge_base.summarization.filing_summary_service import (
    FilingSummaryService,
)
from watcher.services.company_verification_service import (
    CompanyVerificationService,
)
from watcher.services.download_registry import DownloadRegistry
from watcher.services.filing_discovery import FilingDiscovery
from watcher.services.filing_downloader import FilingDownloader
from watcher.services.filing_email_service import FilingEmailService
from watcher.services.filing_metadata_service import FilingMetadataService
from watcher.services.filing_pipeline.output import FilingOutputService
from watcher.services.filing_pipeline.post_processing import (
    FilingPostProcessingService,
)
from watcher.services.filing_pipeline.processing import (
    FilingProcessingService,
)
from watcher.services.item_verification_service import (
    ItemVerificationService,
)
from watcher.services.market_session import MarketSessionService
from watcher.services.notification_service import (
    FilingNotificationService,
)
from watcher.services.ticker_resolver import TickerResolver
from watcher.services.timestamp_service import TimestampService


class TickerProcessor:
    """
    High-level ticker orchestrator.

    Responsibilities:
        resolve ticker
        -> iterate supported forms
        -> discover filings
        -> delegate one-filing processing
        -> aggregate results
    """

    FORMS = ("8-K", "10-K", "10-Q")

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
        timestamp_service=None,
        market_session_service=None,
        item_verification_service=None,
        company_verification_service=None,
        output_service=None,
        post_processing_service=None,
        filing_processing_service=None,
    ):
        self.resolver = resolver or TickerResolver()
        self.discovery = discovery or FilingDiscovery()
        self.downloader = downloader or FilingDownloader()
        self.registry = registry or DownloadRegistry()
        self.registration_service = (
            registration_service or FilingRegistrationService()
        )

        self.auto_index = bool(auto_index)

        self.indexing_service = indexing_service
        if self.auto_index and self.indexing_service is None:
            self.indexing_service = FilingIndexingService()

        self.summary_service = summary_service
        if self.auto_index and self.summary_service is None:
            self.summary_service = FilingSummaryService()

        self.notification_service = (
            notification_service or FilingNotificationService()
        )

        if filing_metadata_service is None:
            timestamp_service = (
                timestamp_service or TimestampService()
            )
            market_session_service = (
                market_session_service or MarketSessionService()
            )
            item_verification_service = (
                item_verification_service
                or ItemVerificationService()
            )
            company_verification_service = (
                company_verification_service
                or CompanyVerificationService()
            )

            filing_metadata_service = FilingMetadataService(
                timestamp_service=timestamp_service,
                market_session_service=market_session_service,
                item_verification_service=item_verification_service,
                company_verification_service=(
                    company_verification_service
                ),
            )

        self.filing_metadata_service = filing_metadata_service

        self.filing_email_service = (
            filing_email_service
            or FilingEmailService(
                notification_service=self.notification_service
            )
        )

        self.output_service = (
            output_service or FilingOutputService()
        )

        self.post_processing_service = (
            post_processing_service
            or FilingPostProcessingService(
                auto_index=self.auto_index,
                indexing_service=self.indexing_service,
                summary_service=self.summary_service,
                metadata_service=self.filing_metadata_service,
                email_service=self.filing_email_service,
                notification_service=self.notification_service,
                output_service=self.output_service,
            )
        )

        self.filing_processing_service = (
            filing_processing_service
            or FilingProcessingService(
                downloader=self.downloader,
                registry=self.registry,
                registration_service=self.registration_service,
                metadata_service=self.filing_metadata_service,
                post_processing_service=self.post_processing_service,
                output_service=self.output_service,
            )
        )

    @staticmethod
    def _form_summary():
        return {
            "discovered": 0,
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "indexed": 0,
            "index_failed": 0,
            "errors": [],
        }

    @staticmethod
    def _merge_filing_result(summary, form_summary, result):
        for key in (
            "downloaded",
            "skipped",
            "failed",
            "indexed",
            "index_failed",
        ):
            value = result.get(key, 0)
            form_summary[key] += value
            summary[key] += value

        form_summary["errors"].extend(
            result.get("errors", [])
        )

    def process(self, ticker):
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
            "indexed": 0,
            "index_failed": 0,
            "forms": {},
        }

        for form in self.FORMS:
            form_summary = self._form_summary()
            summary["forms"][form] = form_summary

            directory = self.downloader.get_download_dir(
                ticker=resolved_ticker,
                form=form,
            )
            directory.mkdir(parents=True, exist_ok=True)

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
                result = self.filing_processing_service.process(
                    company=company,
                    ticker=resolved_ticker,
                    cik=cik,
                    form=form,
                    filing=filing,
                )
                self._merge_filing_result(
                    summary,
                    form_summary,
                    result,
                )

        return summary
