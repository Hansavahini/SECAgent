from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.db import transaction

from watcher.knowledge_base.ingestion.chunker import (
    FilingChunker,
)
from watcher.knowledge_base.ingestion.document_cleaner import (
    DocumentCleaner,
)
from watcher.knowledge_base.ingestion.sec_parser import (
    SECParser,
)
from watcher.knowledge_base.ingestion.text_extractor import (
    TextExtractor,
)
from watcher.models import (
    FilingChunk,
    FilingDocument,
)


class DocumentIngestionError(Exception):
    """Raised when one filing document cannot be ingested."""


@dataclass(frozen=True)
class DocumentIngestionResult:
    document_id: int
    chunks_created: int
    content_sha256: str
    skipped: bool


class DocumentIngestionService:
    """
    Extracts, cleans, parses and chunks one FilingDocument.

    Important:
    only chunks belonging to this exact document are affected.
    Other documents in the same filing are never deleted.
    """

    def __init__(
        self,
        *,
        extractor=None,
        cleaner=None,
        parser=None,
        chunker=None,
    ):
        self.extractor = (
            extractor
            or TextExtractor()
        )

        self.cleaner = (
            cleaner
            or DocumentCleaner()
        )

        self.parser = (
            parser
            or SECParser()
        )

        self.chunker = (
            chunker
            or FilingChunker()
        )

    def ingest(
        self,
        document: FilingDocument,
    ) -> DocumentIngestionResult:

        if not isinstance(
            document,
            FilingDocument,
        ):
            raise TypeError(
                "document must be a FilingDocument instance."
            )

        path = Path(
            document.local_path
        )

        if not path.is_file():
            raise DocumentIngestionError(
                f"Document file does not exist: {path}"
            )

        raw_bytes = path.read_bytes()

        if not raw_bytes:
            raise DocumentIngestionError(
                f"Document file is empty: {path}"
            )

        file_hash = sha256(
            raw_bytes
        ).hexdigest()

        # Safe rerun:
        # if this exact content is already chunked,
        # do not recreate chunks or destroy embeddings.
        if (
            document.content_sha256
            == file_hash
            and document.chunks.exists()
        ):
            return DocumentIngestionResult(
                document_id=document.pk,
                chunks_created=(
                    document.chunks.count()
                ),
                content_sha256=file_hash,
                skipped=True,
            )

        raw_text = (
            self.extractor.extract(
                path
            )
        )

        clean_text = (
            self.cleaner.clean(
                raw_text
            )
        )

        sections = (
            self.parser.parse(
                clean_text
            )
        )

        chunks = (
            self.chunker.chunk_sections(
                sections
            )
        )

        if not chunks:
            raise DocumentIngestionError(
                "No chunks generated for "
                f"document: {path}"
            )

        fallback_title = (
            document.description
            or document.document_type
            or document.document_name
        )

        with transaction.atomic():
            locked_document = (
                FilingDocument.objects
                .select_for_update()
                .select_related("filing")
                .get(pk=document.pk)
            )

            # Delete only chunks belonging to THIS document.
            locked_document.chunks.all().delete()

            FilingChunk.objects.bulk_create(
                [
                    FilingChunk(
                        filing=locked_document.filing,
                        document=locked_document,
                        chunk_index=(
                            chunk.chunk_index
                        ),
                        item_number=(
                            chunk.item_number
                        ),
                        section_title=(
                            chunk.section_title
                            or fallback_title
                        ),
                        text=chunk.text,
                        content_sha256=sha256(
                            chunk.text.encode(
                                "utf-8"
                            )
                        ).hexdigest(),
                        char_start=(
                            chunk.char_start
                        ),
                        char_end=(
                            chunk.char_end
                        ),
                    )
                    for chunk in chunks
                ],
                batch_size=500,
            )

            locked_document.content_sha256 = (
                file_hash
            )

            locked_document.file_size = len(
                raw_bytes
            )

            locked_document.save(
                update_fields=[
                    "content_sha256",
                    "file_size",
                    "updated_at",
                ]
            )

        return DocumentIngestionResult(
            document_id=document.pk,
            chunks_created=len(chunks),
            content_sha256=file_hash,
            skipped=False,
        )