from watcher.models import FilingDocument
from watcher.knowledge_base.summarization.section_summary_service import (
    SectionSummaryService,
)
from watcher.knowledge_base.summarization.summary_validator import (
    SummaryValidator,
)


document = FilingDocument.objects.get(
    filing__company__ticker="AAPL",
    filing__accession_number="0000320193-26-000020",
    is_primary=True,
)

chunks = list(
    document.chunks.order_by("chunk_index")
)

section_service = SectionSummaryService()

groups = section_service.build_groups(
    chunks,
    form=document.filing.form,
)

sections = section_service.summarize_groups(
    groups,
    ticker=document.filing.company.ticker,
    form=document.filing.form,
    filing_date=document.filing.filing_date,
    accession_number=document.filing.accession_number,
    document_name=document.document_name,
)

validated = SummaryValidator().validate_sections(
    section_summaries=sections,
    chunks=chunks,
)

for section in validated:
    print()
    print(
        f"--- {section.category} | "
        f"{section.item_number} | "
        f"{section.section_title} ---"
    )

    print("\nVALIDATED:")
    print(section.summary)

    print("\nREJECTED:")

    if not section.rejected_claims:
        print("None")
    else:
        for rejected in section.rejected_claims:
            print(
                f"- {rejected.reason} | "
                f"{rejected.claim}"
            )