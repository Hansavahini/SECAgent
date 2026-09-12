from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from watcher.knowledge_base.ingestion.ingestion_service import (
    FilingIngestionService,
)
from watcher.knowledge_base.models import Company, Filing


class Command(BaseCommand):
    help = "Test KB ingestion against one real SEC filing and roll back all DB changes."

    def add_arguments(self, parser):
        parser.add_argument("file_path")

    def handle(self, *args, **options):
        path = Path(options["file_path"]).resolve()

        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        self.stdout.write(f"Testing: {path}")

        with transaction.atomic():
            company = Company.objects.create(
                ticker="TESTAAPL",
                cik="9999999999",
                name="Temporary ingestion test",
            )

            filing = Filing.objects.create(
                company=company,
                accession_number="TEST-INGESTION-0001",
                sequence=1,
                form="10-Q",
                local_path=str(path),
            )

            result = FilingIngestionService().ingest(filing)

            filing.refresh_from_db()

            chunks = filing.chunks.order_by("chunk_index")

            self.stdout.write(
                self.style.SUCCESS(
                    f"Status: {result.status}"
                )
            )

            self.stdout.write(
                f"File size: {filing.file_size}"
            )

            self.stdout.write(
                f"SHA256: {filing.content_sha256}"
            )

            self.stdout.write(
                f"Chunks created: {chunks.count()}"
            )

            self.stdout.write("\nFirst chunks:")

            for chunk in chunks[:10]:
                self.stdout.write(
                    f"{chunk.chunk_index}: "
                    f"{chunk.item_number or '<no item>'} | "
                    f"{chunk.section_title or '<no title>'} | "
                    f"{len(chunk.text)} chars"
                )

            # Test only: discard Company, Filing, Job and chunks.
            transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                "\nRollback complete — production DB unchanged."
            )
        )