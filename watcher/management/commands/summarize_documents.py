from django.core.management.base import BaseCommand
from django.db.models import Subquery

from watcher.knowledge_base.models import (
    DocumentSummaryCache,
    FilingDocument,
)
from watcher.knowledge_base.summarization.document_summary_service import (
    DocumentSummaryError,
    DocumentSummaryService,
)


class Command(BaseCommand):
    help = (
        "Generate and store one summary per SEC FilingDocument. "
        "Documents are processed ticker by ticker."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ticker",
            type=str,
            default=None,
            help=(
                "Process one ticker completely. "
                "Example: --ticker AAPL"
            ),
        )

        parser.add_argument(
            "--document-id",
            type=int,
            default=None,
            help="Process one FilingDocument only.",
        )

        parser.add_argument(
            "--missing-only",
            action="store_true",
            help=(
                "Skip documents that already have a summary "
                "for the current pipeline version and model."
            ),
        )

    def handle(self, *args, **options):
        ticker = options["ticker"]
        document_id = options["document_id"]
        missing_only = options["missing_only"]

        service = DocumentSummaryService()

        queryset = (
            FilingDocument.objects
            .select_related(
                "filing",
                "filing__company",
            )
            .order_by(
                "filing__company__ticker",
                "filing__filing_date",
                "filing__id",
                "sequence",
                "id",
            )
        )

        if document_id is not None:
            queryset = queryset.filter(
                id=document_id
            )

        if ticker:
            ticker = ticker.strip().upper()

            queryset = queryset.filter(
                filing__company__ticker__iexact=ticker
            )

        # -----------------------------------------------------
        # FAST RESUME
        # -----------------------------------------------------
        #
        # For the current bulk run, source documents/chunks are
        # assumed unchanged.
        #
        # Exclude documents that already have a cache row for:
        #   - current summary pipeline version
        #   - current generation model
        #
        # This means interrupted bulk processing can resume
        # without iterating through already-completed v10 rows.
        # -----------------------------------------------------

        if missing_only:
            model_name = service._model_name()

            completed_document_ids = (
                DocumentSummaryCache.objects
                .filter(
                    prompt_version=(
                        service.SUMMARY_PIPELINE_VERSION
                    ),
                    model_name=model_name,
                )
                .values(
                    "document_id"
                )
            )

            queryset = queryset.exclude(
                id__in=Subquery(
                    completed_document_ids
                )
            )

        total = queryset.count()

        if total == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    "No documents need summarization."
                )
            )
            return

        self.stdout.write("")

        self.stdout.write(
            f"Documents selected: {total}"
        )

        if missing_only:
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        "Resume mode: already-completed "
                        f"{service.SUMMARY_PIPELINE_VERSION} "
                        "documents are excluded."
                    )
                )
            )

        self.stdout.write("")

        current_ticker = None

        completed = 0
        failed = 0

        for index, document in enumerate(
            queryset.iterator(),
            start=1,
        ):
            filing = document.filing

            document_ticker = (
                filing.company.ticker
                or ""
            ).upper()

            if document_ticker != current_ticker:
                if current_ticker is not None:
                    self.stdout.write(
                        self.style.SUCCESS(
                            (
                                "\n===== FINISHED TICKER "
                                f"{current_ticker} =====\n"
                            )
                        )
                    )

                current_ticker = document_ticker

                self.stdout.write(
                    self.style.WARNING(
                        (
                            "\n===== STARTING TICKER "
                            f"{current_ticker} ====="
                        )
                    )
                )

            self.stdout.write(
                (
                    f"[{index}/{total}] "
                    f"document_id={document.id} | "
                    f"ticker={document_ticker} | "
                    f"form={filing.form} | "
                    f"accession={filing.accession_number} | "
                    f"document={document.document_name}"
                )
            )

            try:
                result = service.summarize_document(
                    document.id
                )

                completed += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        (
                            "    STORED IN POSTGRESQL"
                            f" | chunks={result.chunk_count}"
                            f" | summary_chars="
                            f"{len(result.summary)}"
                        )
                    )
                )

            except DocumentSummaryError as exc:
                failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        f"    FAILED: {exc}"
                    )
                )

            except Exception as exc:
                failed += 1

                self.stderr.write(
                    self.style.ERROR(
                        (
                            "    UNEXPECTED FAILURE: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    )
                )

        if current_ticker is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        "\n===== FINISHED TICKER "
                        f"{current_ticker} ====="
                    )
                )
            )

        self.stdout.write("")

        self.stdout.write(
            "========== SUMMARY PROCESSING COMPLETE =========="
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed/stored: {completed}"
            )
        )

        self.stdout.write(
            self.style.WARNING(
                f"Failed: {failed}"
            )
        )