from __future__ import annotations

from watcher.knowledge_base.generation.ollama_generation_service import (
    OllamaGenerationService,
)
from watcher.knowledge_base.summarization.section_summary_service import (
    SectionSummary,
)


class DocumentFallbackSummaryService:
    """
    Generic source-grounded fallback for SEC documents that do not
    produce usable section-aware summaries.

    Important:
    - company-independent
    - ticker-independent
    - filename-independent
    - reuses existing FilingChunk rows
    - Python assigns citations; the LLM never chooses chunk IDs
    - no re-download, re-parse, re-chunk or re-embedding
    """

    MAX_BATCH_CHARS = 24000
    MAX_SUMMARY_TOKENS = 420

    NO_MATERIAL_SUMMARY = "NO_MATERIAL_SUMMARY"

    def __init__(
        self,
        *,
        generation_service=None,
    ):
        self.generation_service = (
            generation_service
            or OllamaGenerationService()
        )

    def summarize_document(
        self,
        *,
        document,
        chunks,
    ) -> tuple[SectionSummary, ...]:

        batches = self._build_batches(
            chunks
        )

        if not batches:
            return ()

        filing = document.filing
        results = []

        for batch_index, batch in enumerate(
            batches,
            start=1,
        ):
            source_text = self._source_text(
                batch
            )

            prompt = self._build_prompt(
                form=filing.form,
                filing_date=filing.filing_date,
                document_type=document.document_type,
                source_text=source_text,
                batch_number=batch_index,
                batch_count=len(batches),
            )

            response = (
                self.generation_service.generate(
                    prompt,
                    temperature=0.0,
                    max_tokens=self.MAX_SUMMARY_TOKENS,
                )
            )

            claims = self._clean_model_claims(
                response
            )

            if not claims:
                continue

            # IMPORTANT:
            # The model is NOT trusted to provide citations.
            #
            # Python attaches exactly the chunk IDs actually supplied
            # in this batch.
            chunk_ids = tuple(
                chunk.id
                for chunk in batch
            )

            citation_prefix = (
                "["
                + ", ".join(
                    f"CHUNK {chunk_id}"
                    for chunk_id in chunk_ids
                )
                + "]"
            )

            cited_claims = "\n".join(
                f"{citation_prefix} {claim}"
                for claim in claims
            )

            results.append(
                SectionSummary(
                    section_index=batch_index - 1,
                    item_number="",
                    section_title=(
                        "Document-level material disclosure"
                    ),
                    category="other_material",
                    chunk_ids=chunk_ids,
                    chunk_count=len(batch),
                    summary=cited_claims,
                )
            )

        return tuple(
            results
        )

    def _build_batches(
        self,
        chunks,
    ) -> tuple[tuple, ...]:
        """
        Keep whole FilingChunk rows intact.

        Multiple chunks may be placed in one batch for speed,
        but Python always knows exactly which chunks belong
        to each generated set of claims.
        """

        ordered = sorted(
            list(chunks),
            key=lambda chunk: (
                chunk.chunk_index,
                chunk.id,
            ),
        )

        batches = []
        current = []
        current_chars = 0

        for chunk in ordered:
            text = str(
                chunk.text or ""
            ).strip()

            if not text:
                continue

            estimated_chars = (
                len(text) + 64
            )

            if (
                current
                and current_chars + estimated_chars
                > self.MAX_BATCH_CHARS
            ):
                batches.append(
                    tuple(current)
                )

                current = []
                current_chars = 0

            current.append(
                chunk
            )

            current_chars += (
                estimated_chars
            )

        if current:
            batches.append(
                tuple(current)
            )

        return tuple(
            batches
        )

    @staticmethod
    def _source_text(
        chunks,
    ) -> str:
        """
        Chunk markers are shown to the model only as source boundaries.

        The model is explicitly prohibited from returning them.
        """

        blocks = []

        for chunk in chunks:
            text = str(
                chunk.text or ""
            ).strip()

            if not text:
                continue

            blocks.append(
                (
                    f"SOURCE CHUNK {chunk.id}\n"
                    f"{text}"
                )
            )

        return "\n\n".join(
            blocks
        )

    def _build_prompt(
        self,
        *,
        form,
        filing_date,
        document_type,
        source_text,
        batch_number,
        batch_count,
    ) -> str:

        return f"""
You are extracting material facts from ONE SEC filing document.

Use ONLY the supplied SEC source text.

Form: {form or "Unknown"}
Filing date: {filing_date or "Unknown"}
Document type: {document_type or "Unknown"}
Source batch: {batch_number} of {batch_count}

GOAL

Produce a concise summary of the most material facts explicitly
stated in this source.

Material information may include, when actually present:

- financial results
- revenue
- net income
- earnings per share
- gross margin
- operating income
- cash flow
- liquidity
- balance-sheet developments
- dividends
- share repurchases
- financing
- acquisitions or dispositions
- material agreements
- executive or board changes
- legal or regulatory developments
- cybersecurity matters
- restructuring
- guidance or outlook
- significant operational or business developments

These categories are examples only.
Do not assume any of them exist.

STRICT ACCURACY RULES

1. Use only facts explicitly stated in SOURCE TEXT.

2. Do not use outside knowledge.

3. Do NOT output CHUNK labels, source labels, citations,
   chunk numbers or identifiers. Python will attach citations.

4. One bullet must contain ONE independently verifiable
   material idea.

5. Do not combine separate facts merely because they appear
   near each other.

6. Preserve the exact relationship between a number and
   what that number describes.

Example:

If the source says:

Gross margin was 50.1 percent, including a favorable impact
of approximately 2 percentage points from tariff refunds.

Diluted EPS was $2.02 and included a favorable impact of
$0.11 from tariff refunds.

Correct:
Gross margin was 50.1 percent and included an approximately
2 percentage-point favorable impact from tariff refunds.

Correct:
Diluted EPS was $2.02 and included a $0.11 favorable impact
from tariff refunds.

Incorrect:
Gross margin was 50.1 percent and included a $0.11 favorable
impact from tariff refunds.

7. Preserve numeric values exactly as expressed in the source.
   Do not convert:
      109,417 million -> 109.4 billion
   unless the source itself explicitly states 109.4 billion.

8. Preserve reporting periods exactly.

9. Do not convert:
      quarterly -> annual
      annual -> quarterly
      nine-month/YTD -> quarterly

10. Do not calculate new values.

11. Do not merge different:
      transactions
      periods
      programs
      proceedings
      agreements
      jurisdictions
      authorizations

12. Financial tables may contain material information.
    Extract only important headline facts rather than copying
    the whole table.

13. Ignore:
      contact information
      copyright notices
      generic company descriptions
      standard boilerplate
      routine forward-looking-statement language

14. Maximum 6 bullets.

15. Prefer 3-5 bullets.

16. Each bullet must stand on its own.

17. Output plain factual statements, one per line.

18. Do NOT add bullet symbols such as -, *, or •.

19. Do NOT add headings.

20. If there is genuinely no material information, return exactly:

NO_MATERIAL_SUMMARY

SOURCE TEXT
-----------
{source_text}
""".strip()

    def _clean_model_claims(
        self,
        response,
    ) -> tuple[str, ...]:

        text = str(
            response or ""
        ).strip()

        if not text:
            return ()

        if (
            text.upper()
            == self.NO_MATERIAL_SUMMARY
        ):
            return ()

        claims = []

        for raw_line in text.splitlines():
            line = str(
                raw_line or ""
            ).strip()

            if not line:
                continue

            # Remove model-added Markdown bullets.
            line = line.lstrip(
                "-*• "
            ).strip()

            if not line:
                continue

            if (
                line.upper()
                == self.NO_MATERIAL_SUMMARY
            ):
                continue

            # Defense-in-depth:
            # reject any model line that still attempts to
            # manufacture its own chunk/source citation.
            upper_line = line.upper()

            if (
                upper_line.startswith("[CHUNK")
                or upper_line.startswith("CHUNK ")
                or upper_line.startswith("SOURCE CHUNK")
            ):
                continue

            claims.append(
                line
            )

        # Preserve order while removing exact duplicates.
        return tuple(
            dict.fromkeys(
                claims
            )
        )