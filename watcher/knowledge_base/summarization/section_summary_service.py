from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)


class SectionSummaryError(Exception):
    """Raised when a filing section cannot be summarized."""


@dataclass(frozen=True)
class FilingSectionGroup:
    section_index: int
    item_number: str
    section_title: str
    category: str
    chunk_ids: tuple[int, ...]
    chunk_count: int
    text: str


@dataclass(frozen=True)
class SectionSummary:
    section_index: int
    item_number: str
    section_title: str
    category: str
    chunk_ids: tuple[int, ...]
    chunk_count: int
    summary: str


class SectionSummaryService:
    """
    Builds concise, source-grounded summaries from SEC filing sections.

    Design:
    - Company-independent.
    - Form-aware, not ticker-aware.
    - Reuses existing FilingChunk item/section metadata.
    - Groups contiguous chunks belonging to the same SEC section.
    - Does not use embeddings/top-k retrieval for filing summaries.
    - Financial headline facts are handled separately through XBRL.
    - Narrative bullets must cite the source chunk(s) supporting them.
    """
    MAX_SECTION_CHARS = 32000
    MAX_SUMMARY_TOKENS = 512

    SKIPPED_CATEGORIES = {
        "financial_statements",
        "administrative",
    }

    def __init__(
        self,
        *,
        generation_service=None,
        max_section_chars: int | None = None,
    ):
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

        self.max_section_chars = (
            max_section_chars
            or self.MAX_SECTION_CHARS
        )

        if self.max_section_chars < 2000:
            raise ValueError(
                "max_section_chars must be at least 2000."
            )

    def build_groups(
        self,
        chunks: Iterable,
        *,
        form: str,
    ) -> tuple[FilingSectionGroup, ...]:
        """
        Convert ordered FilingChunk rows into contiguous SEC sections.

        We intentionally do not group globally by Item number alone.

        Example:
            Part I Item 1  = Financial Statements
            Part II Item 1 = Legal Proceedings
        """

        ordered_chunks = sorted(
            list(chunks),
            key=lambda chunk: (
                chunk.chunk_index,
                chunk.id,
            ),
        )

        if not ordered_chunks:
            return ()

        raw_groups: list[list] = []
        current_chunks = []
        current_key = None

        for chunk in ordered_chunks:
            item_number = self._clean(
                chunk.item_number
            )

            section_title = self._clean(
                chunk.section_title
            )

            key = (
                item_number.lower(),
                section_title.lower(),
            )

            if (
                current_chunks
                and key != current_key
            ):
                raw_groups.append(
                    current_chunks
                )

                current_chunks = []

            current_key = key

            current_chunks.append(
                chunk
            )

        if current_chunks:
            raw_groups.append(
                current_chunks
            )

        groups = []

        for section_index, group_chunks in enumerate(
            raw_groups
        ):
            first = group_chunks[0]

            item_number = self._clean(
                first.item_number
            )

            section_title = self._clean(
                first.section_title
            )

            joined_text = self._joined_text(
                group_chunks
            )

            category = self._classify_section(
                form=form,
                item_number=item_number,
                section_title=section_title,
                text=joined_text[:2000],
            )

            if not self._should_summarize(
                category
            ):
                continue

            groups.append(
                FilingSectionGroup(
                    section_index=section_index,
                    item_number=item_number,
                    section_title=section_title,
                    category=category,
                    chunk_ids=tuple(
                        chunk.id
                        for chunk in group_chunks
                    ),
                    chunk_count=len(
                        group_chunks
                    ),
                    text=joined_text,
                )
            )

        return tuple(groups)

    def summarize_groups(
        self,
        groups: Iterable[FilingSectionGroup],
        *,
        ticker: str,
        form: str,
        filing_date,
        accession_number: str,
        document_name: str,
    ) -> tuple[SectionSummary, ...]:
        """
        Summarize each relevant SEC section independently.
        """

        results = []

        for group in groups:
            results.append(
                self.summarize_group(
                    group,
                    ticker=ticker,
                    form=form,
                    filing_date=filing_date,
                    accession_number=accession_number,
                    document_name=document_name,
                )
            )

        return tuple(results)

    def summarize_group(
        self,
        group: FilingSectionGroup,
        *,
        ticker: str,
        form: str,
        filing_date,
        accession_number: str,
        document_name: str,
    ) -> SectionSummary:
        """
        Produce one concise source-grounded summary
        for one SEC section.
        """

        text = str(
            group.text or ""
        ).strip()

        if not text:
            raise SectionSummaryError(
                "Cannot summarize an empty SEC section."
            )

        batches = self._split_text(
            text
        )

        partials = []

        for batch_number, batch in enumerate(
            batches,
            start=1,
        ):
            prompt = self._batch_prompt(
                ticker=ticker,
                form=form,
                filing_date=filing_date,
                accession_number=accession_number,
                document_name=document_name,
                group=group,
                batch=batch,
                batch_number=batch_number,
                batch_count=len(batches),
            )

            response = (
                self.generation_service.generate(
                    prompt,
                    temperature=0.0,
                    max_tokens=self.MAX_SUMMARY_TOKENS,
                )
            )

            response = str(
                response or ""
            ).strip()

            if response:
                partials.append(
                    response
                )

        if not partials:
            final_summary = (
                "NO_MATERIAL_SUMMARY"
            )

        elif len(partials) == 1:
            final_summary = (
                partials[0]
            )

        else:
            final_summary = (
                self._reduce_partials(
                    group=group,
                    partials=partials,
                    ticker=ticker,
                    form=form,
                    filing_date=filing_date,
                    accession_number=accession_number,
                    document_name=document_name,
                )
            )

        return SectionSummary(
            section_index=group.section_index,
            item_number=group.item_number,
            section_title=group.section_title,
            category=group.category,
            chunk_ids=group.chunk_ids,
            chunk_count=group.chunk_count,
            summary=str(
                final_summary or ""
            ).strip(),
        )

    @staticmethod
    def _detail_limits(
        text: str,
    ) -> tuple[int, str]:
        """
        Choose a summary-size ceiling from the amount of source text.

        This is intentionally company-independent and filing-independent.
        It does not target a specific ticker, accession number, or file.
        Short source sections stay short; larger source sections are
        allowed to retain more independently supported material facts.
        """

        text_length = len(
            str(text or "").strip()
        )

        if text_length <= 8000:
            return 3, "1-3"

        if text_length <= 18000:
            return 4, "2-4"

        if text_length <= 28000:
            return 5, "3-5"

        return 7, "4-6"

    @staticmethod
    def _normalized_item_number(
        item_number: str,
    ) -> str:
        """
        Normalize SEC item labels without ticker-specific assumptions.

        Examples:
            "Item 9.01" -> "9.01"
            "9.01"      -> "9.01"
        """

        value = " ".join(
            str(item_number or "").split()
        ).strip().lower()

        if value.startswith("item "):
            value = value[5:].strip()

        return value

    def _batch_prompt(
        self,
        *,
        ticker: str,
        form: str,
        filing_date,
        accession_number: str,
        document_name: str,
        group: FilingSectionGroup,
        batch: str,
        batch_number: int,
        batch_count: int,
    ) -> str:
        """
        Ask the model for concise candidate bullets.

        Every bullet must retain the exact supporting
        FilingChunk IDs.
        """

        max_bullets, preferred_bullets = (
            self._detail_limits(
                batch
            )
        )

        return f"""
You are extracting material facts from ONE section of an SEC filing.

Use ONLY the SEC source text below.

Company ticker: {ticker}
Form: {form}
Filing date: {filing_date or "Unknown"}
Accession number: {accession_number}
Document: {document_name}

SEC item: {group.item_number or "Unlabeled"}
Section title: {group.section_title or "Unlabeled"}
Section category: {group.category}

Section batch: {batch_number} of {batch_count}

SOURCE FORMAT

Every source block begins with a label such as:

[CHUNK 123]

Every bullet you return MUST begin with the exact chunk label or
labels that support the COMPLETE statement.

Valid examples:

[CHUNK 123] The company disclosed a material legal proceeding.

[CHUNK 123, CHUNK 124] The company disclosed related regulatory
developments supported by both source chunks.

STRICT RULES

1. Use ONLY information explicitly stated in the supplied SEC text.

2. Never use outside knowledge.

3. Never invent, estimate or infer an unsupported relationship.

4. Never combine two numbers into a relationship unless that
   relationship is explicitly supported by the cited chunk(s).

5. Never state:
      "X shares at an average price of Y"
   unless the cited source explicitly states that Y applies to
   those X shares.

6. Never combine separate:
   - transactions
   - programs
   - authorizations
   - proceedings
   - jurisdictions
   - reporting periods
   - legal matters
   - regulatory matters
   into a single claim unless the cited source explicitly connects them.

7. Preserve amounts, percentages, dates and quantities exactly.

8. Preserve reporting-period meaning exactly.

9. Never describe:
      nine-month/YTD data as quarterly
      quarterly data as annual
      annual data as quarterly

10. Do not calculate new financial figures.

11. Do not reconstruct headline financial statements.
    Verified XBRL facts handle those separately.

12. Do not summarize tables row-by-row.

13. Keep all distinct material information relevant to this SEC section.

14. Remove boilerplate and genuine repetition only.
    Do not remove a distinct material fact merely to shorten the summary.

15. Each bullet must represent ONE independently supportable
    material fact, event, action, disclosure or commitment.

16. Include enough explicit source context to make each retained fact
    understandable, such as:
    - what happened
    - who or what it concerns
    - the relevant date
    - the stated amount or percentage
    - the stated action
    - the stated purpose

    Include such context ONLY when it is explicitly supported by
    the cited source chunk(s).

17. Prefer one complete, clear sentence per bullet.

18. Maximum {max_bullets} bullets for this source batch.

19. Prefer {preferred_bullets} bullets only when that many distinct
    material facts are actually present. Never add or retain a weak,
    repetitive or administrative fact merely to reach a target count.

20. Prioritize information that explains the material event, transaction,
    agreement, operational development, financial fact, risk, commitment
    or management action disclosed by the SEC source.

21. Normally OMIT purely administrative filing mechanics such as:
    - cover-page references
    - table-of-contents references
    - exhibit-index listings
    - signature-page references
    - generic incorporation-by-reference language
    - generic "qualified in its entirety" language
    - routine legal-opinion or consent references

    Retain one of these only when it contains a distinct material fact
    necessary to understand the filing.

22. Every bullet MUST include at least one valid [CHUNK n] label
    appearing in the supplied source text.

23. Never invent a chunk number.

24. If one sentence requires different source chunks to support
    different pieces of the claim, include ALL required chunk labels.

25. Do not add interpretation, significance, causation, investor impact,
    business impact or implications unless the cited SEC source
    explicitly states them.

26. If there is no material information worth retaining, return
    exactly:

NO_MATERIAL_SUMMARY

SEC SOURCE TEXT
----------------
{batch}

Return only source-grounded concise bullets.
""".strip()

    def _reduce_partials(
        self,
        *,
        group: FilingSectionGroup,
        partials: list[str],
        ticker: str,
        form: str,
        filing_date,
        accession_number: str,
        document_name: str,
    ) -> str:
        """
        Consolidate candidate bullets from multiple batches.

        Chunk labels must remain attached to every final claim.
        """

        usable = [
            partial.strip()
            for partial in partials
            if (
                partial
                and partial.strip()
                and partial.strip()
                != "NO_MATERIAL_SUMMARY"
            )
        ]

        if not usable:
            return "NO_MATERIAL_SUMMARY"

        if len(usable) == 1:
            return usable[0]

        max_bullets, preferred_bullets = (
            self._detail_limits(
                group.text
            )
        )

        combined = "\n\n".join(
            (
                f"[PART {index}]\n"
                f"{partial}"
            )
            for index, partial in enumerate(
                usable,
                start=1,
            )
        )
        prompt = f"""
You are consolidating source-grounded candidate bullets from
ONE section of an SEC filing.

Use ONLY the candidate bullets below.

Company ticker: {ticker}
Form: {form}
Filing date: {filing_date or "Unknown"}
Accession number: {accession_number}
Document: {document_name}

SEC item: {group.item_number or "Unlabeled"}
Section title: {group.section_title or "Unlabeled"}
Section category: {group.category}

STRICT RULES

1. Do not add any new facts.

2. Do not use outside knowledge.

3. Preserve the TYPE of each disclosure exactly.
   Do not relabel:
   - investigation as litigation
   - tariff or trade action as a legal proceeding
   - regulatory review as a lawsuit
   - authorization as a transaction
   - ASR agreement as a repurchase authorization
   unless the candidate bullets explicitly describe it that way.

4. Every final bullet MUST preserve its supporting
   [CHUNK n] source labels.

5. Never invent a chunk label.

6. If claims from multiple candidate bullets are combined,
   retain ALL chunk labels needed to support the complete claim.

7. Never combine separate numbers into a new relationship.

8. Never combine separate:
   - transactions
   - programs
   - authorizations
   - agreements
   - investigations
   - lawsuits
   - regulatory matters
   - proceedings
   - jurisdictions
   - reporting periods

   unless the candidate bullets explicitly show they are connected.

9. If two facts are merely near each other, do NOT assume
   they are related.

10. Each final bullet should represent ONE material idea.

11. If combining two bullets could change the relationship
    between facts, keep them separate.

12. Preserve amounts, percentages, dates and quantities exactly.

13. Preserve quarterly, YTD, nine-month and annual reporting-period
    meaning exactly.

14. Never describe:
    - nine-month/YTD data as quarterly
    - quarterly data as annual
    - annual data as quarterly

15. Remove duplicate claims, boilerplate and genuine repetition only.

16. Keep every distinct material claim that remains independently
    supported by its cited chunk(s).

17. Prioritize the facts needed to understand the material disclosure.
    Do not retain administrative filing mechanics merely because they
    appeared in a candidate bullet.

18. Normally omit:
    - cover-page references
    - table-of-contents references
    - exhibit-index listings
    - signature-page references
    - generic incorporation-by-reference language
    - generic "qualified in its entirety" language
    - routine legal-opinion or consent references

    Retain one only when it carries a distinct material fact necessary
    to understand the disclosure.

19. Keep each final bullet focused on ONE independently supportable
    material idea.

20. Maximum {max_bullets} final bullets for this section.

21. Prefer {preferred_bullets} final bullets only when that many
    independently supported material facts exist. Never keep a low-value
    fact merely to reach a target count.

22. Every final bullet must begin with its supporting
    [CHUNK n] label or labels.

23. Never add interpretation, causation, significance, investor impact,
    business impact or implications unless explicitly present in the
    candidate bullets and supported by their cited chunks.

24. If a claim is not clearly supported, OMIT IT.

25. If nothing material remains, return exactly:

NO_MATERIAL_SUMMARY

CANDIDATE BULLETS
-----------------
{combined}

Return only the consolidated source-grounded bullets.
""".strip()
        response = (
            self.generation_service.generate(
                prompt,
                temperature=0.0,
                max_tokens=self.MAX_SUMMARY_TOKENS,
            )
        )

        return str(
            response or ""
        ).strip()

    def _classify_section(
        self,
        *,
        form: str,
        item_number: str,
        section_title: str,
        text: str,
    ) -> str:
        """
        Generic SEC section classification.

        Section title is authoritative when recognizable.
        Body text is only a fallback.
        """

        normalized_form = (
            str(form or "")
            .strip()
            .upper()
        )

        title = (
            self._clean(
                section_title
            )
            .lower()
        )

        body = (
            self._clean(
                text[:1200]
            )
            .lower()
        )

        # ---------------------------------------------
        # Explicit SEC section title classification.
        # ---------------------------------------------

        if self._contains_any(
            title,
            (
                "risk factors",
                "risk factor",
            ),
        ):
            return "risk"

        if self._contains_any(
            title,
            (
                "legal proceedings",
                "legal proceeding",
            ),
        ):
            return "legal"

        if self._contains_any(
            title,
            (
                "management's discussion",
                "management’s discussion",
                "management discussion",
                "results of operations",
                "financial condition and results of operations",
            ),
        ):
            return "md&a"

        if self._contains_any(
            title,
            (
                "quantitative and qualitative disclosures about market risk",
                "quantitative and qualitative disclosures",
                "market risk",
            ),
        ):
            return "market_risk"

        if self._contains_any(
            title,
            (
                "controls and procedures",
                "disclosure controls",
                "internal control",
            ),
        ):
            return "controls"

        if self._contains_any(
            title,
            (
                "unregistered sales",
                "issuer purchases",
                "use of proceeds",
                "repurchases",
            ),
        ):
            return "capital_activity"

        if self._contains_any(
            title,
            (
                "other information",
                "other events",
            ),
        ):
            return "other_material"

        if self._contains_any(
            title,
            (
                "financial statements",
                "condensed consolidated",
                "statements of operations",
                "statement of operations",
                "balance sheets",
                "balance sheet",
                "statements of cash flows",
                "statement of cash flows",
                "statements of shareholders",
                "statements of stockholders",
            ),
        ):
            return "financial_statements"

        if self._contains_any(
            title,
            (
                "exhibits",
                "exhibit index",
                "signatures",
                "signature",
            ),
        ):
            return "administrative"

        # 8-K Item 9.01 is normally the filing's exhibit / financial
        # statement index. The actual exhibit documents are handled
        # separately by the document summarization pipeline, so treating
        # this index as a material event would duplicate low-value filing
        # mechanics in the final summary.
        if (
            normalized_form == "8-K"
            and self._normalized_item_number(
                item_number
            ).startswith("9.01")
        ):
            return "administrative"

        # Other labeled 8-K items are event-driven.
        if (
            normalized_form == "8-K"
            and item_number
        ):
            return "8k_event"

        # ---------------------------------------------
        # Body fallback classification.
        # ---------------------------------------------

        if self._contains_any(
            body,
            (
                "risk factors",
                "risk factor",
            ),
        ):
            return "risk"

        if self._contains_any(
            body,
            (
                "legal proceedings",
                "litigation",
                "legal matter",
            ),
        ):
            return "legal"

        if self._contains_any(
            body,
            (
                "management's discussion",
                "management’s discussion",
                "results of operations",
                "financial condition",
            ),
        ):
            return "md&a"

        if self._contains_any(
            body,
            (
                "liquidity",
                "capital resources",
                "contractual obligations",
                "purchase obligations",
            ),
        ):
            return "liquidity_capital"

        if self._contains_any(
            body,
            (
                "controls and procedures",
                "disclosure controls",
                "internal control over financial reporting",
            ),
        ):
            return "controls"

        if self._contains_any(
            body,
            (
                "market risk",
                "quantitative and qualitative",
            ),
        ):
            return "market_risk"

        if self._contains_any(
            body,
            (
                "unregistered sales",
                "issuer purchases",
                "repurchases",
                "use of proceeds",
            ),
        ):
            return "capital_activity"

        if self._contains_any(
            body,
            (
                "other information",
                "trading arrangement",
                "rule 10b5-1",
                "10b5-1",
            ),
        ):
            return "other_material"

        if self._contains_any(
            body,
            (
                "financial statements",
                "condensed consolidated",
                "statements of operations",
                "balance sheets",
                "statements of cash flows",
            ),
        ):
            return "financial_statements"

        if self._contains_any(
            body,
            (
                "exhibit index",
                "signatures",
            ),
        ):
            return "administrative"

        return "other"

    @classmethod
    def _should_summarize(
        cls,
        category: str,
    ) -> bool:
        """
        Skip sections that should not consume LLM generation.

        Financial statement headline facts are handled by XBRL.
        Administrative sections do not add useful summary content.
        """

        return (
            category
            not in cls.SKIPPED_CATEGORIES
        )

    def _split_text(
        self,
        text: str,
    ) -> tuple[str, ...]:
        """
        Split long SEC sections at natural boundaries where possible.
        """

        text = str(
            text or ""
        ).strip()

        if not text:
            return ()

        if len(text) <= self.max_section_chars:
            return (text,)

        batches = []
        start = 0
        text_length = len(text)

        while start < text_length:
            hard_end = min(
                start + self.max_section_chars,
                text_length,
            )

            end = hard_end

            if hard_end < text_length:
                paragraph_break = text.rfind(
                    "\n\n",
                    start,
                    hard_end,
                )

                sentence_break = text.rfind(
                    ". ",
                    start,
                    hard_end,
                )

                candidate = max(
                    paragraph_break,
                    sentence_break,
                )

                minimum_acceptable = (
                    start
                    + int(
                        self.max_section_chars
                        * 0.60
                    )
                )

                if candidate >= minimum_acceptable:
                    end = candidate + 1

            if end <= start:
                end = hard_end

            batch = (
                text[start:end]
                .strip()
            )

            if batch:
                batches.append(
                    batch
                )

            start = end

        return tuple(
            batches
        )

    @staticmethod
    def _joined_text(
        chunks,
    ) -> str:
        """
        Preserve chunk IDs directly in model input.

        These IDs later allow every generated bullet to be
        validated against its claimed source chunk(s).
        """

        parts = []

        for chunk in chunks:
            text = str(
                chunk.text or ""
            ).strip()

            if not text:
                continue

            parts.append(
                (
                    f"[CHUNK {chunk.id}]\n"
                    f"{text}"
                )
            )

        return "\n\n".join(
            parts
        )

    @staticmethod
    def _contains_any(
        text: str,
        values: tuple[str, ...],
    ) -> bool:

        return any(
            value in text
            for value in values
        )

    @staticmethod
    def _clean(
        value,
    ) -> str:

        return " ".join(
            str(
                value or ""
            ).split()
        ).strip()