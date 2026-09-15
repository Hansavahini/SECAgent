from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class KnowledgeIntent(str, Enum):
    NORMAL_QA = "normal_qa"
    COMPANY_SUMMARY = "company_summary"
    CHANGE_DETECTION = "change_detection"


@dataclass(frozen=True)
class IntentResult:
    intent: KnowledgeIntent
    reason: str
    detected_form: str | None = None


class IntentRouter:
    """
    Deterministic router for SEC knowledge-base questions.

    Responsibilities:
    - determine the high-level request intent
    - normalize SEC form references such as 10K, 10-K and 10 K
    - never resolve companies
    - never retrieve evidence
    - never call the generation model
    """

    _CHANGE_PATTERNS = (
        r"\bwhat\s+changed\b",
        r"\bwhat(?:'s|\s+has)?\s+changed\b",
        r"\bchanges?\b",
        r"\bcompare\b",
        r"\bcomparison\b",
        r"\bdifference(?:s)?\b",
        r"\bdiffer(?:ed|ence|ences)?\b",
        r"\bversus\b",
        r"\bvs\.?\b",
    )

    _SUMMARY_PATTERNS = (
        r"\bsummarize\b",
        r"\bsummarise\b",
        r"\bsummary\b",
        r"\boverview\b",
        r"\brecap\b",
    )

    _FORM_PATTERNS = (
        ("10-K", r"(?<![A-Za-z0-9])10[\s-]*k(?![A-Za-z0-9])"),
        ("10-Q", r"(?<![A-Za-z0-9])10[\s-]*q(?![A-Za-z0-9])"),
        ("8-K", r"(?<![A-Za-z0-9])8[\s-]*k(?![A-Za-z0-9])"),
    )

    def route(self, question: str) -> IntentResult:
        normalized = self._normalize_question(question)
        detected_form = self._detect_form(normalized)

        if self._matches_any(normalized, self._CHANGE_PATTERNS):
            return IntentResult(
                intent=KnowledgeIntent.CHANGE_DETECTION,
                reason="change/comparison language detected",
                detected_form=detected_form,
            )

        if self._matches_any(normalized, self._SUMMARY_PATTERNS):
            return IntentResult(
                intent=KnowledgeIntent.COMPANY_SUMMARY,
                reason="summary language detected",
                detected_form=detected_form,
            )

        return IntentResult(
            intent=KnowledgeIntent.NORMAL_QA,
            reason="default factual question path",
            detected_form=detected_form,
        )

    @staticmethod
    def _normalize_question(question: str) -> str:
        return " ".join(str(question or "").strip().lower().split())

    @classmethod
    def _detect_form(cls, question: str) -> str | None:
        for canonical_form, pattern in cls._FORM_PATTERNS:
            if re.search(pattern, question, flags=re.IGNORECASE):
                return canonical_form
        return None

    @staticmethod
    def _matches_any(question: str, patterns: tuple[str, ...]) -> bool:
        return any(
            re.search(pattern, question, flags=re.IGNORECASE)
            for pattern in patterns
        )