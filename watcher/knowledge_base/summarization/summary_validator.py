from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.summarization.section_summary_service import (
    SectionSummary,
)


class SummaryValidationError(Exception):
    """Raised when narrative-summary validation cannot be completed."""


@dataclass(frozen=True)
class ClaimValidation:
    claim: str
    chunk_ids: tuple[int, ...]
    is_valid: bool
    reason: str


@dataclass(frozen=True)
class ValidatedSectionSummary:
    section_index: int
    item_number: str
    section_title: str
    category: str
    chunk_ids: tuple[int, ...]
    chunk_count: int
    summary: str
    accepted_claims: tuple[ClaimValidation, ...]
    rejected_claims: tuple[ClaimValidation, ...]


class SummaryValidator:
    """
    Validates LLM-generated narrative claims against the
    exact FilingChunk sources cited by each claim.

    Validation is deliberately fail-closed:

        unsupported / malformed / ambiguous
        -> reject claim

    Architecture:
        candidate claim
            -> citation validation
            -> literal numeric/date validation
            -> semantic entailment validation
            -> accepted or rejected

    This class contains no ticker-specific logic.
    """

    NO_MATERIAL_SUMMARY = "NO_MATERIAL_SUMMARY"

    CHUNK_PREFIX_RE = re.compile(
        r"""
        ^\s*
        \[
            (?P<labels>
                CHUNK\s+\d+
                (?:
                    \s*,\s*
                    CHUNK\s+\d+
                )*
            )
        \]
        \s*
        (?P<claim>.+?)
        \s*$
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    CHUNK_ID_RE = re.compile(
        r"CHUNK\s+(\d+)",
        re.IGNORECASE,
    )

    # Capture material numeric expressions conservatively.
    NUMBER_RE = re.compile(
        r"""
        (?<![A-Za-z])
        \$?
        -?
        (?:
            \d{1,3}(?:,\d{3})+
            |
            \d+
        )
        (?:\.\d+)?
        \s*
        (?:
            %
            |
            percent
            |
            billion
            |
            million
            |
            trillion
            |
            thousand
            |
            shares?
        )?
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    MONTH_DATE_RE = re.compile(
        r"""
        \b
        (?:
            January|February|March|April|May|June|
            July|August|September|October|November|December
        )
        \s+\d{1,2},
        \s+\d{4}
        \b
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    ISO_DATE_RE = re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b"
    )

    MAX_VALIDATION_TOKENS = 24
    DISCLOSURE_TYPE_TERMS = {
        "legal proceeding": (
            "legal proceeding",
            "legal proceedings",
        ),
        "litigation": (
            "litigation",
            "lawsuit",
            "court",
            "injunction",
        ),
        "investigation": (
            "investigation",
            "investigations",
            "investigating",
        ),
        "regulatory": (
            "regulatory",
            "regulation",
            "commission",
            "agency",
        ),
        "authorization": (
            "authorization",
            "authorized",
            "authorize",
        ),
        "repurchase program": (
            "repurchase program",
            "share repurchase program",
            "stock repurchase program",
        ),
        "asr": (
            "accelerated share repurchase",
            "accelerated share repurchase agreement",
            "asr",
            "asrs",
        ),
    }
    RELATIONSHIP_ANCHOR_RE = re.compile(
        r"""
        \$?\d+(?:\.\d+)?\s*(?:billion|million|thousand|%)
        |
        Article\s+\d+\(\d+\)
        |
        Supreme\s+Court
        |
        Ninth\s+Circuit
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    SOURCE_SENTENCE_RE = re.compile(
        r"(?<=[.!?])\s+"
    )
    def __init__(
        self,
        *,
        generation_service=None,
    ):
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

    def validate_sections(
        self,
        *,
        section_summaries: Iterable[SectionSummary],
        chunks: Iterable,
    ) -> tuple[ValidatedSectionSummary, ...]:
        """
        Validate all generated section summaries using the
        original FilingChunk text.
        """

        chunk_map = {
            int(chunk.id): chunk
            for chunk in chunks
        }

        results = []

        for section in section_summaries:
            results.append(
                self.validate_section(
                    section_summary=section,
                    chunk_map=chunk_map,
                )
            )

        return tuple(results)
    def _validate_disclosure_type(
        self,
        *,
        claim: str,
        source_text: str,
    ) -> str | None:
        """
        Prevent a generated claim from changing the source disclosure type.
        """

        normalized_claim = self._normalize_text(claim)
        normalized_source = self._normalize_text(source_text)

        for claim_term, source_terms in self.DISCLOSURE_TYPE_TERMS.items():
            if claim_term not in normalized_claim:
                continue

            supported = any(
                source_term in normalized_source
                for source_term in source_terms
            )

            if not supported:
                return (
                    "disclosure_type_not_supported:"
                    f"{claim_term}"
                )

        return None
    def _validate_relationship_proximity(
        self,
        *,
        claim: str,
        source_text: str,
    ) -> str | None:
        """
        Validate numeric/legal relationships sentence by sentence.

        A multi-sentence summary claim may legitimately contain
        different facts in different sentences. We therefore require
        the anchors within EACH individual claim sentence to coexist
        in one source sentence.

        This remains fail-closed:
        if a sentence combines multiple anchors that do not coexist
        in any source sentence, the claim is rejected.
        """

        claim_sentences = [
            self._normalize_text(sentence)
            for sentence in self.SOURCE_SENTENCE_RE.split(
                claim
            )
            if sentence.strip()
        ]

        source_sentences = [
            self._normalize_text(sentence)
            for sentence in self.SOURCE_SENTENCE_RE.split(
                source_text
            )
            if sentence.strip()
        ]

        for claim_sentence in claim_sentences:
            claim_anchors = [
                self._normalize_text(
                    match.group(0)
                )
                for match in self.RELATIONSHIP_ANCHOR_RE.finditer(
                    claim_sentence
                )
            ]

            # Zero or one anchor cannot create the type of
            # cross-value relationship this check is designed
            # to prevent.
            if len(claim_anchors) < 2:
                continue

            relationship_supported = any(
                all(
                    anchor in source_sentence
                    for anchor in claim_anchors
                )
                for source_sentence in source_sentences
            )

            if not relationship_supported:
                return (
                    "relationship_not_supported_"
                    "in_single_source_statement"
                )

        return None
    def validate_section(
        self,
        *,
        section_summary: SectionSummary,
        chunk_map: dict[int, object],
    ) -> ValidatedSectionSummary:

        raw_summary = str(
            section_summary.summary or ""
        ).strip()

        if (
            not raw_summary
            or raw_summary
            == self.NO_MATERIAL_SUMMARY
        ):
            return ValidatedSectionSummary(
                section_index=section_summary.section_index,
                item_number=section_summary.item_number,
                section_title=section_summary.section_title,
                category=section_summary.category,
                chunk_ids=section_summary.chunk_ids,
                chunk_count=section_summary.chunk_count,
                summary=self.NO_MATERIAL_SUMMARY,
                accepted_claims=(),
                rejected_claims=(),
            )

        candidate_lines = self._candidate_lines(
            raw_summary
        )

        accepted = []
        rejected = []

        allowed_chunk_ids = set(
            section_summary.chunk_ids
        )

        for line in candidate_lines:
            validation = self._validate_claim(
                line=line,
                chunk_map=chunk_map,
                allowed_chunk_ids=allowed_chunk_ids,
            )

            if validation.is_valid:
                accepted.append(
                    validation
                )
            else:
                rejected.append(
                    validation
                )

        if accepted:
            validated_summary = "\n".join(
                self._render_claim(
                    validation
                )
                for validation in accepted
            )
        else:
            validated_summary = (
                self.NO_MATERIAL_SUMMARY
            )

        return ValidatedSectionSummary(
            section_index=section_summary.section_index,
            item_number=section_summary.item_number,
            section_title=section_summary.section_title,
            category=section_summary.category,
            chunk_ids=section_summary.chunk_ids,
            chunk_count=section_summary.chunk_count,
            summary=validated_summary,
            accepted_claims=tuple(
                accepted
            ),
            rejected_claims=tuple(
                rejected
            ),
        )
    def _semantic_entailment(
        self,
        *,
        claim: str,
        source_text: str,
    ) -> bool:
        """
        Ask the generation model one narrow question:

        Does the cited source support the COMPLETE claim?

        The model is not asked to summarize or rewrite.
        """

        prompt = f"""
You are validating ONE factual claim against SEC source text.

Determine whether the COMPLETE claim is directly supported
by the source.

SOURCE
------
{source_text}

CLAIM
-----
{claim}

VALIDATION RULES

Return SUPPORTED only if the source directly supports the
entire claim exactly as stated.

Return UNSUPPORTED if ANY part of the claim:

- is missing from the source
- changes a number
- changes a date
- changes a percentage
- changes a reporting period
- combines unrelated numbers
- combines separate transactions
- combines separate programs
- combines separate authorizations
- combines separate legal proceedings
- combines separate jurisdictions
- infers a relationship the source does not state
- introduces outside knowledge
- exaggerates or materially changes the source meaning

Examples:

Source:
10 shares were purchased.
Another row shows an average price of $50 for 2 shares.

Claim:
10 shares were purchased at an average price of $50.

Result:
UNSUPPORTED

Source:
10 shares were purchased at an average price of $50.

Claim:
10 shares were purchased at an average price of $50.

Result:
SUPPORTED

Return exactly one word:

SUPPORTED

or

UNSUPPORTED
""".strip()

        response = (
            self.generation_service.generate(
                prompt,
                temperature=0.0,
                max_tokens=self.MAX_VALIDATION_TOKENS,
            )
        )

        normalized = (
            str(response or "")
            .strip()
            .upper()
        )

        # Fail closed.
        return normalized == "SUPPORTED"
    def _validate_claim(
        self,
        *,
        line: str,
        chunk_map: dict[int, object],
        allowed_chunk_ids: set[int],
    ) -> ClaimValidation:
        parsed = self._parse_claim(line)

        if parsed is None:
            return ClaimValidation(
                claim=line,
                chunk_ids=(),
                is_valid=False,
                reason="missing_or_invalid_chunk_citation",
            )

        chunk_ids, claim = parsed

        if not chunk_ids:
            return ClaimValidation(
                claim=claim,
                chunk_ids=(),
                is_valid=False,
                reason="missing_chunk_ids",
            )

        for chunk_id in chunk_ids:
            if chunk_id not in allowed_chunk_ids:
                return ClaimValidation(
                    claim=claim,
                    chunk_ids=chunk_ids,
                    is_valid=False,
                    reason="citation_outside_section",
                )

            if chunk_id not in chunk_map:
                return ClaimValidation(
                    claim=claim,
                    chunk_ids=chunk_ids,
                    is_valid=False,
                    reason="unknown_chunk",
                )

        source_text = "\n\n".join(
            str(chunk_map[chunk_id].text or "")
            for chunk_id in chunk_ids
        )

        if not source_text.strip():
            return ClaimValidation(
                claim=claim,
                chunk_ids=chunk_ids,
                is_valid=False,
                reason="empty_source",
            )

        literal_error = self._validate_literals(
            claim=claim,
            source_text=source_text,
        )

        if literal_error:
            return ClaimValidation(
                claim=claim,
                chunk_ids=chunk_ids,
                is_valid=False,
                reason=literal_error,
            )

        type_error = self._validate_disclosure_type(
            claim=claim,
            source_text=source_text,
        )

        if type_error:
            return ClaimValidation(
                claim=claim,
                chunk_ids=chunk_ids,
                is_valid=False,
                reason=type_error,
            )

        relationship_error = self._validate_relationship_proximity(
            claim=claim,
            source_text=source_text,
        )

        if relationship_error:
            return ClaimValidation(
                claim=claim,
                chunk_ids=chunk_ids,
                is_valid=False,
                reason=relationship_error,
            )

        supported = self._semantic_entailment(
            claim=claim,
            source_text=source_text,
        )

        if not supported:
            return ClaimValidation(
                claim=claim,
                chunk_ids=chunk_ids,
                is_valid=False,
                reason="not_supported_by_source",
            )

        return ClaimValidation(
            claim=claim,
            chunk_ids=chunk_ids,
            is_valid=True,
            reason="supported",
        )
    def _validate_literals(
        self,
        *,
        claim: str,
        source_text: str,
    ) -> str | None:
        """
        Conservative deterministic validation.

        Important numeric/date expressions in the claim must
        appear in the cited source text.

        We intentionally do not convert or recompute values.
        XBRL handles financial normalization separately.
        """

        normalized_source = (
            self._normalize_text(
                source_text
            )
        )

        numeric_literals = (
            self.NUMBER_RE.findall(
                claim
            )
        )

        for value in numeric_literals:
            normalized_value = (
                self._normalize_text(
                    value
                )
            )

            if not normalized_value:
                continue

            if (
                normalized_value
                not in normalized_source
            ):
                return (
                    "numeric_literal_not_in_source:"
                    f"{value.strip()}"
                )

        date_literals = (
            self.MONTH_DATE_RE.findall(
                claim
            )
            + self.ISO_DATE_RE.findall(
                claim
            )
        )

        for value in date_literals:
            normalized_value = (
                self._normalize_text(
                    value
                )
            )

            if (
                normalized_value
                not in normalized_source
            ):
                return (
                    "date_literal_not_in_source:"
                    f"{value.strip()}"
                )

        return None

    def _parse_claim(
        self,
        line: str,
    ) -> tuple[
        tuple[int, ...],
        str,
    ] | None:

        line = str(
            line or ""
        ).strip()

        line = line.lstrip(
            "-•* "
        ).strip()

        match = self.CHUNK_PREFIX_RE.match(
            line
        )

        if match is None:
            return None

        labels = match.group(
            "labels"
        )

        claim = (
            match.group(
                "claim"
            )
            .strip()
        )

        ids = tuple(
            dict.fromkeys(
                int(value)
                for value in (
                    self.CHUNK_ID_RE.findall(
                        labels
                    )
                )
            )
        )

        if not claim:
            return None

        return (
            ids,
            claim,
        )

    @staticmethod
    def _candidate_lines(
        summary: str,
    ) -> tuple[str, ...]:

        lines = []

        for raw_line in (
            summary.splitlines()
        ):
            line = str(
                raw_line or ""
            ).strip()

            if not line:
                continue

            lines.append(
                line
            )

        return tuple(
            lines
        )

    @staticmethod
    def _render_claim(
        validation: ClaimValidation,
    ) -> str:

        labels = ", ".join(
            f"CHUNK {chunk_id}"
            for chunk_id
            in validation.chunk_ids
        )

        return (
            f"[{labels}] "
            f"{validation.claim}"
        )
    @staticmethod
    def _normalize_text(
        value: str,
    ) -> str:

        value = str(
            value or ""
        ).lower()

        value = value.replace(
            "\u00a0",
            " ",
        )

        # Normalize equivalent percentage notation only for
        # validation comparison.
        #
        # Example:
        #   source: "16 percent"
        #   claim:  "16%"
        #
        # Both normalize to:
        #   "16 percent"
        #
        # This does NOT calculate or alter the numeric value.
        value = value.replace(
            "%",
            " percent",
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()
        