from dataclasses import dataclass

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.models import Filing
from watcher.knowledge_base.summarization.document_summary_service import (
    DocumentSummary,
    DocumentSummaryService,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitation,
)


class FilingSummaryError(Exception):
    """Raised when a filing cannot be summarized."""


@dataclass(frozen=True)
class FilingSummary:
    filing_id: int
    ticker: str
    form: str
    filing_date: str | None
    accession_number: str
    document_count: int
    chunk_count: int
    summary: str
    documents: tuple[DocumentSummary, ...]
    citations: tuple[SummaryCitation, ...]
    source_urls: tuple[str, ...]


class FilingSummaryService:
    """
    Summarizes every chunked document belonging to one SEC filing.

    Flow:
        documents
        -> document summaries
        -> filing summary

    Chunk-level SEC provenance is preserved.
    """

    def __init__(
        self,
        *,
        generation_service=None,
        document_summary_service=None,
    ):
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

        self.document_summary_service = (
            document_summary_service
            or DocumentSummaryService(
                generation_service=self.generation_service
            )
        )

    def summarize_filing(
        self,
        filing_id: int,
    ) -> FilingSummary:

        try:
            filing = (
                Filing.objects
                .select_related("company")
                .get(pk=filing_id)
            )
        except Filing.DoesNotExist as exc:
            raise FilingSummaryError(
                f"Filing {filing_id} does not exist."
            ) from exc

        documents = list(
            filing.documents
            .filter(chunks__isnull=False)
            .distinct()
            .order_by(
                "-is_primary",
                "sequence",
                "id",
            )
        )

        if not documents:
            raise FilingSummaryError(
                f"Filing {filing_id} has no chunked documents."
            )

        document_summaries = tuple(
            self.document_summary_service.summarize_document(
                document.id
            )
            for document in documents
        )

        citations = tuple(
            citation
            for document_summary in document_summaries
            for citation in document_summary.citations
        )

        final_summary = self._combine_document_summaries(
            filing=filing,
            summaries=document_summaries,
        )

        source_urls = tuple(
            dict.fromkeys(
                citation.source_url
                for citation in citations
                if citation.source_url
            )
        )

        filing_date = (
            filing.filing_date.isoformat()
            if filing.filing_date
            else None
        )

        return FilingSummary(
            filing_id=filing.id,
            ticker=filing.company.ticker,
            form=filing.form,
            filing_date=filing_date,
            accession_number=filing.accession_number,
            document_count=len(document_summaries),
            chunk_count=sum(
                summary.chunk_count
                for summary in document_summaries
            ),
            summary=final_summary,
            documents=document_summaries,
            citations=citations,
            source_urls=source_urls,
        )

    def _combine_document_summaries(
        self,
        *,
        filing,
        summaries,
    ) -> str:

        if len(summaries) == 1:
            return summaries[0].summary

        combined = "\n\n".join(
            (
                f"[DOCUMENT {index + 1}]\n"
                f"Type: {summary.document_type or 'Unknown'}\n"
                f"Name: {summary.document_name}\n"
                f"Primary: {summary.is_primary}\n"
                f"Summary:\n{summary.summary}"
            )
            for index, summary in enumerate(summaries)
        )

        prompt = f"""
Combine the SEC document summaries below into one filing-level summary.

Use ONLY the supplied document summaries.

Rules:
- Do not add outside knowledge.
- Do not invent facts.
- Preserve important numbers, dates, percentages and amounts.
- Preserve material risks, events and commitments.
- Preserve valid SEC source labels such as [C625].
- Never invent a source label.
- Distinguish the primary filing from exhibits when relevant.
- Remove unnecessary repetition.
- Do not merge facts from different periods incorrectly.
- Keep the result factual and organized.

Company: {filing.company.ticker}
Form: {filing.form}
Filing date: {filing.filing_date or "Unknown"}
Accession: {filing.accession_number}

DOCUMENT SUMMARIES
------------------
{combined}

Return one consolidated filing summary with SEC source labels.
""".strip()

        return self.generation_service.generate(
            prompt,
            temperature=0.0,
        )