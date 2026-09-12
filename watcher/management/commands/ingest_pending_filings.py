from django.core.management.base import BaseCommand, CommandError

from watcher.knowledge_base.ingestion.ingestion_service import (
    FilingIngestionService,
)
from watcher.knowledge_base.models import IngestionJob


class Command(BaseCommand):
    help = (
        "Process pending SEC filing ingestion jobs "
        "sequentially."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--ticker",
            help="Only process one ticker, for example AAPL.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of filings to process.",
        )

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually ingest the selected filings.",
        )

    def handle(self, *args, **options):
        ticker = (
            options.get("ticker") or ""
        ).strip().upper()

        limit = options.get("limit")
        apply_changes = options["apply"]

        if limit is not None and limit <= 0:
            raise CommandError(
                "--limit must be greater than zero."
            )

        jobs = (
            IngestionJob.objects
            .filter(
                status=IngestionJob.Status.PENDING
            )
            .select_related(
                "filing",
                "filing__company",
            )
            .order_by(
                "filing__filing_date",
                "filing_id",
            )
        )

        if ticker:
            jobs = jobs.filter(
                filing__company__ticker=ticker
            )

        if limit is not None:
            jobs = jobs[:limit]

        jobs = list(jobs)

        self.stdout.write(
            "Mode: "
            + ("APPLY" if apply_changes else "DRY RUN")
        )

        if ticker:
            self.stdout.write(
                f"Ticker filter: {ticker}"
            )

        self.stdout.write(
            f"Pending jobs selected: {len(jobs)}"
        )

        if not jobs:
            self.stdout.write(
                self.style.SUCCESS(
                    "No pending ingestion jobs found."
                )
            )
            return

        service = FilingIngestionService()

        completed = 0
        failed = 0
        chunks_created = 0

        for index, job in enumerate(
            jobs,
            start=1,
        ):
            filing = job.filing

            self.stdout.write(
                "\n"
                f"[{index}/{len(jobs)}] "
                f"{filing.company.ticker} "
                f"{filing.form} "
                f"{filing.filing_date} "
                f"{filing.accession_number}"
            )

            self.stdout.write(
                f"  File: {filing.local_path}"
            )

            if not apply_changes:
                continue

            try:
                result = service.ingest(
                    filing
                )

                completed += 1
                chunks_created += (
                    result.chunks_created
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  CHUNKED: "
                        f"{result.chunks_created} chunks"
                    )
                )

            except Exception as exc:
                failed += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"  FAILED: {exc}"
                    )
                )

                # One bad filing must not stop the batch.
                continue

        self.stdout.write(
            "\n"
            + "=" * 60
        )

        self.stdout.write(
            "INGESTION SUMMARY"
        )

        self.stdout.write(
            f"Selected: {len(jobs)}"
        )

        self.stdout.write(
            f"Completed: {completed}"
        )

        self.stdout.write(
            f"Failed: {failed}"
        )

        self.stdout.write(
            f"Chunks created: {chunks_created}"
        )