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

    GENERATION_EVIDENCE_LIMIT = 3
    MAX_CHARS_PER_SOURCE = 1800

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

        retrieved_evidence = self.retriever.search(
            question,
            ticker=ticker,
            form=form,
            item_number=item_number,
            date_from=date_from,
            date_to=date_to,
            top_k=top_k,
        )

        if not retrieved_evidence:
            return GroundedAnswer(
                answer=self.NO_ANSWER,
                citations=[],
                evidence_count=0,
                found=False,
            )

        evidence = self._prepare_generation_evidence(
            retrieved_evidence
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

        # Small local models can ignore formatting on the
        # first attempt. If so, discard the uncited answer
        # and regenerate from evidence only.
        if not citation_numbers:
            repair_prompt = (
                self._build_citation_repair_prompt(
                    question=question,
                    evidence=evidence,
                )
            )

            answer = (
                self.generation_service
                .generate(
                    repair_prompt,
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
                    chunk_id=result.chunk_id,
                    ticker=result.ticker,
                    form=result.form,
                    filing_date=result.filing_date,
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

    def _prepare_generation_evidence(
        self,
        evidence,
    ):
        return list(
            evidence[
                :self.GENERATION_EVIDENCE_LIMIT
            ]
        )

    def _trim_text(
        self,
        text,
    ) -> str:
        value = str(
            text or ""
        ).strip()

        if (
            len(value)
            <= self.MAX_CHARS_PER_SOURCE
        ):
            return value

        return (
            value[
                :self.MAX_CHARS_PER_SOURCE
            ].rstrip()
            + "\n[TRUNCATED]"
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
                    "SEC Item: "
                    f"{result.item_number or '<none>'}"
                ),
                (
                    "Section: "
                    f"{result.section_title or '<none>'}"
                ),
                "Text:",
                self._trim_text(
                    result.text
                ),
            ]

            sources.append(
                "\n".join(
                    source_lines
                )
            )

        evidence_text = "\n\n".join(
            sources
        )

        allowed_labels = ", ".join(
            f"[S{number}]"
            for number in range(
                1,
                len(evidence) + 1,
            )
        )

        return f"""
You answer questions using SEC filing evidence.

Use ONLY the evidence below.

Allowed source labels:
{allowed_labels}

Important:
- Use ONLY these exact source labels.
- Never write [S#].
- Never invent a source.
- Every factual sentence must end with at least one
  source label such as [S1].
- Do not use outside knowledge.
- Do not invent numbers.
- Keep the answer concise.
- Answer the user's actual question.
- If the evidence is insufficient, return exactly:

{self.NO_ANSWER}

QUESTION:

{question}

SEC EVIDENCE:

{evidence_text}

Return a concise factual answer with valid source labels.
""".strip()

    def _build_citation_repair_prompt(
        self,
        *,
        question,
        evidence,
    ) -> str:

        sources = []

        for number, result in enumerate(
            evidence,
            start=1,
        ):
            sources.append(
                "\n".join(
                    [
                        f"[S{number}]",
                        (
                            f"Form: "
                            f"{result.form}"
                        ),
                        (
                            f"Date: "
                            f"{result.filing_date}"
                        ),
                        (
                            f"Section: "
                            f"{result.section_title}"
                        ),
                        "Text:",
                        self._trim_text(
                            result.text
                        ),
                    ]
                )
            )

        evidence_text = "\n\n".join(
            sources
        )

        allowed_labels = ", ".join(
            f"[S{number}]"
            for number in range(
                1,
                len(evidence) + 1,
            )
        )

        return f"""
Answer the SEC question below.

Allowed citations are ONLY:
{allowed_labels}

Rules:
- Use ONLY the supplied SEC evidence.
- Every factual sentence MUST end with one of the
  allowed citations.
- Never output [S#].
- Never invent a citation.
- Never invent a number.
- Do not repeat filing metadata unless relevant.
- Keep the answer short and directly responsive.
- If the evidence is insufficient, return exactly:

{self.NO_ANSWER}

QUESTION:

{question}

SEC EVIDENCE:

{evidence_text}

Return ONLY the cited answer.
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