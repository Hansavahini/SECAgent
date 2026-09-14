from dataclasses import dataclass
from datetime import date

from pgvector.django import CosineDistance

from watcher.knowledge_base.embeddings.embedding_indexer import (
    EmbeddingIndexer,
)
from watcher.knowledge_base.embeddings.ollama_embedding_service import (
    OllamaEmbeddingService,
)
from watcher.knowledge_base.models import (
    ChunkEmbedding,
    EmbeddingVersion,
)


class RetrievalError(Exception):
    """Raised when SEC evidence cannot be retrieved."""


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: int
    ticker: str
    form: str
    filing_date: date | None
    accession_number: str

    document_type: str
    document_name: str
    is_primary: bool

    item_number: str
    section_title: str
    text: str
    distance: float


class VectorRetriever:
    def __init__(
        self,
        embedding_service=None,
    ):
        self.embedding_service = (
            embedding_service
            or OllamaEmbeddingService()
        )

    def search(
        self,
        query: str,
        *,
        ticker=None,
        form=None,
        item_number=None,
        date_from=None,
        date_to=None,
        top_k=8,
    ) -> list[RetrievalResult]:

        query = str(query or "").strip()

        if not query:
            raise RetrievalError(
                "Search query cannot be empty."
            )

        if top_k <= 0 or top_k > 50:
            raise RetrievalError(
                "top_k must be between 1 and 50."
            )

        version = self._get_embedding_version()

        query_vector = (
            self.embedding_service.embed(
                query
            )
        )

        results = (
            ChunkEmbedding.objects
            .filter(
                embedding_version=version,
            )
            .select_related(
                "chunk",
                "chunk__document",
                "chunk__filing",
                "chunk__filing__company",
            )
        )

        if ticker:
            results = results.filter(
                chunk__filing__company__ticker=(
                    str(ticker)
                    .strip()
                    .upper()
                )
            )

        if form:
            results = results.filter(
                chunk__filing__form=(
                    str(form)
                    .strip()
                    .upper()
                )
            )

        if item_number:
            results = results.filter(
                chunk__item_number__iexact=(
                    str(item_number).strip()
                )
            )

        if date_from:
            results = results.filter(
                chunk__filing__filing_date__gte=(
                    date_from
                )
            )

        if date_to:
            results = results.filter(
                chunk__filing__filing_date__lte=(
                    date_to
                )
            )

        results = (
            results
            .annotate(
                distance=CosineDistance(
                    "vector",
                    query_vector,
                )
            )
            .order_by(
                "distance",
                "chunk_id",
            )[:top_k]
        )

        output = []

        for row in results:
            chunk = row.chunk
            document = chunk.document
            filing = chunk.filing

            output.append(
                RetrievalResult(
                    chunk_id=chunk.id,
                    ticker=(
                        filing.company.ticker
                    ),
                    form=filing.form,
                    filing_date=(
                        filing.filing_date
                    ),
                    accession_number=(
                        filing.accession_number
                    ),
                    document_type=(
                        document.document_type
                        if document
                        else ""
                    ),
                    document_name=(
                        document.document_name
                        if document
                        else ""
                    ),
                    is_primary=(
                        document.is_primary
                        if document
                        else False
                    ),
                    item_number=(
                        chunk.item_number
                    ),
                    section_title=(
                        chunk.section_title
                    ),
                    text=chunk.text,
                    distance=float(
                        row.distance
                    ),
                )
            )

        return output

    def _get_embedding_version(
        self,
    ) -> EmbeddingVersion:

        try:
            return (
                EmbeddingVersion.objects.get(
                    provider="ollama",
                    model_name=(
                        self.embedding_service
                        .model_name
                    ),
                    dimensions=(
                        self.embedding_service
                        .dimensions
                    ),
                    chunker_version=(
                        EmbeddingIndexer
                        .CHUNKER_VERSION
                    ),
                    is_active=True,
                )
            )

        except (
            EmbeddingVersion.DoesNotExist
        ) as exc:
            raise RetrievalError(
                "Active embedding version "
                "was not found."
            ) from exc

        except (
            EmbeddingVersion
            .MultipleObjectsReturned
        ) as exc:
            raise RetrievalError(
                "Multiple active embedding "
                "versions were found."
            ) from exc