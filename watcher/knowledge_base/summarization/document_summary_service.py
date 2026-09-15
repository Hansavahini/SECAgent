import hashlib
from dataclasses import dataclass

from django.db import transaction

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.models import (
    DocumentSummaryCache,
    FilingDocument,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitation,
    build_summary_citation,
)


class DocumentSummaryError(Exception):
    """Raised when a filing document cannot be summarized."""


@dataclass(frozen=True)
class DocumentSummary:
    document_id: int
    filing_id: int
    ticker: str
    form: str
    filing_date: str | None
    accession_number: str
    document_type: str
    document_name: str
    is_primary: bool
    chunk_count: int
    summary: str
    source_url: str
    citations: tuple[SummaryCitation, ...]


class DocumentSummaryService:
    """
    Generates and persistently caches one concise summary
    for each SEC FilingDocument.

    Flow:

        FilingDocument
            -> build content signature
            -> valid PostgreSQL cache?
                YES -> reuse stored summary
                NO  -> summarize all chunks
                       -> store DocumentSummaryCache
                       -> return summary

    The original SEC document text is NOT copied into the
    summary table.
    """

    SUMMARY_PIPELINE_VERSION = "sec-document-summary-v4"
    DEFAULT_MAX_BATCH_CHARS = 40000
    BATCH_MAX_TOKENS = 384
    FINAL_MAX_TOKENS = 1024
    REDUCE_GROUP_SIZE = 8

    def __init__(
        self,
        *,
        generation_service=None,
        max_batch_chars=None,
    ):
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

        self.max_batch_chars = (
            max_batch_chars
            or self.DEFAULT_MAX_BATCH_CHARS
        )

        if self.max_batch_chars < 1000:
            raise ValueError(
                "max_batch_chars must be at least 1000."
            )

    def summarize_document(
        self,
        document_id: int,
    ) -> DocumentSummary:

        try:
            document = (
                FilingDocument.objects
                .select_related(
                    "filing",
                    "filing__company",
                )
                .get(pk=document_id)
            )
        except FilingDocument.DoesNotExist as exc:
            raise DocumentSummaryError(
                f"FilingDocument {document_id} does not exist."
            ) from exc

        chunks = list(
            document.chunks
            .select_related(
                "filing",
                "filing__company",
                "document",
            )
            .order_by("chunk_index")
        )

        if not chunks:
            raise DocumentSummaryError(
                f"FilingDocument {document_id} has no chunks."
            )

        citations = tuple(
            build_summary_citation(chunk)
            for chunk in chunks
        )

        content_signature = (
            self._build_content_signature(
                document=document,
                chunks=chunks,
            )
        )

        model_name = self._model_name()

        cached = (
            DocumentSummaryCache.objects
            .filter(
                document=document,
                content_signature=content_signature,
                model_name=model_name,
                prompt_version=self.SUMMARY_PIPELINE_VERSION,
            )
            .first()
        )

        if cached is not None:
            return self._build_result(
                document=document,
                chunks=chunks,
                citations=citations,
                summary=cached.summary,
            )

        citation_by_chunk_id = {
            citation.chunk_id: citation
            for citation in citations
        }

        batches = self._build_chunk_batches(
            chunks,
            citation_by_chunk_id,
        )

        partial_summaries = [
            self._summarize_batch(
                document=document,
                batch_text=batch,
            )
            for batch in batches
        ]

        final_summary = self._reduce_summaries(
            document=document,
            summaries=partial_summaries,
        )

        self._store_cache(
            document=document,
            content_signature=content_signature,
            model_name=model_name,
            summary=final_summary,
            chunk_count=len(chunks),
        )

        return self._build_result(
            document=document,
            chunks=chunks,
            citations=citations,
            summary=final_summary,
        )

    def _build_result(
        self,
        *,
        document,
        chunks,
        citations,
        summary,
    ) -> DocumentSummary:

        filing = document.filing

        filing_date = (
            filing.filing_date.isoformat()
            if filing.filing_date
            else None
        )

        return DocumentSummary(
            document_id=document.id,
            filing_id=filing.id,
            ticker=filing.company.ticker,
            form=filing.form,
            filing_date=filing_date,
            accession_number=filing.accession_number,
            document_type=document.document_type,
            document_name=document.document_name,
            is_primary=document.is_primary,
            chunk_count=len(chunks),
            summary=summary,
            source_url=(
                document.source_url
                or filing.source_url
                or ""
            ),
            citations=citations,
        )

    def _build_content_signature(
        self,
        *,
        document,
        chunks,
    ) -> str:

        digest = hashlib.sha256()

        document_parts = (
            str(document.id),
            str(document.sequence or ""),
            str(document.document_type or ""),
            str(document.document_name or ""),
            str(document.is_primary),
            str(document.content_sha256 or ""),
        )

        digest.update(
            "|".join(document_parts).encode("utf-8")
        )

        for chunk in chunks:
            parts = (
                str(chunk.id),
                str(chunk.chunk_index),
                str(chunk.item_number or ""),
                str(chunk.section_title or ""),
                str(chunk.content_sha256 or ""),
            )

            digest.update(b"\n")
            digest.update(
                "|".join(parts).encode("utf-8")
            )

        return digest.hexdigest()

    def _store_cache(
        self,
        *,
        document,
        content_signature,
        model_name,
        summary,
        chunk_count,
    ):
        with transaction.atomic():
            DocumentSummaryCache.objects.update_or_create(
                document=document,
                defaults={
                    "content_signature": content_signature,
                    "model_name": model_name,
                    "prompt_version": (
                        self.SUMMARY_PIPELINE_VERSION
                    ),
                    "summary": summary,
                    "chunk_count": chunk_count,
                },
            )

    def _model_name(self) -> str:
        value = getattr(
            self.generation_service,
            "model_name",
            None,
        )

        if value:
            return str(value)

        return (
            self.generation_service
            .__class__
            .__name__
        )

    def _build_chunk_batches(
        self,
        chunks,
        citation_by_chunk_id,
    ):
        batches = []
        current_parts = []
        current_size = 0

        for chunk in chunks:
            citation = citation_by_chunk_id[chunk.id]

            heading_parts = [
                f"[{citation.source_id}]",
                f"Chunk {chunk.chunk_index}",
            ]

            if chunk.item_number:
                heading_parts.append(
                    chunk.item_number
                )

            if chunk.section_title:
                heading_parts.append(
                    chunk.section_title
                )

            heading = " | ".join(
                heading_parts
            )

            piece = (
                f"{heading}\n"
                f"{chunk.text.strip()}"
            )

            piece_size = len(piece)

            if (
                current_parts
                and current_size + piece_size
                > self.max_batch_chars
            ):
                batches.append(
                    "\n\n".join(current_parts)
                )

                current_parts = []
                current_size = 0

            current_parts.append(piece)
            current_size += piece_size

        if current_parts:
            batches.append(
                "\n\n".join(current_parts)
            )

        return batches

    def _summarize_batch(
        self,
        *,
        document,
        batch_text,
    ):
        filing = document.filing

        prompt = f"""
You are summarizing evidence from an SEC filing document.

Use ONLY the source text provided below.

Rules:
- Do not use outside knowledge.
- Do not invent facts.
- Preserve material numbers, dates, percentages and amounts.
- Preserve material risks, commitments and events.
- Distinguish disclosed facts from management expectations.
- Preserve SEC source labels such as [C700].
- Use only source labels appearing in the supplied text.
- Do not add information that is not present.
- Remove unnecessary repetition.
- Keep the result concise, factual and structured.

Company: {filing.company.ticker}
Form: {filing.form}
Filing date: {filing.filing_date or "Unknown"}
Accession: {filing.accession_number}
Document type: {document.document_type or "Unknown"}
Document: {document.document_name}

SOURCE TEXT
-----------
{batch_text}

Return a concise factual SEC document summary with source labels.
""".strip()

        return self.generation_service.generate(
            prompt,
            temperature=0.0,
            max_tokens=self.BATCH_MAX_TOKENS,
        )

    def _reduce_summaries(
        self,
        *,
        document,
        summaries,
    ):
        if not summaries:
            raise DocumentSummaryError(
                "No partial summaries were generated."
            )

        if len(summaries) == 1:
            return summaries[0]

        current = list(summaries)

        while len(current) > 1:
            next_level = []

            for start in range(
                0,
                len(current),
                self.REDUCE_GROUP_SIZE,
            ):
                group = current[
                    start:
                    start + self.REDUCE_GROUP_SIZE
                ]

                if len(group) == 1:
                    next_level.append(
                        group[0]
                    )
                    continue

                combined = "\n\n".join(
                    f"[PART {index + 1}]\n{text}"
                    for index, text
                    in enumerate(group)
                )

                filing = document.filing

                prompt = f"""
Combine the partial SEC document summaries below.

Use ONLY the supplied partial summaries.

Rules:
- Do not invent facts.
- Do not add outside knowledge.
- Preserve material numbers, dates, percentages and amounts.
- Preserve material risks, commitments and events.
- Preserve valid SEC source labels.
- Never invent source labels.
- Remove repetition.
- Keep different reporting periods distinct.
- Keep the final summary concise and factual.

Company: {filing.company.ticker}
Form: {filing.form}
Filing date: {filing.filing_date or "Unknown"}
Accession: {filing.accession_number}
Document: {document.document_name}

PARTIAL SUMMARIES
-----------------
{combined}

Return one consolidated SEC document summary.
""".strip()

                reduced = (
                    self.generation_service.generate(
                        prompt,
                        temperature=0.0,
                        max_tokens=self.FINAL_MAX_TOKENS,
                    )
                )

                next_level.append(
                    reduced
                )

            current = next_level

        return current[0]
