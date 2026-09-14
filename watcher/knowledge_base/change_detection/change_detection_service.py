from dataclasses import dataclass

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.models import Filing
from watcher.knowledge_base.summarization.filing_summary_service import (
    FilingSummary,
    FilingSummaryService,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitation,
    resolve_summary_citations,
)


class ChangeDetectionError(Exception):
    """Raised when comparable SEC filings cannot be found or compared."""


@dataclass(frozen=True)
class ChangeDetectionResult:
    ticker: str
    form: str

    latest_filing: FilingSummary
    previous_filing: FilingSummary

    summary: str

    citations: tuple[SummaryCitation, ...]
    used_citations: tuple[SummaryCitation, ...]

    source_urls: tuple[str, ...]


class ChangeDetectionService:
    """
    Compare the latest filing with the immediately previous filing
    of the same SEC form for one company.

    Example:
        latest AAPL 10-Q
        vs
        previous AAPL 10-Q
    """

    def __init__(
        self,
        *,
        generation_service=None,
        filing_summary_service=None,
    ):
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

        self.filing_summary_service = (
            filing_summary_service
            or FilingSummaryService(
                generation_service=self.generation_service
            )
        )

    def compare_latest(
        self,
        ticker: str,
        *,
        form: str,
    ) -> ChangeDetectionResult:

        ticker = str(ticker or "").strip().upper()
        form = str(form or "").strip().upper()

        if not ticker:
            raise ChangeDetectionError(
                "Ticker cannot be empty."
            )

        if not form:
            raise ChangeDetectionError(
                "Form cannot be empty."
            )

        filings = list(
            Filing.objects
            .select_related("company")
            .filter(
                company__ticker__iexact=ticker,
                form__iexact=form,
                filing_date__isnull=False,
                documents__chunks__isnull=False,
            )
            .distinct()
            .order_by(
                "-filing_date",
                "-id",
            )[:2]
        )

        if len(filings) < 2:
            raise ChangeDetectionError(
                f"At least two chunked {form} filings "
                f"are required for {ticker}."
            )

        latest_filing = (
            self.filing_summary_service
            .summarize_filing(
                filings[0].id
            )
        )

        previous_filing = (
            self.filing_summary_service
            .summarize_filing(
                filings[1].id
            )
        )

        citations = self._deduplicate_citations(
            latest_filing.citations
            + previous_filing.citations
        )

        comparison_summary = (
            self._generate_comparison(
                ticker=ticker,
                form=form,
                latest=latest_filing,
                previous=previous_filing,
            )
        )

        used_citations = resolve_summary_citations(
            comparison_summary,
            citations,
            require_citation=False,
        )

        source_urls = tuple(
            dict.fromkeys(
                citation.source_url
                for citation in used_citations
                if citation.source_url
            )
        )

        return ChangeDetectionResult(
            ticker=ticker,
            form=form,
            latest_filing=latest_filing,
            previous_filing=previous_filing,
            summary=comparison_summary,
            citations=citations,
            used_citations=used_citations,
            source_urls=source_urls,
        )

    def _generate_comparison(
        self,
        *,
        ticker,
        form,
        latest,
        previous,
    ):
        prompt = f"""
Compare two SEC filings from the same company and same form.

Use ONLY the supplied SEC filing summaries.

Your task is to identify material differences between the
latest filing and the previous filing.

Rules:
- Do not use outside knowledge.
- Do not invent facts.
- Compare only evidence actually supplied.
- Preserve valid SEC source labels such as [C625].
- Never invent source labels.
- Keep reporting periods distinct.
- Identify newly disclosed facts when supported.
- Identify removed disclosures when supported.
- Identify changed numbers, risks, commitments or events when supported.
- Do not describe something as a change unless both filings support
  that comparison.
- If no supported material change is visible, say so.
- Preserve dates, percentages, dollar amounts and other material numbers.
- Keep the output factual and organized.

Company: {ticker}
Form: {form}

LATEST FILING
-------------
Filing date: {latest.filing_date or "Unknown"}
Accession: {latest.accession_number}

{latest.summary}


PREVIOUS FILING
---------------
Filing date: {previous.filing_date or "Unknown"}
Accession: {previous.accession_number}

{previous.summary}


Return a grounded latest-versus-previous change summary.
""".strip()

        return self.generation_service.generate(
            prompt,
            temperature=0.0,
        )

    @staticmethod
    def _deduplicate_citations(
        citations,
    ):
        citation_map = {}

        for citation in citations:
            citation_map.setdefault(
                citation.source_id,
                citation,
            )

        return tuple(
            citation_map.values()
        )