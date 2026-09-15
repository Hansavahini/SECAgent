from django.core.management.base import BaseCommand

from watcher.models import (
    DocumentSummaryCache,
    Filing,
    FilingSummaryCache,
)
from watcher.knowledge_base.summarization.filing_summary_service import (
    FilingSummaryError,
    FilingSummaryService,
)


class Command(BaseCommand):
    help = (
        "Generate missing/stale PostgreSQL document and filing summaries "
        "from already-indexed SEC filing chunks. "
        "This command does not download, extract, chunk, or embed filings."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ticker",
            type=str,
            help="Process only one ticker, for example AAPL.",
        )

        parser.add_argument(
            "--form",
            type=str,
            help="Process only one SEC form, for example 10-Q.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            help=(
                "Maximum number of filings requiring summary work "
                "to process."
            ),
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Show filings/documents requiring work without "
                "generating summaries."
            ),
        )

    def handle(self, *args, **options):
        ticker = self._normalize_ticker(
            options.get("ticker")
        )

        form = self._normalize_form(
            options.get("form")
        )

        limit = options.get("limit")
        dry_run = options.get(
            "dry_run",
            False,
        )

        if limit is not None and limit <= 0:
            self.stderr.write(
                self.style.ERROR(
                    "--limit must be greater than 0."
                )
            )
            return

        queryset = (
            Filing.objects
            .select_related("company")
            .filter(
                documents__chunks__isnull=False
            )
            .distinct()
            .order_by(
                "company__ticker",
                "filing_date",
                "id",
            )
        )

        if ticker:
            queryset = queryset.filter(
                company__ticker__iexact=ticker
            )

        if form:
            queryset = queryset.filter(
                form=form
            )

        filing_service = FilingSummaryService()

        document_service = (
            filing_service.document_summary_service
        )

        self.stdout.write(
            "=== DOCUMENT + FILING SUMMARY BACKFILL ==="
        )

        self.stdout.write(
            f"Ticker filter: {ticker or 'ALL'}"
        )

        self.stdout.write(
            f"Form filter: {form or 'ALL'}"
        )

        self.stdout.write(
            f"Dry run: {'YES' if dry_run else 'NO'}"
        )

        if limit:
            self.stdout.write(
                f"Work limit: {limit}"
            )
        else:
            self.stdout.write(
                "Work limit: ALL"
            )

        work_selected = 0
        completed = 0
        skipped = 0
        failed = 0

        documents_generated = 0
        documents_reused = 0

        for filing in queryset:
            documents = list(
                filing.documents
                .filter(
                    chunks__isnull=False
                )
                .distinct()
                .order_by(
                    "-is_primary",
                    "sequence",
                    "id",
                )
            )

            if not documents:
                continue

            document_states = []

            for document in documents:
                is_current = (
                    self._document_cache_is_current(
                        service=document_service,
                        document=document,
                    )
                )

                document_states.append(
                    (
                        document,
                        is_current,
                    )
                )

            filing_current = (
                self._filing_cache_is_current(
                    service=filing_service,
                    filing=filing,
                )
            )

            all_documents_current = all(
                is_current
                for _, is_current
                in document_states
            )

            if (
                all_documents_current
                and filing_current
            ):
                skipped += 1
                continue

            if (
                limit is not None
                and work_selected >= limit
            ):
                break

            work_selected += 1

            label = (
                f"{filing.company.ticker} "
                f"{filing.form} "
                f"{filing.filing_date or 'UNKNOWN_DATE'} "
                f"{filing.accession_number}"
            )

            missing_documents = [
                document
                for document, is_current
                in document_states
                if not is_current
            ]

            if dry_run:
                self.stdout.write(
                    f"WOULD PROCESS: {label} "
                    f"| documents={len(documents)} "
                    f"| document_summaries_needed="
                    f"{len(missing_documents)} "
                    f"| filing_summary_needed="
                    f"{'NO' if filing_current else 'YES'}"
                )
                continue

            try:
                for document, is_current in document_states:
                    if is_current:
                        documents_reused += 1

                    else:
                        document_service.summarize_document(
                            document.id
                        )

                        documents_generated += 1

                        self.stdout.write(
                            self.style.SUCCESS(
                                "  DOCUMENT GENERATED: "
                                f"{document.document_name}"
                            )
                        )

                result = (
                    filing_service.summarize_filing(
                        filing.id
                    )
                )

                completed += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"FILING READY: {label} "
                        f"| documents="
                        f"{result.document_count} "
                        f"| chunks="
                        f"{result.chunk_count} "
                        f"| summary_chars="
                        f"{len(result.summary)}"
                    )
                )

            except FilingSummaryError as exc:
                failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"FAILED: {label} | {exc}"
                    )
                )

            except Exception as exc:
                failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"FAILED: {label} "
                        f"| {exc.__class__.__name__}: "
                        f"{exc}"
                    )
                )

        self.stdout.write("")
        self.stdout.write(
            "=== SUMMARY ==="
        )

        self.stdout.write(
            f"Filings requiring work: "
            f"{work_selected}"
        )

        self.stdout.write(
            f"Fully cached filings skipped: "
            f"{skipped}"
        )

        if dry_run:
            self.stdout.write(
                "No summaries generated "
                "(dry run)."
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Filings completed: "
                f"{completed}"
            )
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Document summaries generated: "
                f"{documents_generated}"
            )
        )

        self.stdout.write(
            f"Document summaries reused: "
            f"{documents_reused}"
        )

        if failed:
            self.stdout.write(
                self.style.ERROR(
                    f"Failed filings: {failed}"
                )
            )
        else:
            self.stdout.write(
                "Failed filings: 0"
            )

    def _document_cache_is_current(
        self,
        *,
        service,
        document,
    ):
        chunks = list(
            document.chunks
            .order_by("chunk_index")
        )

        if not chunks:
            return False

        content_signature = (
            service._build_content_signature(
                document=document,
                chunks=chunks,
            )
        )

        model_name = (
            service._model_name()
        )

        return (
            DocumentSummaryCache.objects
            .filter(
                document_id=document.id,
                content_signature=(
                    content_signature
                ),
                model_name=model_name,
                prompt_version=(
                    service
                    .SUMMARY_PIPELINE_VERSION
                ),
            )
            .exists()
        )

    def _filing_cache_is_current(
        self,
        *,
        service,
        filing,
    ):
        try:
            content_signature = (
                service._build_content_signature(
                    filing
                )
            )
        except FilingSummaryError:
            return False

        model_name = (
            service._model_name()
        )

        return (
            FilingSummaryCache.objects
            .filter(
                filing_id=filing.id,
                content_signature=(
                    content_signature
                ),
                model_name=model_name,
                prompt_version=(
                    service
                    .SUMMARY_PIPELINE_VERSION
                ),
            )
            .exists()
        )

    @staticmethod
    def _normalize_ticker(value):
        if not value:
            return None

        value = (
            value.strip()
            .upper()
        )

        return value or None

    @staticmethod
    def _normalize_form(value):
        if not value:
            return None

        compact = (
            value.strip()
            .upper()
            .replace("-", "")
            .replace(" ", "")
        )

        supported_forms = {
            "8K": "8-K",
            "10K": "10-K",
            "10Q": "10-Q",
        }

        return supported_forms.get(
            compact,
            value.strip().upper(),
        )