import re
from dataclasses import dataclass
from datetime import date


class SummaryCitationError(Exception):
    """Raised when generated summary citations are invalid."""


@dataclass(frozen=True)
class SummaryCitation:
    """
    Provenance for one SEC filing chunk used by summarization.
    """

    source_id: str
    chunk_id: int
    filing_id: int
    document_id: int

    ticker: str
    form: str
    filing_date: date | None
    accession_number: str

    document_type: str
    document_name: str

    item_number: str
    section_title: str

    source_url: str


def build_summary_citation(chunk) -> SummaryCitation:
    """
    Build stable SEC provenance metadata from a FilingChunk.
    """

    filing = chunk.filing
    document = chunk.document

    if document is None:
        raise ValueError(
            f"Chunk {chunk.id} has no FilingDocument."
        )

    return SummaryCitation(
        source_id=f"C{chunk.id}",
        chunk_id=chunk.id,
        filing_id=filing.id,
        document_id=document.id,
        ticker=filing.company.ticker,
        form=filing.form,
        filing_date=filing.filing_date,
        accession_number=filing.accession_number,
        document_type=document.document_type or "",
        document_name=document.document_name,
        item_number=chunk.item_number or "",
        section_title=chunk.section_title or "",
        source_url=(
            document.source_url
            or filing.source_url
            or ""
        ),
    )


def extract_summary_source_ids(
    summary_text: str,
) -> tuple[str, ...]:
    """
    Extract unique [C123] source labels in appearance order.
    """

    matches = re.findall(
        r"\[C(\d+)\]",
        str(summary_text or ""),
    )

    source_ids = []
    seen = set()

    for chunk_id in matches:
        source_id = f"C{chunk_id}"

        if source_id not in seen:
            seen.add(source_id)
            source_ids.append(source_id)

    return tuple(source_ids)


def resolve_summary_citations(
    summary_text: str,
    available_citations,
    *,
    require_citation: bool = True,
) -> tuple[SummaryCitation, ...]:
    """
    Return only citations actually referenced by the summary.

    Rejects any [Cxxx] label that was not available to the model.
    """

    source_ids = extract_summary_source_ids(
        summary_text
    )

    citation_map = {
        citation.source_id: citation
        for citation in available_citations
    }

    if require_citation and not source_ids:
        raise SummaryCitationError(
            "Generated summary contains no SEC citations."
        )

    invalid_ids = [
        source_id
        for source_id in source_ids
        if source_id not in citation_map
    ]

    if invalid_ids:
        raise SummaryCitationError(
            "Generated summary referenced invalid SEC "
            f"citations: {', '.join(invalid_ids)}"
        )

    return tuple(
        citation_map[source_id]
        for source_id in source_ids
    )