from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from watcher.knowledge_base.ingestion.chunker import FilingChunker
from watcher.knowledge_base.ingestion.document_cleaner import DocumentCleaner
from watcher.knowledge_base.ingestion.sec_parser import SECParser
from watcher.knowledge_base.ingestion.text_extractor import TextExtractor
from watcher.knowledge_base.models import (
    Filing,
    FilingChunk,
    IngestionJob,
)


class IngestionError(Exception):
    """Raised when a filing cannot be ingested."""


class IngestionAlreadyRunning(IngestionError):
    """Raised when the same filing is already being processed."""


@dataclass(frozen=True)
class IngestionResult:
    filing_id: int
    chunks_created: int
    content_sha256: str
    status: str


class FilingIngestionService:
    def __init__(
        self,
        extractor=None,
        cleaner=None,
        parser=None,
        chunker=None,
    ):
        self.extractor = extractor or TextExtractor()
        self.cleaner = cleaner or DocumentCleaner()
        self.parser = parser or SECParser()
        self.chunker = chunker or FilingChunker()

    def ingest(self, filing: Filing) -> IngestionResult:
        if not isinstance(filing, Filing):
            raise TypeError(
                "filing must be a Filing instance."
            )

        self._begin_job(filing)

        try:
            path = Path(filing.local_path)

            if not path.is_file():
                raise IngestionError(
                    f"Filing file does not exist: {path}"
                )

            raw_bytes = path.read_bytes()

            if not raw_bytes:
                raise IngestionError(
                    f"Filing file is empty: {path}"
                )

            file_hash = sha256(
                raw_bytes
            ).hexdigest()

            raw_text = self.extractor.extract(
                path
            )

            clean_text = self.cleaner.clean(
                raw_text
            )

            sections = self.parser.parse(
                clean_text
            )

            chunks = self.chunker.chunk_sections(
                sections
            )

            if not chunks:
                raise IngestionError(
                    f"No chunks generated for filing: {path}"
                )

            now = timezone.now()

            with transaction.atomic():
                locked_filing = (
                    Filing.objects
                    .select_for_update()
                    .get(pk=filing.pk)
                )

                locked_filing.chunks.all().delete()

                FilingChunk.objects.bulk_create(
                    [
                        FilingChunk(
                            filing=locked_filing,
                            chunk_index=chunk.chunk_index,
                            item_number=chunk.item_number,
                            section_title=chunk.section_title,
                            text=chunk.text,
                            content_sha256=sha256(
                                chunk.text.encode(
                                    "utf-8"
                                )
                            ).hexdigest(),
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                        )
                        for chunk in chunks
                    ],
                    batch_size=500,
                )

                locked_filing.content_sha256 = (
                    file_hash
                )

                locked_filing.file_size = len(
                    raw_bytes
                )

                locked_filing.ingestion_status = (
                    Filing.IngestionStatus.CHUNKED
                )

                locked_filing.save(
                    update_fields=[
                        "content_sha256",
                        "file_size",
                        "ingestion_status",
                        "updated_at",
                    ]
                )

                job = (
                    IngestionJob.objects
                    .select_for_update()
                    .get(filing=locked_filing)
                )

                job.status = (
                    IngestionJob.Status.COMPLETED
                )

                job.completed_at = now
                job.last_error = ""
                job.next_retry_at = None

                job.save(
                    update_fields=[
                        "status",
                        "completed_at",
                        "last_error",
                        "next_retry_at",
                        "updated_at",
                    ]
                )

            return IngestionResult(
                filing_id=filing.pk,
                chunks_created=len(chunks),
                content_sha256=file_hash,
                status=Filing.IngestionStatus.CHUNKED,
            )

        except Exception as exc:
            self._mark_failed(
                filing=filing,
                error=exc,
            )
            raise

    def _begin_job(
        self,
        filing: Filing,
    ) -> None:
        now = timezone.now()

        with transaction.atomic():
            locked_filing = (
                Filing.objects
                .select_for_update()
                .get(pk=filing.pk)
            )

            job, _ = (
                IngestionJob.objects
                .select_for_update()
                .get_or_create(
                    filing=locked_filing
                )
            )

            if (
                job.status
                == IngestionJob.Status.PROCESSING
            ):
                raise IngestionAlreadyRunning(
                    f"Filing {filing.pk} "
                    "is already being ingested."
                )

            job.status = (
                IngestionJob.Status.PROCESSING
            )

            job.attempt_count += 1
            job.started_at = now
            job.completed_at = None
            job.last_error = ""

            job.save(
                update_fields=[
                    "status",
                    "attempt_count",
                    "started_at",
                    "completed_at",
                    "last_error",
                    "updated_at",
                ]
            )

            locked_filing.ingestion_status = (
                Filing.IngestionStatus.PROCESSING
            )

            locked_filing.save(
                update_fields=[
                    "ingestion_status",
                    "updated_at",
                ]
            )

    def _mark_failed(
        self,
        filing: Filing,
        error: Exception,
    ) -> None:
        now = timezone.now()

        with transaction.atomic():
            locked_filing = (
                Filing.objects
                .select_for_update()
                .get(pk=filing.pk)
            )

            locked_filing.ingestion_status = (
                Filing.IngestionStatus.FAILED
            )

            locked_filing.save(
                update_fields=[
                    "ingestion_status",
                    "updated_at",
                ]
            )

            job, _ = (
                IngestionJob.objects
                .select_for_update()
                .get_or_create(
                    filing=locked_filing
                )
            )

            job.status = (
                IngestionJob.Status.FAILED
            )

            job.last_error = str(error)
            job.completed_at = now

            job.save(
                update_fields=[
                    "status",
                    "last_error",
                    "completed_at",
                    "updated_at",
                ]
            )