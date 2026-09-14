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
    Deterministic router for SEC knowledge-base requests.

    Routes:
        normal factual question -> GroundedQAService
        whole-company summary   -> CompanySummaryService
        change/comparison       -> ChangeDetectionService
    """

    SUMMARY_PATTERNS = (
        r"\bsummarize\s+all\b",
        r"\bsummarise\s+all\b",
        r"\bsummary\s+of\s+all\b",
        r"\bcomplete\s+summary\b",
        r"\boverall\s+summary\b",
        r"\bsummarize\s+everything\b",
        r"\bsummarise\s+everything\b",
        r"\ball\s+(?:the\s+)?filings\b",
        r"\ball\s+(?:the\s+)?files\b",
    )

    CHANGE_PATTERNS = (
        r"\bwhat\s+changed\b",
        r"\bwhat'?s\s+changed\b",
        r"\bwhat\s+is\s+new\b",
        r"\bwhat'?s\s+new\b",
        r"\bchanges?\s+between\b",
        r"\bcompare\b",
        r"\bcomparison\b",
        r"\blatest\s+vs\.?\s+previous\b",
        r"\blatest\s+versus\s+previous\b",
        r"\bdifference(?:s)?\s+between\b",
    )

    FORM_PATTERNS = (
        ("10-Q", r"\b10\s*-\s*q\b"),
        ("10-K", r"\b10\s*-\s*k\b"),
        ("8-K", r"\b8\s*-\s*k\b"),
    )

    def route(
        self,
        question: str,
    ) -> IntentResult:

        question = str(question or "").strip()

        if not question:
            raise ValueError(
                "Question cannot be empty."
            )

        normalized = re.sub(
            r"\s+",
            " ",
            question.lower(),
        )

        detected_form = self.detect_form(
            normalized
        )

        if self._matches(
            normalized,
            self.CHANGE_PATTERNS,
        ):
            return IntentResult(
                intent=KnowledgeIntent.CHANGE_DETECTION,
                reason="change/comparison language detected",
                detected_form=detected_form,
            )

        if self._matches(
            normalized,
            self.SUMMARY_PATTERNS,
        ):
            return IntentResult(
                intent=KnowledgeIntent.COMPANY_SUMMARY,
                reason="whole-company summary language detected",
                detected_form=detected_form,
            )

        return IntentResult(
            intent=KnowledgeIntent.NORMAL_QA,
            reason="default factual question path",
            detected_form=detected_form,
        )

    def detect_form(
        self,
        text: str,
    ) -> str | None:

        normalized = str(text or "").lower()

        for form, pattern in self.FORM_PATTERNS:
            if re.search(pattern, normalized):
                return form

        return None

    @staticmethod
    def _matches(
        text: str,
        patterns,
    ) -> bool:
        return any(
            re.search(pattern, text)
            for pattern in patterns
        )