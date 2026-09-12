from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from watcher.models import (
    Filing,
    FilingDocument,
)


class Command(BaseCommand):
    help = (
        "Create PRIMARY FilingDocument rows for existing filings "
        "and link existing chunks."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually create documents and link chunks.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        filings = (
            Filing.objects
            .select_related("company")
            .order_by("id")
        )

        self.stdout.write(
            "Mode: "
            + ("APPLY" if apply_changes else "DRY RUN")
        )

        created = 0
        existing = 0
        linked_chunks = 0
        failed = 0

        for filing in filings:
            document_name = (
                filing.primary_document
                or Path(filing.local_path).name
            )

            if not document_name:
                self.stdout.write(
                    self.style.ERROR(
                        f"SKIP filing={filing.id}: "
                        "no primary document name"
                    )
                )
                failed += 1
                continue

            self.stdout.write(
                f"{filing.company.ticker} "
                f"{filing.form} "
                f"{filing.accession_number} "
                f"→ {document_name}"
            )

            if not apply_changes:
                continue

            try:
                with transaction.atomic():
                    document, was_created = (
                        FilingDocument.objects.get_or_create(
                            filing=filing,
                            sequence=str(filing.sequence),
                            document_name=document_name,
                            defaults={
                                "document_type": "PRIMARY",
                                "description": (
                                    "Primary SEC filing document"
                                ),
                                "source_url": (
                                    filing.source_url or ""
                                ),
                                "local_path": (
                                    filing.local_path or ""
                                ),
                                "content_sha256": (
                                    filing.content_sha256 or ""
                                ),
                                "file_size": filing.file_size,
                                "is_primary": True,
                                "downloaded_at": (
                                    filing.downloaded_at
                                ),
                            },
                        )
                    )

                    if was_created:
                        created += 1
                    else:
                        existing += 1

                    updated = (
                        filing.chunks
                        .filter(document__isnull=True)
                        .update(document=document)
                    )

                    linked_chunks += updated

            except Exception as exc:
                failed += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"FAILED filing={filing.id}: {exc}"
                    )
                )

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("PRIMARY DOCUMENT BACKFILL")
        self.stdout.write(
            f"Filings examined: {filings.count()}"
        )
        self.stdout.write(
            f"Documents created: {created}"
        )
        self.stdout.write(
            f"Documents already existing: {existing}"
        )
        self.stdout.write(
            f"Chunks linked: {linked_chunks}"
        )
        self.stdout.write(
            f"Failures: {failed}"
        )