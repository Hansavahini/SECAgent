import hashlib
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from watcher.knowledge_base.summarization.document_fallback_summary_service import (
    DocumentFallbackSummaryService,
)
from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.models import (
    DocumentSummaryCache,
    FilingDocument,
)
from watcher.knowledge_base.summarization.financial_fact_extractor import (
    FinancialFactExtractor,
)
from watcher.knowledge_base.summarization.financial_fact_selector import (
    FinancialFactSelector,
)
from watcher.knowledge_base.summarization.section_summary_service import (
    SectionSummaryService,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitation,
    build_summary_citation,
)
from watcher.knowledge_base.summarization.summary_composer import (
    SummaryComposer,
)
from watcher.knowledge_base.summarization.summary_validator import (
    SummaryValidator,
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
    Production document-summary service.

    IMPORTANT:

        1 FilingDocument
            -> 1 generated summary
            -> 1 DocumentSummaryCache row

    Existing parser/chunker/embedding work is reused.

    Flow:

        FilingDocument
            -> existing FilingChunk rows
            -> existing section/item metadata

            -> for primary 10-Q / 10-K:
                   read existing local SEC HTML
                   -> extract XBRL financial facts
                   -> select trusted facts

            -> build narrative section groups
            -> generate source-grounded candidate summaries
            -> validate claims against existing chunks
            -> deterministically compose final summary
            -> store/update DocumentSummaryCache
    """

    SUMMARY_PIPELINE_VERSION = "sec-document-summary-v11"

    FINANCIAL_FORMS = {
        "10-Q",
        "10-K",
    }

    def __init__(
        self,
        *,
        generation_service=None,
    ):
        # Kept for compatibility and cache model metadata.
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

        self.financial_extractor = (
            FinancialFactExtractor()
        )

        self.financial_selector = (
            FinancialFactSelector()
        )

        self.section_service = (
            SectionSummaryService()
        )
        self.fallback_service = (
            DocumentFallbackSummaryService(
                generation_service=self.generation_service,
            )
        )
        self.validator = (
            SummaryValidator()
        )

        self.composer = (
            SummaryComposer()
        )

    def summarize_document(
        self,
        document_id: int,
    ) -> DocumentSummary:
        """
        Generate or reuse one summary for one FilingDocument.
        """

        document = self._load_document(
            document_id
        )

        chunks = self._load_chunks(
            document
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

        cached = self._get_valid_cache(
            document=document,
            content_signature=content_signature,
            model_name=model_name,
        )

        if cached is not None:
            return self._build_result(
                document=document,
                chunks=chunks,
                citations=citations,
                summary=cached.summary,
            )

        financial_facts = (
            self._build_financial_facts(
                document=document,
            )
        )

        validated_sections = (
            self._build_validated_sections(
                document=document,
                chunks=chunks,
            )
        )

        final_summary = self.composer.compose(
            ticker=document.filing.company.ticker,
            form=document.filing.form,
            filing_date=document.filing.filing_date,
            financial_facts=financial_facts,
            section_summaries=validated_sections,
        )

        final_summary = (
            final_summary or ""
        ).strip()

        if not final_summary:
            raise DocumentSummaryError(
                f"FilingDocument {document.id} produced "
                "an empty final summary."
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

    def _load_document(
        self,
        document_id: int,
    ):
        try:
            return (
                FilingDocument.objects
                .select_related(
                    "filing",
                    "filing__company",
                )
                .get(pk=document_id)
            )
        except FilingDocument.DoesNotExist as exc:
            raise DocumentSummaryError(
                f"FilingDocument {document_id} "
                "does not exist."
            ) from exc

    def _load_chunks(
        self,
        document,
    ):
        return list(
            document.chunks
            .select_related(
                "filing",
                "filing__company",
                "document",
            )
            .order_by(
                "chunk_index",
                "id",
            )
        )

    def _get_valid_cache(
        self,
        *,
        document,
        content_signature,
        model_name,
    ):
        return (
            DocumentSummaryCache.objects
            .filter(
                document=document,
                content_signature=content_signature,
                model_name=model_name,
                prompt_version=(
                    self.SUMMARY_PIPELINE_VERSION
                ),
            )
            .first()
        )

    def _build_financial_facts(
        self,
        *,
        document,
    ):
        """
        Use deterministic XBRL financial extraction only
        for primary 10-Q and 10-K documents.

        Other documents continue through narrative summarization
        without manufactured financial facts.
        """

        filing = document.filing
        form = (
            filing.form
            or ""
        ).upper().strip()

        if form not in self.FINANCIAL_FORMS:
            return []

        if not document.is_primary:
            return []

        local_path = str(
            document.local_path
            or ""
        ).strip()

        if not local_path:
            raise DocumentSummaryError(
                f"Primary {form} document "
                f"{document.id} has no local_path."
            )

        path = Path(
            local_path
        )

        if not path.exists():
            raise DocumentSummaryError(
                f"Local SEC document does not exist: "
                f"{path}"
            )

        try:
            raw_facts = (
                self.financial_extractor
                .extract_from_path(
                    path
                )
            )

            return (
                self.financial_selector
                .select(
                    raw_facts,
                    form=form,
                )
            )

        except Exception as exc:
            raise DocumentSummaryError(
                f"Financial extraction failed for "
                f"FilingDocument {document.id}: {exc}"
            ) from exc

    def _build_validated_sections(
        self,
        *,
        document,
        chunks,
    ):
        """
        Build normal section-aware summaries first.

        A validator result containing only NO_MATERIAL_SUMMARY does
        NOT count as usable material.

        When normal section summarization finds no usable material,
        use the generic document fallback for:
            - non-primary documents/exhibits
            - primary 8-K documents

        Primary 10-Q and 10-K documents continue to rely on their
        existing XBRL + section-aware path.
        """

        filing = document.filing

        no_material = (
            self.validator.NO_MATERIAL_SUMMARY
        )

        # ---------------------------------------------------------
        # 1. NORMAL SECTION-AWARE PATH
        # ---------------------------------------------------------

        groups = (
            self.section_service
            .build_groups(
                chunks,
                form=filing.form,
            )
        )

        if groups:
            section_summaries = (
                self.section_service
                .summarize_groups(
                    groups,
                    ticker=filing.company.ticker,
                    form=filing.form,
                    filing_date=filing.filing_date,
                    accession_number=(
                        filing.accession_number
                    ),
                    document_name=(
                        document.document_name
                    ),
                )
            )

            validated_sections = (
                self.validator
                .validate_sections(
                    section_summaries=(
                        section_summaries
                    ),
                    chunks=chunks,
                )
            )

            material_sections = tuple(
                section
                for section in validated_sections
                if (
                    str(
                        section.summary
                        or ""
                    ).strip()
                    and str(
                        section.summary
                        or ""
                    ).strip()
                    != no_material
                )
            )

            if material_sections:
                return material_sections

        # ---------------------------------------------------------
        # 2. DETERMINE WHETHER FALLBACK IS APPROPRIATE
        # ---------------------------------------------------------

        form = str(
            filing.form or ""
        ).strip().upper()

        should_use_fallback = (
            not document.is_primary
            or form == "8-K"
        )

        if not should_use_fallback:
            return ()

        # ---------------------------------------------------------
        # 3. GENERIC DOCUMENT-LEVEL FALLBACK
        # ---------------------------------------------------------

        fallback_summaries = (
            self.fallback_service
            .summarize_document(
                document=document,
                chunks=chunks,
            )
        )

        if not fallback_summaries:
            return ()

        validated_fallback = (
            self.validator
            .validate_sections(
                section_summaries=(
                    fallback_summaries
                ),
                chunks=chunks,
            )
        )

        material_fallback = tuple(
            section
            for section in validated_fallback
            if (
                str(
                    section.summary
                    or ""
                ).strip()
                and str(
                    section.summary
                    or ""
                ).strip()
                != no_material
            )
        )

        return material_fallback

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
            accession_number=(
                filing.accession_number
            ),
            document_type=(
                document.document_type
                or ""
            ),
            document_name=(
                document.document_name
                or ""
            ),
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
        """
        If document/chunk content changes, the signature changes
        and the summary is regenerated.
        """

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
            "|".join(
                document_parts
            ).encode(
                "utf-8"
            )
        )

        for chunk in chunks:
            parts = (
                str(chunk.id),
                str(chunk.chunk_index),
                str(
                    chunk.item_number
                    or ""
                ),
                str(
                    chunk.section_title
                    or ""
                ),
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
                ).encode(
                    "utf-8"
                )
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
        """
        One cache row per FilingDocument.

        Re-running updates the existing row rather
        than creating duplicate summaries.
        """

        with transaction.atomic():
            DocumentSummaryCache.objects.update_or_create(
                document=document,
                defaults={
                    "content_signature": (
                        content_signature
                    ),
                    "model_name": model_name,
                    "prompt_version": (
                        self.SUMMARY_PIPELINE_VERSION
                    ),
                    "summary": summary,
                    "chunk_count": chunk_count,
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
            return str(
                value
            )

        return (
            self.generation_service
            .__class__
            .__name__
        )