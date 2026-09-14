from datetime import date

from django.test import TestCase

from watcher.knowledge_base.models import (
    Company,
    Filing,
    FilingChunk,
    FilingDocument,
)
from watcher.knowledge_base.summarization.company_summary_service import (
    CompanySummaryService,
)
from watcher.knowledge_base.summarization.document_summary_service import (
    DocumentSummaryService,
)
from watcher.knowledge_base.summarization.filing_summary_service import (
    FilingSummaryService,
)


class FakeGenerationService:
    def generate(self, prompt, temperature=0.0):
        return "TEST_SUMMARY"


class SummarizationServiceTests(TestCase):
    def setUp(self):
        self.generator = FakeGenerationService()

        self.company = Company.objects.create(
            ticker="TEST",
            cik="0000000001",
            name="Test Company",
        )

        self.filing_8k = Filing.objects.create(
            company=self.company,
            accession_number="0000000001-26-000001",
            sequence=1,
            form="8-K",
            filing_date=date(2026, 1, 10),
            primary_document="test-8k.htm",
            source_url=(
                "https://www.sec.gov/test/test-8k.htm"
            ),
            local_path="C:/test/test-8k.htm",
        )

        self.primary_document = FilingDocument.objects.create(
            filing=self.filing_8k,
            sequence="1",
            document_type="PRIMARY",
            document_name="test-8k.htm",
            source_url=(
                "https://www.sec.gov/test/test-8k.htm"
            ),
            local_path="C:/test/test-8k.htm",
            content_sha256="a" * 64,
            is_primary=True,
        )

        self.exhibit_document = FilingDocument.objects.create(
            filing=self.filing_8k,
            sequence="2",
            document_type="EX-99.1",
            document_name="test-ex991.htm",
            source_url=(
                "https://www.sec.gov/test/test-ex991.htm"
            ),
            local_path="C:/test/test-ex991.htm",
            content_sha256="b" * 64,
            is_primary=False,
        )

        FilingChunk.objects.create(
            filing=self.filing_8k,
            document=self.primary_document,
            chunk_index=0,
            item_number="Item 2.02",
            section_title=(
                "Results of Operations and Financial Condition"
            ),
            text="Primary filing financial results.",
            content_sha256="c" * 64,
        )

        FilingChunk.objects.create(
            filing=self.filing_8k,
            document=self.exhibit_document,
            chunk_index=1,
            item_number="",
            section_title="Press Release",
            text="Exhibit financial results.",
            content_sha256="d" * 64,
        )

        self.filing_10q = Filing.objects.create(
            company=self.company,
            accession_number="0000000001-26-000002",
            sequence=1,
            form="10-Q",
            filing_date=date(2026, 4, 10),
            primary_document="test-10q.htm",
            source_url=(
                "https://www.sec.gov/test/test-10q.htm"
            ),
            local_path="C:/test/test-10q.htm",
        )

        self.document_10q = FilingDocument.objects.create(
            filing=self.filing_10q,
            sequence="1",
            document_type="PRIMARY",
            document_name="test-10q.htm",
            source_url=(
                "https://www.sec.gov/test/test-10q.htm"
            ),
            local_path="C:/test/test-10q.htm",
            content_sha256="e" * 64,
            is_primary=True,
        )

        FilingChunk.objects.create(
            filing=self.filing_10q,
            document=self.document_10q,
            chunk_index=0,
            item_number="Item 2",
            section_title="Management Discussion",
            text="Quarterly operating results.",
            content_sha256="f" * 64,
        )

    def test_document_summary_uses_all_document_chunks(self):
        service = DocumentSummaryService(
            generation_service=self.generator
        )

        result = service.summarize_document(
            self.primary_document.id
        )

        self.assertEqual(result.ticker, "TEST")
        self.assertEqual(result.form, "8-K")
        self.assertEqual(result.chunk_count, 1)
        self.assertEqual(result.summary, "TEST_SUMMARY")
        self.assertEqual(
            result.source_url,
            "https://www.sec.gov/test/test-8k.htm",
        )

    def test_filing_summary_includes_primary_and_exhibit(self):
        service = FilingSummaryService(
            generation_service=self.generator
        )

        result = service.summarize_filing(
            self.filing_8k.id
        )

        self.assertEqual(result.ticker, "TEST")
        self.assertEqual(result.document_count, 2)
        self.assertEqual(result.chunk_count, 2)
        self.assertEqual(len(result.source_urls), 2)
        self.assertEqual(result.summary, "TEST_SUMMARY")

    def test_company_summary_uses_all_filings_and_filters(self):
        service = CompanySummaryService(
            generation_service=self.generator
        )

        result = service.summarize_company("TEST")

        self.assertEqual(result.filing_count, 2)
        self.assertEqual(result.document_count, 3)
        self.assertEqual(result.chunk_count, 3)
        self.assertEqual(result.summary, "TEST_SUMMARY")

        filtered = service.summarize_company(
            "TEST",
            form="10-Q",
        )

        self.assertEqual(filtered.filing_count, 1)
        self.assertEqual(filtered.form_filter, "10-Q")
        self.assertEqual(
            filtered.filings[0].form,
            "10-Q",
        )