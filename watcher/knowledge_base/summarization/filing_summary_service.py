import hashlib
from dataclasses import dataclass

from django.db import transaction

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.models import (
    Filing,
    FilingSummaryCache,
)
from watcher.knowledge_base.summarization.document_summary_service import (
    DocumentSummary,
    DocumentSummaryService,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitation,
    build_summary_citation,
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

    Production flow:

        filing
          -> calculate content signature
          -> valid cache exists?
                YES -> return cached filing summary
                NO  -> summarize documents
                       -> build filing summary
                       -> persist cache
                       -> return result

    Cache validity depends on:
        - actual filing/document/chunk content
        - generation model
        - summary pipeline version
    """

    SUMMARY_PIPELINE_VERSION = "sec-filing-summary-v4"
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

        content_signature = (
            self._build_content_signature(
                filing
            )
        )

        model_name = self._model_name()

        cached = (
            FilingSummaryCache.objects
            .filter(
                filing=filing,
                content_signature=content_signature,
                model_name=model_name,
                prompt_version=(
                    self.SUMMARY_PIPELINE_VERSION
                ),
            )
            .first()
        )

        if cached is not None:
            return self._build_cached_result(
                filing=filing,
                documents=documents,
                cached=cached,
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

        final_summary = (
            self._combine_document_summaries(
                filing=filing,
                summaries=document_summaries,
            )
        )

        source_urls = self._source_urls(
            citations
        )

        filing_date = (
            filing.filing_date.isoformat()
            if filing.filing_date
            else None
        )

        result = FilingSummary(
            filing_id=filing.id,
            ticker=filing.company.ticker,
            form=filing.form,
            filing_date=filing_date,
            accession_number=(
                filing.accession_number
            ),
            document_count=len(
                document_summaries
            ),
            chunk_count=sum(
                summary.chunk_count
                for summary
                in document_summaries
            ),
            summary=final_summary,
            documents=document_summaries,
            citations=citations,
            source_urls=source_urls,
        )

        self._store_cache(
            filing=filing,
            result=result,
            content_signature=(
                content_signature
            ),
            model_name=model_name,
        )

        return result

    def _build_cached_result(
        self,
        *,
        filing,
        documents,
        cached,
    ) -> FilingSummary:
        """
        Restore the expensive filing-level summary from
        PostgreSQL.

        Citation metadata is rebuilt from current chunks.
        This is cheap and ensures SEC provenance remains
        tied to the current database records.

        Document summaries themselves are not regenerated
        on a cache hit because doing so would defeat the
        purpose of the cache. Downstream company-summary
        and change-detection services consume the filing
        summary, counts and citations.
        """

        citations = (
            self._build_current_citations(
                filing
            )
        )

        source_urls = self._source_urls(
            citations
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
            accession_number=(
                filing.accession_number
            ),
            document_count=len(documents),
            chunk_count=len(citations),
            summary=cached.summary,
            documents=(),
            citations=citations,
            source_urls=source_urls,
        )

    def _build_content_signature(
        self,
        filing,
    ) -> str:
        """
        Build a deterministic SHA-256 signature from the
        actual filing metadata and all current document /
        chunk content hashes.

        Any filing-content change invalidates the cache.
        """

        digest = hashlib.sha256()

        filing_parts = (
            str(filing.id),
            str(filing.accession_number or ""),
            str(filing.form or ""),
            str(filing.filing_date or ""),
        )

        digest.update(
            "|".join(
                filing_parts
            ).encode("utf-8")
        )

        chunks = (
            filing.chunks
            .filter(
                document__isnull=False
            )
            .select_related("document")
            .order_by(
                "document_id",
                "chunk_index",
                "id",
            )
        )

        found_chunk = False

        for chunk in chunks:
            found_chunk = True

            document = chunk.document

            parts = (
                str(document.id),
                str(
                    document.document_type
                    or ""
                ),
                str(
                    document.document_name
                    or ""
                ),
                str(
                    document.is_primary
                ),
                str(
                    document.content_sha256
                    or ""
                ),
                str(chunk.id),
                str(chunk.chunk_index),
                str(
                    chunk.content_sha256
                    or ""
                ),
            )

            digest.update(
                b"\n"
            )

            digest.update(
                "|".join(
                    parts
                ).encode("utf-8")
            )

        if not found_chunk:
            raise FilingSummaryError(
                f"Filing {filing.id} has no chunked content."
            )

        return digest.hexdigest()

    def _build_current_citations(
        self,
        filing,
    ) -> tuple[SummaryCitation, ...]:

        chunks = (
            filing.chunks
            .filter(
                document__isnull=False
            )
            .select_related(
                "filing",
                "filing__company",
                "document",
            )
            .order_by(
                "document_id",
                "chunk_index",
                "id",
            )
        )

        return tuple(
            build_summary_citation(
                chunk
            )
            for chunk in chunks
        )

    def _store_cache(
        self,
        *,
        filing,
        result,
        content_signature,
        model_name,
    ):
        """
        Replace any stale cache for this filing with the
        newly generated summary.

        OneToOneField guarantees at most one cache row
        per filing.
        """

        with transaction.atomic():
            FilingSummaryCache.objects.update_or_create(
                filing=filing,
                defaults={
                    "content_signature": (
                        content_signature
                    ),
                    "model_name": (
                        model_name
                    ),
                    "prompt_version": (
                        self.SUMMARY_PIPELINE_VERSION
                    ),
                    "summary": (
                        result.summary
                    ),
                    "document_count": (
                        result.document_count
                    ),
                    "chunk_count": (
                        result.chunk_count
                    ),
                },
            )

    def _model_name(
        self,
    ) -> str:

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

    @staticmethod
    def _source_urls(
        citations,
    ) -> tuple[str, ...]:

        return tuple(
            dict.fromkeys(
                citation.source_url
                for citation in citations
                if citation.source_url
            )
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
                f"Type: "
                f"{summary.document_type or 'Unknown'}\n"
                f"Name: {summary.document_name}\n"
                f"Primary: {summary.is_primary}\n"
                f"Summary:\n{summary.summary}"
            )
            for index, summary
            in enumerate(summaries)
        )
        prompt = f"""
Combine the SEC document summaries below into one complete,
clear filing-level summary.

Use ONLY the supplied document summaries.

STRICT RULES:

- Do not add outside knowledge.

- Do not invent, estimate, assume or infer facts.

- Preserve every distinct material fact that is useful to
  understanding the filing.

- Explain each retained fact clearly, but do not add interpretation,
  significance, causation, investor impact, business impact or
  implications unless explicitly stated in the supplied document
  summaries.

- Preserve important numbers, dates, percentages and amounts exactly.

- Preserve material risks, events, commitments, agreements,
  transactions, management actions and disclosures.

- Do not omit material dates, amounts, percentages, commitments,
  risks, agreements, transactions or management actions merely to
  make the result shorter.

- Preserve valid SEC source labels such as [C625].

- Never invent a source label.

- Keep every source label attached to the fact it supports.

- Distinguish the primary filing from exhibits when relevant.

- Remove genuine duplication, but do not remove separate material
  facts simply because they discuss the same general subject.

- Never combine facts from different reporting periods unless the
  supplied summaries explicitly connect them.

- Never combine separate transactions, agreements, programs,
  authorizations, legal matters, regulatory matters or events into
  one claim unless the supplied summaries explicitly connect them.

- Prefer a complete, readable filing summary over aggressive
  compression.

- If the available source material is short, keep the summary short.
  Do not add filler merely to make the summary longer.

- Keep the result factual, readable and organized.

Company: {filing.company.ticker}
Form: {filing.form}
Filing date: {filing.filing_date or "Unknown"}
Accession: {filing.accession_number}

DOCUMENT SUMMARIES
------------------
{combined}

Return one consolidated filing summary with the existing valid
SEC source labels preserved.
""".strip()
        return self.generation_service.generate(
            prompt,
            temperature=0.0,
            max_tokens=768,
        )
