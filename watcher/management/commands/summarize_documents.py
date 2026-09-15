from django.core.management.base import BaseCommand

from watcher.knowledge_base.models import FilingDocument
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

    def handle(self, *args, **options):
        ticker = options["ticker"]
        document_id = options["document_id"]

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

        total = queryset.count()

        if total == 0:
            self.stdout.write(
                self.style.WARNING(
                    "No FilingDocument rows found."
                )
            )
            return

        self.stdout.write("")
        self.stdout.write(
            f"Documents selected: {total}"
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
                            f"\n===== FINISHED TICKER "
                            f"{current_ticker} =====\n"
                        )
                    )

                current_ticker = document_ticker

                self.stdout.write(
                    self.style.WARNING(
                        f"\n===== STARTING TICKER "
                        f"{current_ticker} ====="
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
                    f"\n===== FINISHED TICKER "
                    f"{current_ticker} ====="
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