from dataclasses import dataclass

from watcher.knowledge_base.embeddings.filing_embedding_service import (
    FilingEmbeddingService,
)
from watcher.knowledge_base.ingestion.ingestion_service import (
    FilingIngestionService,
)
from watcher.knowledge_base.models import Filing


class FilingIndexingError(Exception):
    """Raised when a filing cannot complete the indexing pipeline."""


@dataclass(frozen=True)
class FilingIndexingResult:
    filing_id: int
    ingestion_status: str
    chunks_created: int
    embeddings_selected: int
    embeddings_completed: int
    embeddings_failed: int
    failed_chunk_ids: tuple[int, ...]


class FilingIndexingService:
    """
    Orchestrates the knowledge-base pipeline for one Filing.

    Flow:
        Filing
        -> primary filing ingestion
        -> chunk creation / safe reuse
        -> embed only chunks missing the current embedding version
    """

    def __init__(
        self,
        *,
        ingestion_service=None,
        embedding_service=None,
    ):
        self.ingestion_service = (
            ingestion_service
            or FilingIngestionService()
        )

        self.embedding_service = (
            embedding_service
            or FilingEmbeddingService()
        )

    def index_filing(
        self,
        filing: Filing,
    ) -> FilingIndexingResult:

        if not isinstance(filing, Filing):
            raise TypeError(
                "filing must be a Filing instance."
            )

        ingestion_result = (
            self.ingestion_service.ingest(
                filing
            )
        )

        embedding_result = (
            self.embedding_service.index_filing(
                filing
            )
        )

        if embedding_result.failed:
            raise FilingIndexingError(
                f"Filing {filing.id} had "
                f"{embedding_result.failed} "
                "embedding failure(s)."
            )

        return FilingIndexingResult(
            filing_id=filing.id,
            ingestion_status=(
                ingestion_result.status
            ),
            chunks_created=(
                ingestion_result.chunks_created
            ),
            embeddings_selected=(
                embedding_result.selected
            ),
            embeddings_completed=(
                embedding_result.completed
            ),
            embeddings_failed=(
                embedding_result.failed
            ),
            failed_chunk_ids=(
                embedding_result.failed_chunk_ids
            ),
        )