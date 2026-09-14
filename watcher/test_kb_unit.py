import json
from unittest.mock import patch

from django.test import (
    Client,
    SimpleTestCase,
    override_settings,
)

from watcher.knowledge_base.agents.intent_router import (
    IntentRouter,
    KnowledgeIntent,
)
from watcher.knowledge_base.agents.knowledge_base_service import (
    KnowledgeBaseResult,
    KnowledgeBaseService,
)
from watcher.knowledge_base.summarization.summary_citation import (
    SummaryCitationError,
    extract_summary_source_ids,
    resolve_summary_citations,
)


class FakeCitation:
    def __init__(self, source_id):
        self.source_id = source_id


class FakeQAResult:
    found = True
    answer = "TEST_ANSWER"
    citations = ()


class FakeQAService:
    def answer(self, question, **kwargs):
        return "QA_OK"


class FakeSummaryService:
    def summarize_company(self, ticker, **kwargs):
        return "SUMMARY_OK"


class FakeChangeService:
    def compare_latest(self, ticker, **kwargs):
        return f"CHANGE_OK_{kwargs['form']}"


class IntentRouterTests(SimpleTestCase):
    def setUp(self):
        self.router = IntentRouter()

    def test_normal_question_routes_to_qa(self):
        result = self.router.route(
            "What were Apples financial results?"
        )

        self.assertEqual(
            result.intent,
            KnowledgeIntent.NORMAL_QA,
        )

        self.assertIsNone(
            result.detected_form
        )

    def test_company_summary_routes_correctly(self):
        result = self.router.route(
            "Summarize all AAPL files"
        )

        self.assertEqual(
            result.intent,
            KnowledgeIntent.COMPANY_SUMMARY,
        )

    def test_change_question_detects_form(self):
        result = self.router.route(
            "What changed in Apples latest 10-Q?"
        )

        self.assertEqual(
            result.intent,
            KnowledgeIntent.CHANGE_DETECTION,
        )

        self.assertEqual(
            result.detected_form,
            "10-Q",
        )


class KnowledgeBaseServiceTests(SimpleTestCase):
    def setUp(self):
        self.service = KnowledgeBaseService(
            qa_service=FakeQAService(),
            company_summary_service=FakeSummaryService(),
            change_detection_service=FakeChangeService(),
        )

    def test_all_three_routes_reach_correct_service(self):
        qa = self.service.answer(
            "What were Apples financial results?",
            ticker="AAPL",
        )

        summary = self.service.answer(
            "Summarize all AAPL files",
            ticker="AAPL",
        )

        change = self.service.answer(
            "What changed in Apples latest 10-Q?",
            ticker="AAPL",
        )

        self.assertEqual(
            qa.result,
            "QA_OK",
        )

        self.assertEqual(
            summary.result,
            "SUMMARY_OK",
        )

        self.assertEqual(
            change.result,
            "CHANGE_OK_10-Q",
        )


class CitationValidationTests(SimpleTestCase):
    def test_extracts_unique_source_ids_in_order(self):
        text = (
            "Fact one [C625]. "
            "Fact two [C700]. "
            "Repeated [C625]."
        )

        self.assertEqual(
            extract_summary_source_ids(text),
            ("C625", "C700"),
        )

    def test_invalid_source_id_is_rejected(self):
        citations = (
            FakeCitation("C625"),
            FakeCitation("C700"),
        )

        with self.assertRaises(
            SummaryCitationError
        ):
            resolve_summary_citations(
                "Unsupported claim [C999999].",
                citations,
            )


@override_settings(
    ALLOWED_HOSTS=["testserver"]
)
class KnowledgeBaseAPITests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def test_missing_question_returns_400(self):
        response = self.client.post(
            "/api/knowledge-base/ask/",
            data=json.dumps(
                {
                    "ticker": "AAPL",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            400,
        )

        self.assertEqual(
            response.json(),
            {
                "error": "question is required.",
            },
        )

    @patch(
        "watcher.knowledge_base.api.views."
        "KnowledgeBaseService"
    )
    def test_normal_qa_http_response(
        self,
        service_class,
    ):
        service = service_class.return_value

        service.answer.return_value = (
            KnowledgeBaseResult(
                intent=KnowledgeIntent.NORMAL_QA,
                status="completed",
                result=FakeQAResult(),
                message="default factual question path",
            )
        )

        response = self.client.post(
            "/api/knowledge-base/ask/",
            data=json.dumps(
                {
                    "question": (
                        "What were Apples "
                        "financial results?"
                    ),
                    "ticker": "AAPL",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        payload = response.json()

        self.assertEqual(
            payload["intent"],
            "normal_qa",
        )

        self.assertEqual(
            payload["status"],
            "completed",
        )

        self.assertEqual(
            payload["result"]["answer"],
            "TEST_ANSWER",
        )