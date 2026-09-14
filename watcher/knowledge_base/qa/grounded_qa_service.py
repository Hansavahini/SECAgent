import re
from dataclasses import dataclass
from datetime import date

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.retrieval.hybrid_retriever import (
    HybridRetriever,
)


class GroundedQAError(Exception):
    """Raised when a grounded SEC answer cannot be produced safely."""


@dataclass(frozen=True)
class GroundedCitation:
    source_id: str
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


@dataclass(frozen=True)
class GroundedAnswer:
    answer: str
    citations: list[GroundedCitation]
    evidence_count: int
    found: bool


class GroundedQAService:
    NO_ANSWER = "NOT_FOUND_IN_SEC_EVIDENCE"

    def __init__(
        self,
        *,
        retriever=None,
        generation_service=None,
    ):
        self.retriever = (
            retriever
            or HybridRetriever()
        )

        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

    def answer(
        self,
        question: str,
        *,
        ticker=None,
        form=None,
        item_number=None,
        date_from=None,
        date_to=None,
        top_k=6,
    ) -> GroundedAnswer:

        question = str(
            question or ""
        ).strip()

        if not question:
            raise GroundedQAError(
                "Question cannot be empty."
            )

        evidence = self.retriever.search(
            question,
            ticker=ticker,
            form=form,
            item_number=item_number,
            date_from=date_from,
            date_to=date_to,
            top_k=top_k,
        )

        if not evidence:
            return GroundedAnswer(
                answer=self.NO_ANSWER,
                citations=[],
                evidence_count=0,
                found=False,
            )

        prompt = self._build_prompt(
            question,
            evidence,
        )

        answer = (
            self.generation_service
            .generate(
                prompt,
                temperature=0.0,
            )
            .strip()
        )

        if answer == self.NO_ANSWER:
            return GroundedAnswer(
                answer=answer,
                citations=[],
                evidence_count=len(evidence),
                found=False,
            )

        citation_numbers = (
            self._extract_citations(
                answer,
                len(evidence),
            )
        )

        if not citation_numbers:
            raise GroundedQAError(
                "Model returned an answer without "
                "valid SEC evidence citations."
            )

        citations = []

        for number in citation_numbers:
            result = evidence[
                number - 1
            ]

            citations.append(
                GroundedCitation(
                    source_id=f"S{number}",
                    chunk_id=(
                        result.chunk_id
                    ),
                    ticker=(
                        result.ticker
                    ),
                    form=(
                        result.form
                    ),
                    filing_date=(
                        result.filing_date
                    ),
                    accession_number=(
                        result.accession_number
                    ),
                    document_type=(
                        result.document_type
                    ),
                    document_name=(
                        result.document_name
                    ),
                    is_primary=(
                        result.is_primary
                    ),
                    item_number=(
                        result.item_number
                    ),
                    section_title=(
                        result.section_title
                    ),
                )
            )

        return GroundedAnswer(
            answer=answer,
            citations=citations,
            evidence_count=len(evidence),
            found=True,
        )

    def _build_prompt(
        self,
        question,
        evidence,
    ) -> str:

        sources = []

        for number, result in enumerate(
            evidence,
            start=1,
        ):
            source_lines = [
                f"[S{number}]",
                f"Ticker: {result.ticker}",
                f"Form: {result.form}",
                (
                    "Filing date: "
                    f"{result.filing_date}"
                ),
                (
                    "Accession: "
                    f"{result.accession_number}"
                ),
                (
                    "Document type: "
                    f"{result.document_type}"
                ),
                (
                    "Document name: "
                    f"{result.document_name}"
                ),
                (
                    "Primary document: "
                    f"{result.is_primary}"
                ),
                (
                    "SEC Item: "
                    f"{result.item_number or '<none>'}"
                ),
                (
                    "Section: "
                    f"{result.section_title or '<none>'}"
                ),
                "Text:",
                result.text,
            ]

            sources.append(
                "\n".join(
                    source_lines
                )
            )

        evidence_text = (
            "\n\n".join(
                sources
            )
        )

        return f"""
You are an SEC filing evidence assistant.

STRICT RULES:

1. Answer ONLY from the SEC evidence below.
2. Do not use general knowledge or prior knowledge.
3. Do not invent facts, numbers, dates, or conclusions.
4. Every factual statement must include at least one
   evidence citation such as [S1] or [S2].
5. Use only source labels that appear below.
6. Prefer the source containing the actual disclosed
   information over a source that merely references
   another document.
7. When an exhibit contains the actual financial results,
   use the exhibit evidence rather than merely saying
   that a press release was attached.
8. If the evidence does not contain enough information
   to answer the question, respond with exactly:

{self.NO_ANSWER}

Do not add anything else when using that response.

QUESTION:

{question}

SEC EVIDENCE:

{evidence_text}

ANSWER:
""".strip()

    def _extract_citations(
        self,
        answer: str,
        evidence_count: int,
    ) -> list[int]:

        matches = re.findall(
            r"\[S(\d+)\]",
            answer,
        )

        citation_numbers = []

        for value in matches:
            number = int(value)

            if (
                number < 1
                or number > evidence_count
            ):
                raise GroundedQAError(
                    "Model referenced an invalid "
                    f"evidence source: S{number}."
                )

            if (
                number
                not in citation_numbers
            ):
                citation_numbers.append(
                    number
                )

        return citation_numbers