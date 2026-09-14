from dataclasses import dataclass
from datetime import date

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
)
from django.db.models import (
    ExpressionWrapper,
    F,
    FloatField,
    Value,
)

from watcher.knowledge_base.models import FilingChunk


class KeywordRetrievalError(Exception):
    """Raised when keyword evidence cannot be retrieved."""


@dataclass(frozen=True)
class KeywordRetrievalResult:
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
    rank: float


class KeywordRetriever:
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
    ) -> list[KeywordRetrievalResult]:

        query = str(query or "").strip()

        if not query:
            raise KeywordRetrievalError(
                "Search query cannot be empty."
            )

        if top_k <= 0 or top_k > 50:
            raise KeywordRetrievalError(
                "top_k must be between 1 and 50."
            )

        search_vector = (
            SearchVector(
                "text",
                weight="A",
                config="english",
            )
            + SearchVector(
                "section_title",
                weight="B",
                config="english",
            )
            + SearchVector(
                "item_number",
                weight="C",
                config="english",
            )
        )

        web_query = SearchQuery(
            query,
            config="english",
            search_type="websearch",
        )

        phrase_query = SearchQuery(
            query,
            config="english",
            search_type="phrase",
        )

        results = (
            FilingChunk.objects
            .select_related(
                "document",
                "filing",
                "filing__company",
            )
            .annotate(
                search=search_vector,
                web_rank=SearchRank(
                    search_vector,
                    web_query,
                    cover_density=True,
                    normalization=32,
                ),
                phrase_rank=SearchRank(
                    search_vector,
                    phrase_query,
                    cover_density=True,
                    normalization=32,
                ),
            )
            .annotate(
                keyword_score=ExpressionWrapper(
                    F("web_rank")
                    + (
                        F("phrase_rank")
                        * Value(2.0)
                    ),
                    output_field=FloatField(),
                )
            )
            .filter(
                search=web_query,
            )
        )

        if ticker:
            results = results.filter(
                filing__company__ticker=(
                    str(ticker)
                    .strip()
                    .upper()
                )
            )

        if form:
            results = results.filter(
                filing__form=(
                    str(form)
                    .strip()
                    .upper()
                )
            )

        if item_number:
            results = results.filter(
                item_number__iexact=(
                    str(item_number).strip()
                )
            )

        if date_from:
            results = results.filter(
                filing__filing_date__gte=(
                    date_from
                )
            )

        if date_to:
            results = results.filter(
                filing__filing_date__lte=(
                    date_to
                )
            )

        results = results.order_by(
            "-keyword_score",
            "id",
        )[:top_k]

        output = []

        for row in results:
            document = row.document

            output.append(
                KeywordRetrievalResult(
                    chunk_id=row.id,
                    ticker=(
                        row.filing.company.ticker
                    ),
                    form=row.filing.form,
                    filing_date=(
                        row.filing.filing_date
                    ),
                    accession_number=(
                        row.filing.accession_number
                    ),
                    document_type=(
                        document.document_type
                        if document
                        else ""
                    ),
                    document_name=(
                        document.document_name
                        if document
                        else ""
                    ),
                    is_primary=(
                        document.is_primary
                        if document
                        else False
                    ),
                    item_number=(
                        row.item_number
                    ),
                    section_title=(
                        row.section_title
                    ),
                    text=row.text,
                    rank=float(
                        row.keyword_score
                    ),
                )
            )

        return output