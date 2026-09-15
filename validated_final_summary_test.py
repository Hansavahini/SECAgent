from watcher.models import FilingDocument

from watcher.knowledge_base.summarization.financial_fact_extractor import (
    FinancialFactExtractor,
)
from watcher.knowledge_base.summarization.financial_fact_selector import (
    FinancialFactSelector,
)
from watcher.knowledge_base.summarization.section_summary_service import (
    SectionSummaryService,
)
from watcher.knowledge_base.summarization.summary_validator import (
    SummaryValidator,
)
from watcher.knowledge_base.summarization.summary_composer import (
    SummaryComposer,
)


# ---------------------------------------------------------
# 1. LOAD ONE REAL SEC DOCUMENT
# ---------------------------------------------------------

document = FilingDocument.objects.get(
    filing__company__ticker="AAPL",
    filing__accession_number="0000320193-26-000020",
    is_primary=True,
)

filing = document.filing

chunks = list(
    document.chunks.order_by("chunk_index")
)


print()
print("========== DOCUMENT ==========")
print(f"Ticker: {filing.company.ticker}")
print(f"Form: {filing.form}")
print(f"Filing date: {filing.filing_date}")
print(f"Accession: {filing.accession_number}")
print(f"Document: {document.document_name}")
print(f"Local path: {document.local_path}")
print(f"Chunks: {len(chunks)}")
print()


# ---------------------------------------------------------
# 2. EXTRACT XBRL FINANCIAL FACTS
# ---------------------------------------------------------

financial_extractor = FinancialFactExtractor()

raw_financial_facts = financial_extractor.extract_from_path(
    document.local_path
)


# ---------------------------------------------------------
# 3. SELECT TRUSTED CONSOLIDATED FINANCIAL FACTS
# ---------------------------------------------------------

financial_selector = FinancialFactSelector()

selected_financial_facts = financial_selector.select(
    raw_financial_facts,
    form=filing.form,
)


print(
    "Raw XBRL facts:",
    len(raw_financial_facts),
)

print(
    "Selected financial facts:",
    len(selected_financial_facts),
)


# ---------------------------------------------------------
# 4. BUILD SEC SECTION GROUPS
# ---------------------------------------------------------

section_service = SectionSummaryService()

groups = section_service.build_groups(
    chunks,
    form=filing.form,
)


# ---------------------------------------------------------
# 5. GENERATE SOURCE-GROUNDED SECTION SUMMARIES
# ---------------------------------------------------------

section_summaries = section_service.summarize_groups(
    groups,
    ticker=filing.company.ticker,
    form=filing.form,
    filing_date=filing.filing_date,
    accession_number=filing.accession_number,
    document_name=document.document_name,
)


# ---------------------------------------------------------
# 6. VALIDATE EVERY NARRATIVE CLAIM
# ---------------------------------------------------------

validator = SummaryValidator()

validated_sections = validator.validate_sections(
    section_summaries=section_summaries,
    chunks=chunks,
)


# ---------------------------------------------------------
# 7. COUNT ACCEPTED / REJECTED CLAIMS
# ---------------------------------------------------------

# ---------------------------------------------------------
# 7. COUNT ACCEPTED / REJECTED CLAIMS
# ---------------------------------------------------------

accepted_count = 0
rejected_count = 0

for section in validated_sections:
    if section.summary:
        accepted_count += len(
            [
                line
                for line in section.summary.splitlines()
                if line.strip().startswith("[CHUNK ")
            ]
        )

    rejected_count += len(
        section.rejected_claims
    )
# ---------------------------------------------------------
# 8. COMPOSE FINAL DOCUMENT SUMMARY
# ---------------------------------------------------------

composer = SummaryComposer()

final_summary = composer.compose(
    ticker=filing.company.ticker,
    form=filing.form,
    filing_date=filing.filing_date,
    financial_facts=selected_financial_facts,
    section_summaries=validated_sections,
)


# ---------------------------------------------------------
# 9. PRINT RESULT
# ---------------------------------------------------------

print()
print(
    "========== VALIDATED FINAL SUMMARY =========="
)
print()

print(final_summary)

print()
print(
    "============================================="
)

print(
    f"Characters: {len(final_summary)}"
)

print()
print(
    "========== VALIDATION STATS =========="
)

print(
    f"Accepted narrative claims: {accepted_count}"
)

print(
    f"Rejected narrative claims: {rejected_count}"
)