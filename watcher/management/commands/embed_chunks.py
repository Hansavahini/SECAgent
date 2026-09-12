from django.core.management.base import BaseCommand, CommandError

from watcher.knowledge_base.embeddings.embedding_indexer import (
    EmbeddingIndexer,
)
from watcher.models import ChunkEmbedding, FilingChunk


class Command(BaseCommand):
    help = "Generate embeddings for SEC chunks that are not yet embedded."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ticker",
            help="Only process one ticker, for example AAPL.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of chunks to process.",
        )

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually generate and save embeddings.",
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

        indexer = EmbeddingIndexer()

        chunks = (
            FilingChunk.objects
            .select_related(
                "filing",
                "filing__company",
            )
            .exclude(
                embeddings__embedding_version__provider="ollama",
                embeddings__embedding_version__model_name=(
                    indexer.embedding_service.model_name
                ),
                embeddings__embedding_version__dimensions=(
                    indexer.embedding_service.dimensions
                ),
                embeddings__embedding_version__chunker_version=(
                    indexer.CHUNKER_VERSION
                ),
            )
            .order_by(
                "filing_id",
                "chunk_index",
            )
        )

        if ticker:
            chunks = chunks.filter(
                filing__company__ticker=ticker
            )

        if limit is not None:
            chunks = chunks[:limit]

        chunks = list(chunks)

        self.stdout.write(
            "Mode: "
            + ("APPLY" if apply_changes else "DRY RUN")
        )

        self.stdout.write(
            f"Model: "
            f"{indexer.embedding_service.model_name}"
        )

        self.stdout.write(
            f"Dimensions: "
            f"{indexer.embedding_service.dimensions}"
        )

        self.stdout.write(
            f"Chunks selected: {len(chunks)}"
        )

        completed = 0
        failed = 0

        for number, chunk in enumerate(
            chunks,
            start=1,
        ):
            self.stdout.write(
                f"\n[{number}/{len(chunks)}] "
                f"{chunk.filing.company.ticker} "
                f"{chunk.filing.form} "
                f"{chunk.item_number or '<no item>'} "
                f"chunk={chunk.id}"
            )

            if not apply_changes:
                continue

            try:
                indexer.index_chunk(chunk)

                completed += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        "  EMBEDDED"
                    )
                )

            except Exception as exc:
                failed += 1

                self.stdout.write(
                    self.style.ERROR(
                        f"  FAILED: {exc}"
                    )
                )

        self.stdout.write(
            "\n"
            + "=" * 60
        )

        self.stdout.write(
            "EMBEDDING SUMMARY"
        )

        self.stdout.write(
            f"Selected: {len(chunks)}"
        )

        self.stdout.write(
            f"Completed: {completed}"
        )

        self.stdout.write(
            f"Failed: {failed}"
        )

        self.stdout.write(
            f"Total stored embeddings: "
            f"{ChunkEmbedding.objects.count()}"
        )