from dataclasses import dataclass
from datetime import date

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


class CompanySummaryError(Exception):
    """Raised when company filings cannot be summarized."""


@dataclass(frozen=True)
class CompanySummary:
    ticker: str
    company_name: str
    filing_count: int
    document_count: int
    chunk_count: int
    summary: str

    filings: tuple[FilingSummary, ...]

    citations: tuple[SummaryCitation, ...]
    used_citations: tuple[SummaryCitation, ...]

    source_urls: tuple[str, ...]

    start_date: str | None
    end_date: str | None
    form_filter: str | None


class CompanySummaryService:
    """
    Summarizes all available chunked filings for one company.

    Flow:
        all company filings
        -> filing summaries
        -> grouped reductions
        -> final company summary

    Chunk-level SEC provenance is preserved.
    """

    REDUCE_GROUP_SIZE = 4

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

    def summarize_company(
        self,
        ticker: str,
        *,
        form: str | None = None,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> CompanySummary:

        ticker = str(ticker or "").strip().upper()

        if not ticker:
            raise CompanySummaryError(
                "Ticker cannot be empty."
            )

        start_date = self._normalize_date(
            start_date,
            "start_date",
        )

        end_date = self._normalize_date(
            end_date,
            "end_date",
        )

        if (
            start_date
            and end_date
            and start_date > end_date
        ):
            raise CompanySummaryError(
                "start_date cannot be after end_date."
            )

        queryset = (
            Filing.objects
            .select_related("company")
            .filter(
                company__ticker__iexact=ticker,
                documents__chunks__isnull=False,
            )
            .distinct()
        )

        normalized_form = None

        if form:
            normalized_form = str(form).strip().upper()

            queryset = queryset.filter(
                form__iexact=normalized_form
            )

        if start_date:
            queryset = queryset.filter(
                filing_date__gte=start_date
            )

        if end_date:
            queryset = queryset.filter(
                filing_date__lte=end_date
            )

        filings = list(
            queryset.order_by(
                "filing_date",
                "id",
            )
        )

        if not filings:
            raise CompanySummaryError(
                f"No chunked filings found for {ticker}."
            )

        filing_summaries = tuple(
            self.filing_summary_service.summarize_filing(
                filing.id
            )
            for filing in filings
        )

        citations = tuple(
            citation
            for filing_summary in filing_summaries
            for citation in filing_summary.citations
        )

        final_summary = self._reduce_filing_summaries(
            ticker=ticker,
            summaries=filing_summaries,
        )

        used_citations = resolve_summary_citations(
            final_summary,
            citations,
            require_citation=False,
        )

        source_urls = tuple(
            dict.fromkeys(
                citation.source_url
                for citation in citations
                if citation.source_url
            )
        )

        company = filings[0].company

        available_dates = [
            filing.filing_date
            for filing in filings
            if filing.filing_date
        ]

        result_start_date = (
            min(available_dates).isoformat()
            if available_dates
            else None
        )

        result_end_date = (
            max(available_dates).isoformat()
            if available_dates
            else None
        )

        return CompanySummary(
            ticker=company.ticker,
            company_name=company.name or "",
            filing_count=len(filing_summaries),
            document_count=sum(
                item.document_count
                for item in filing_summaries
            ),
            chunk_count=sum(
                item.chunk_count
                for item in filing_summaries
            ),
            summary=final_summary,
            filings=filing_summaries,
            citations=citations,
            used_citations=used_citations,
            source_urls=source_urls,
            start_date=result_start_date,
            end_date=result_end_date,
            form_filter=normalized_form,
        )

    def _reduce_filing_summaries(
        self,
        *,
        ticker,
        summaries,
    ) -> str:

        if not summaries:
            raise CompanySummaryError(
                "No filing summaries were generated."
            )

        if len(summaries) == 1:
            return summaries[0].summary

        current = [
            self._format_filing_summary(summary)
            for summary in summaries
        ]

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
                    f"[SUMMARY {index + 1}]\n{text}"
                    for index, text in enumerate(group)
                )

                prompt = f"""
Combine the SEC filing summaries below into one company-level summary.

Use ONLY the supplied SEC filing summaries.

Rules:
- Do not use outside knowledge.
- Do not invent facts.
- Preserve important numbers, dates, percentages and amounts.
- Preserve material risks, events and commitments.
- Preserve valid SEC source labels such as [C625].
- Never invent a source label.
- Keep different reporting periods distinct.
- Identify meaningful developments only when supported.
- Do not claim a change unless the supplied summaries support it.
- Remove unnecessary repetition.
- Keep the result factual and organized.

Company ticker: {ticker}

SEC FILING SUMMARIES
--------------------
{combined}

Return one consolidated company summary with SEC source labels.
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

    @staticmethod
    def _format_filing_summary(
        summary: FilingSummary,
    ) -> str:

        return (
            f"Form: {summary.form}\n"
            f"Filing date: "
            f"{summary.filing_date or 'Unknown'}\n"
            f"Accession: "
            f"{summary.accession_number}\n"
            f"Documents: "
            f"{summary.document_count}\n"
            f"Summary:\n"
            f"{summary.summary}"
        )

    @staticmethod
    def _normalize_date(
        value,
        field_name,
    ):
        if value is None:
            return None

        if isinstance(value, date):
            return value

        try:
            return date.fromisoformat(
                str(value).strip()
            )
        except ValueError as exc:
            raise CompanySummaryError(
                f"{field_name} must use YYYY-MM-DD."
            ) from exc