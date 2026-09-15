from dataclasses import dataclass
from typing import Any

from watcher.knowledge_base.agents.company_resolver import (
    CompanyResolution,
    CompanyResolver,
    CompanyResolverError,
)
from watcher.knowledge_base.agents.intent_router import (
    IntentRouter,
    KnowledgeIntent,
)
from watcher.knowledge_base.change_detection.change_detection_service import (
    ChangeDetectionService,
)
from watcher.knowledge_base.qa.grounded_qa_service import (
    GroundedQAService,
)
from watcher.knowledge_base.summarization.company_summary_service import (
    CompanySummaryService,
)


class KnowledgeBaseServiceError(Exception):
    """Raised when a knowledge-base request cannot be processed."""


@dataclass(frozen=True)
class KnowledgeBaseResult:
    intent: KnowledgeIntent
    status: str
    result: Any | None
    message: str
    resolved_company: CompanyResolution | None = None


class KnowledgeBaseService:
    """
    Unified internal entry point for SEC knowledge-base requests.

    Flow:
        question
            -> CompanyResolver
            -> IntentRouter
            -> GroundedQAService
               OR CompanySummaryService
               OR ChangeDetectionService

    The caller may supply a ticker explicitly, but it is
    no longer required when the company can be resolved
    from the natural-language question.
    """

    def __init__(
        self,
        *,
        company_resolver=None,
        intent_router=None,
        qa_service=None,
        company_summary_service=None,
        change_detection_service=None,
    ):
        self.company_resolver = (
            company_resolver
            or CompanyResolver()
        )

        self.intent_router = (
            intent_router
            or IntentRouter()
        )

        self.qa_service = (
            qa_service
            or GroundedQAService()
        )

        self.company_summary_service = (
            company_summary_service
            or CompanySummaryService()
        )

        self.change_detection_service = (
            change_detection_service
            or ChangeDetectionService()
        )

    def answer(
        self,
        question: str,
        *,
        ticker: str | None = None,
        form: str | None = None,
        start_date=None,
        end_date=None,
        top_k: int = 6,
    ) -> KnowledgeBaseResult:

        question = str(
            question or ""
        ).strip()

        if not question:
            raise KnowledgeBaseServiceError(
                "Question cannot be empty."
            )

        try:
            company = self.company_resolver.resolve(
                question,
                ticker=ticker,
            )
        except CompanyResolverError as exc:
            raise KnowledgeBaseServiceError(
                str(exc)
            ) from exc

        resolved_ticker = (
            company.ticker
            .strip()
            .upper()
        )

        route = self.intent_router.route(
            question
        )

        effective_form = (
            str(form).strip().upper()
            if form
            else route.detected_form
        )

        if route.intent == KnowledgeIntent.NORMAL_QA:
            qa_result = self.qa_service.answer(
                question,
                ticker=resolved_ticker,
                form=effective_form,
                top_k=top_k,
            )

            return KnowledgeBaseResult(
                intent=route.intent,
                status="completed",
                result=qa_result,
                message=route.reason,
                resolved_company=company,
            )

        if (
            route.intent
            == KnowledgeIntent.COMPANY_SUMMARY
        ):
            summary_result = (
                self.company_summary_service
                .summarize_company(
                    resolved_ticker,
                    form=effective_form,
                    start_date=start_date,
                    end_date=end_date,
                )
            )

            return KnowledgeBaseResult(
                intent=route.intent,
                status="completed",
                result=summary_result,
                message=route.reason,
                resolved_company=company,
            )

        if (
            route.intent
            == KnowledgeIntent.CHANGE_DETECTION
        ):
            if not effective_form:
                raise KnowledgeBaseServiceError(
                    "Change detection requires an SEC form "
                    "such as 8-K, 10-Q, or 10-K."
                )

            change_result = (
                self.change_detection_service
                .compare_latest(
                    resolved_ticker,
                    form=effective_form,
                )
            )

            return KnowledgeBaseResult(
                intent=route.intent,
                status="completed",
                result=change_result,
                message=route.reason,
                resolved_company=company,
            )

        raise KnowledgeBaseServiceError(
            f"Unsupported intent: {route.intent}"
        )