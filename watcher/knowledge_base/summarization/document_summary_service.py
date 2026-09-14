from dataclasses import dataclass

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.models import FilingDocument
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
    Summarizes every chunk belonging to one FilingDocument.

    Flow:
        chunks
        -> chunk batches
        -> partial summaries
        -> final document summary
    """

    DEFAULT_MAX_BATCH_CHARS = 12000
    REDUCE_GROUP_SIZE = 4

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
            summary=final_summary,
            source_url=(
                document.source_url
                or filing.source_url
                or ""
            ),
            citations=citations,
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
                heading_parts.append(chunk.item_number)

            if chunk.section_title:
                heading_parts.append(
                    chunk.section_title
                )

            heading = " | ".join(heading_parts)

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
You are summarizing evidence from an SEC filing.

Use ONLY the source text provided below.

Rules:
- Do not use outside knowledge.
- Do not invent facts.
- Preserve important numbers, dates, percentages and amounts.
- Preserve material risks, commitments and events.
- Distinguish disclosed facts from management expectations.
- Every factual statement should preserve its source label such as [C700].
- Use only source labels that appear in the supplied text.
- If information is not present, do not add it.
- Keep the summary factual and concise.

Company: {filing.company.ticker}
Form: {filing.form}
Filing date: {filing.filing_date or "Unknown"}
Accession: {filing.accession_number}
Document type: {document.document_type or "Unknown"}
Document: {document.document_name}

SOURCE TEXT
-----------
{batch_text}

Return a structured factual summary with SEC source labels.
""".strip()

        return self.generation_service.generate(
            prompt,
            temperature=0.0,
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
                    next_level.append(group[0])
                    continue

                combined = "\n\n".join(
                    f"[PART {index + 1}]\n{text}"
                    for index, text in enumerate(group)
                )

                filing = document.filing

                prompt = f"""
Combine the partial SEC summaries below into one document summary.

Use ONLY the supplied partial summaries.

Rules:
- Do not invent facts.
- Do not add outside knowledge.
- Preserve material numbers, dates, risks and events.
- Preserve all valid source labels such as [C700].
- Never invent a source label.
- Remove repetition.
- Keep different periods distinct.
- Keep the result factual and organized.

Company: {filing.company.ticker}
Form: {filing.form}
Filing date: {filing.filing_date or "Unknown"}
Accession: {filing.accession_number}
Document: {document.document_name}

PARTIAL SUMMARIES
-----------------
{combined}

Return one consolidated document summary with source labels.
""".strip()

                reduced = (
                    self.generation_service.generate(
                        prompt,
                        temperature=0.0,
                    )
                )

                next_level.append(reduced)

            current = next_level

        return current[0]