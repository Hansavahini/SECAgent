from dataclasses import dataclass
from datetime import date

from watcher.knowledge_base.retrieval.keyword_retriever import (
    KeywordRetriever,
)
from watcher.knowledge_base.retrieval.vector_retriever import (
    VectorRetriever,
)


@dataclass(frozen=True)
class HybridRetrievalResult:
    chunk_id: int
    ticker: str
    form: str
    filing_date: date | None
    accession_number: str

    document_type: str
    document_name: str
    is_primary: bool

    item_number: str
    section_title: str
    text: str

    hybrid_score: float
    vector_score: float
    keyword_score: float


class HybridRetriever:
    VECTOR_WEIGHT = 0.6
    KEYWORD_WEIGHT = 0.4

    def __init__(
        self,
        vector_retriever=None,
        keyword_retriever=None,
    ):
        self.vector_retriever = (
            vector_retriever
            or VectorRetriever()
        )

        self.keyword_retriever = (
            keyword_retriever
            or KeywordRetriever()
        )

    def search(
        self,
        query: str,
        *,
        ticker=None,
        form=None,
        item_number=None,
        date_from=None,
        date_to=None,
        top_k=8,
        candidate_k=20,
    ) -> list[HybridRetrievalResult]:

        if top_k <= 0 or top_k > 50:
            raise ValueError(
                "top_k must be between 1 and 50."
            )

        if candidate_k < top_k:
            candidate_k = top_k

        vector_results = (
            self.vector_retriever.search(
                query,
                ticker=ticker,
                form=form,
                item_number=item_number,
                date_from=date_from,
                date_to=date_to,
                top_k=candidate_k,
            )
        )

        keyword_results = (
            self.keyword_retriever.search(
                query,
                ticker=ticker,
                form=form,
                item_number=item_number,
                date_from=date_from,
                date_to=date_to,
                top_k=candidate_k,
            )
        )

        combined = {}

        for rank, result in enumerate(
            vector_results,
            start=1,
        ):
            record = combined.setdefault(
                result.chunk_id,
                {
                    "chunk_id": result.chunk_id,
                    "ticker": result.ticker,
                    "form": result.form,
                    "filing_date": (
                        result.filing_date
                    ),
                    "accession_number": (
                        result.accession_number
                    ),
                    "document_type": (
                        result.document_type
                    ),
                    "document_name": (
                        result.document_name
                    ),
                    "is_primary": (
                        result.is_primary
                    ),
                    "item_number": (
                        result.item_number
                    ),
                    "section_title": (
                        result.section_title
                    ),
                    "text": result.text,
                    "vector_score": 0.0,
                    "keyword_score": 0.0,
                },
            )

            record["vector_score"] = (
                1.0 / (60 + rank)
            )

        for rank, result in enumerate(
            keyword_results,
            start=1,
        ):
            record = combined.setdefault(
                result.chunk_id,
                {
                    "chunk_id": result.chunk_id,
                    "ticker": result.ticker,
                    "form": result.form,
                    "filing_date": (
                        result.filing_date
                    ),
                    "accession_number": (
                        result.accession_number
                    ),
                    "document_type": (
                        result.document_type
                    ),
                    "document_name": (
                        result.document_name
                    ),
                    "is_primary": (
                        result.is_primary
                    ),
                    "item_number": (
                        result.item_number
                    ),
                    "section_title": (
                        result.section_title
                    ),
                    "text": result.text,
                    "vector_score": 0.0,
                    "keyword_score": 0.0,
                },
            )

            record["keyword_score"] = (
                1.0 / (60 + rank)
            )

        results = []

        for record in combined.values():
            hybrid_score = (
                record["vector_score"]
                * self.VECTOR_WEIGHT
                + record["keyword_score"]
                * self.KEYWORD_WEIGHT
            )

            results.append(
                HybridRetrievalResult(
                    chunk_id=record["chunk_id"],
                    ticker=record["ticker"],
                    form=record["form"],
                    filing_date=(
                        record["filing_date"]
                    ),
                    accession_number=(
                        record["accession_number"]
                    ),
                    document_type=(
                        record["document_type"]
                    ),
                    document_name=(
                        record["document_name"]
                    ),
                    is_primary=(
                        record["is_primary"]
                    ),
                    item_number=(
                        record["item_number"]
                    ),
                    section_title=(
                        record["section_title"]
                    ),
                    text=record["text"],
                    hybrid_score=hybrid_score,
                    vector_score=(
                        record["vector_score"]
                    ),
                    keyword_score=(
                        record["keyword_score"]
                    ),
                )
            )

        results.sort(
            key=lambda item: (
                -item.hybrid_score,
                item.chunk_id,
            )
        )

        return results[:top_k]