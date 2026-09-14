from dataclasses import dataclass

from watcher.knowledge_base.embeddings.embedding_indexer import (
    EmbeddingIndexer,
)
from watcher.knowledge_base.models import (
    Filing,
    FilingChunk,
)


class FilingEmbeddingError(Exception):
    """Raised when a filing cannot be embedded."""


@dataclass(frozen=True)
class FilingEmbeddingResult:
    filing_id: int
    selected: int
    completed: int
    failed: int
    failed_chunk_ids: tuple[int, ...]


class FilingEmbeddingService:
    """
    Embeds only chunks belonging to one filing that do not
    already have the current embedding version.

    This does not scan or re-embed the whole database.
    """

    def __init__(
        self,
        *,
        indexer=None,
    ):
        self.indexer = (
            indexer
            or EmbeddingIndexer()
        )

    def missing_chunks(
        self,
        filing: Filing,
    ):
        if not isinstance(filing, Filing):
            raise TypeError(
                "filing must be a Filing instance."
            )

        return (
            FilingChunk.objects
            .filter(
                filing=filing,
            )
            .exclude(
                embeddings__embedding_version__provider=(
                    "ollama"
                ),
                embeddings__embedding_version__model_name=(
                    self.indexer.embedding_service.model_name
                ),
                embeddings__embedding_version__dimensions=(
                    self.indexer.embedding_service.dimensions
                ),
                embeddings__embedding_version__chunker_version=(
                    self.indexer.CHUNKER_VERSION
                ),
            )
            .order_by(
                "chunk_index",
                "id",
            )
        )

    def count_missing(
        self,
        filing: Filing,
    ) -> int:
        return self.missing_chunks(
            filing
        ).count()

    def index_filing(
        self,
        filing: Filing,
    ) -> FilingEmbeddingResult:

        chunks = list(
            self.missing_chunks(
                filing
            )
        )

        completed = 0
        failed_chunk_ids = []

        for chunk in chunks:
            try:
                self.indexer.index_chunk(
                    chunk
                )

                completed += 1

            except Exception:
                failed_chunk_ids.append(
                    chunk.id
                )

        return FilingEmbeddingResult(
            filing_id=filing.id,
            selected=len(chunks),
            completed=completed,
            failed=len(failed_chunk_ids),
            failed_chunk_ids=tuple(
                failed_chunk_ids
            ),
        )