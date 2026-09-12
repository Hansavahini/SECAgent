from django.db import transaction

from watcher.knowledge_base.embeddings.ollama_embedding_service import (
    OllamaEmbeddingService,
)
from watcher.knowledge_base.models import (
    ChunkEmbedding,
    EmbeddingVersion,
    FilingChunk,
)


class EmbeddingIndexer:
    CHUNKER_VERSION = "sec-char-v1"

    def __init__(
        self,
        embedding_service=None,
    ):
        self.embedding_service = (
            embedding_service
            or OllamaEmbeddingService()
        )

    def index_chunk(
        self,
        chunk: FilingChunk,
    ) -> ChunkEmbedding:
        if not isinstance(chunk, FilingChunk):
            raise TypeError(
                "chunk must be a FilingChunk instance."
            )

        vector = self.embedding_service.embed(
            chunk.text
        )

        version, _ = (
            EmbeddingVersion.objects.get_or_create(
                provider="ollama",
                model_name=(
                    self.embedding_service.model_name
                ),
                dimensions=(
                    self.embedding_service.dimensions
                ),
                chunker_version=self.CHUNKER_VERSION,
                defaults={
                    "is_active": True,
                },
            )
        )

        with transaction.atomic():
            embedding, _ = (
                ChunkEmbedding.objects.update_or_create(
                    chunk=chunk,
                    embedding_version=version,
                    defaults={
                        "vector": vector,
                    },
                )
            )

        return embedding