import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from watcher.knowledge_base.qa.grounded_qa_service import (
    GroundedQAError,
)
from watcher.knowledge_base.agents.knowledge_base_service import (
    KnowledgeBaseService,
    KnowledgeBaseServiceError,
)
from watcher.knowledge_base.change_detection.change_detection_service import (
    ChangeDetectionError,
    ChangeDetectionResult,
)
from watcher.knowledge_base.summarization.company_summary_service import (
    CompanySummary,
    CompanySummaryError,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitationError,
)


def _citation_to_dict(citation):
    return {
        "source_id": citation.source_id,
        "chunk_id": citation.chunk_id,
        "filing_id": citation.filing_id,
        "document_id": citation.document_id,
        "ticker": citation.ticker,
        "form": citation.form,
        "filing_date": (
            citation.filing_date.isoformat()
            if citation.filing_date
            else None
        ),
        "accession_number": citation.accession_number,
        "document_type": citation.document_type,
        "document_name": citation.document_name,
        "item_number": citation.item_number,
        "section_title": citation.section_title,
        "source_url": citation.source_url,
    }


def _filing_reference_to_dict(filing):
    return {
        "filing_id": filing.filing_id,
        "ticker": filing.ticker,
        "form": filing.form,
        "filing_date": filing.filing_date,
        "accession_number": filing.accession_number,
        "document_count": filing.document_count,
        "chunk_count": filing.chunk_count,
        "source_urls": list(filing.source_urls),
    }


def _serialize_company_summary(result):
    return {
        "ticker": result.ticker,
        "company_name": result.company_name,
        "filing_count": result.filing_count,
        "document_count": result.document_count,
        "chunk_count": result.chunk_count,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "form_filter": result.form_filter,
        "summary": result.summary,
        "citations": [
            _citation_to_dict(citation)
            for citation in result.used_citations
        ],
    }


def _serialize_change_detection(result):
    return {
        "ticker": result.ticker,
        "form": result.form,
        "latest_filing": _filing_reference_to_dict(
            result.latest_filing
        ),
        "previous_filing": _filing_reference_to_dict(
            result.previous_filing
        ),
        "summary": result.summary,
        "citations": [
            _citation_to_dict(citation)
            for citation in result.used_citations
        ],
        "source_urls": list(result.source_urls),
    }


def _serialize_grounded_qa(result):
    citations = []

    for citation in getattr(
        result,
        "citations",
        (),
    ):
        citations.append(
            {
                "source_id": getattr(
                    citation,
                    "source_id",
                    "",
                ),
                "ticker": getattr(
                    citation,
                    "ticker",
                    "",
                ),
                "form": getattr(
                    citation,
                    "form",
                    "",
                ),
                "filing_date": str(
                    getattr(
                        citation,
                        "filing_date",
                        "",
                    )
                    or ""
                ),
                "accession_number": getattr(
                    citation,
                    "accession_number",
                    "",
                ),
                "item_number": getattr(
                    citation,
                    "item_number",
                    "",
                ),
                "section_title": getattr(
                    citation,
                    "section_title",
                    "",
                ),
                "source_url": getattr(
                    citation,
                    "source_url",
                    "",
                ),
            }
        )

    return {
        "found": getattr(
            result,
            "found",
            False,
        ),
        "answer": result.answer,
        "citations": citations,
    }


def _serialize_result(result):
    if result is None:
        return None

    if isinstance(
        result,
        ChangeDetectionResult,
    ):
        return _serialize_change_detection(
            result
        )

    if isinstance(
        result,
        CompanySummary,
    ):
        return _serialize_company_summary(
            result
        )

    if hasattr(
        result,
        "answer",
    ):
        return _serialize_grounded_qa(
            result
        )

    return {
        "value": str(result),
    }


@csrf_exempt
def ask_knowledge_base(request):
    if request.method != "POST":
        return JsonResponse(
            {
                "error": "Method not allowed.",
            },
            status=405,
        )

    try:
        payload = json.loads(
            request.body.decode("utf-8")
            or "{}"
        )
    except json.JSONDecodeError:
        return JsonResponse(
            {
                "error": "Request body must be valid JSON.",
            },
            status=400,
        )

    question = str(
        payload.get("question") or ""
    ).strip()

    ticker = str(
        payload.get("ticker") or ""
    ).strip().upper()

    form = payload.get("form")
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")

    try:
        top_k = int(
            payload.get(
                "top_k",
                6,
            )
        )
    except (TypeError, ValueError):
        return JsonResponse(
            {
                "error": "top_k must be an integer.",
            },
            status=400,
        )

    if not question:
        return JsonResponse(
            {
                "error": "question is required.",
            },
            status=400,
        )

    if not ticker:
        return JsonResponse(
            {
                "error": "ticker is required.",
            },
            status=400,
        )

    if top_k < 1 or top_k > 50:
        return JsonResponse(
            {
                "error": (
                    "top_k must be between 1 and 50."
                ),
            },
            status=400,
        )

    try:
        result = KnowledgeBaseService().answer(
            question,
            ticker=ticker,
            form=form,
            start_date=start_date,
            end_date=end_date,
            top_k=top_k,
        )

    except (
        KnowledgeBaseServiceError,
        CompanySummaryError,
        ChangeDetectionError,
        SummaryCitationError,
        GroundedQAError,
        ValueError,
    ) as exc:
        return JsonResponse(
            {
                "error": str(exc),
            },
            status=400,
        )

    return JsonResponse(
        {
            "intent": result.intent.value,
            "status": result.status,
            "message": result.message,
            "result": _serialize_result(
                result.result
            ),
        },
        status=200,
    )