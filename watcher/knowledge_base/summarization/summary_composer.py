from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable

from watcher.knowledge_base.summarization.financial_fact_selector import (
    SelectedFinancialFact,
)
from watcher.knowledge_base.summarization.section_summary_service import (
    SectionSummary,
)


class SummaryComposer:
    """
    Deterministically combines trusted financial facts and
    narrative SEC-section summaries.

    Important:
    - No LLM is used here.
    - Financial values are never rewritten by a model.
    - Reporting-period labels come directly from XBRL.
    - Company-independent.
    - Form-aware for 10-Q, 10-K and 8-K.
    - Produces a summary, not a reproduction of the filing.
    """

    TEN_Q_FINANCIAL_CATEGORIES = (
        "revenue",
        "net_income",
        "operating_income",
        "diluted_eps",
        "operating_cash_flow",
        "cash",
    )

    TEN_K_FINANCIAL_CATEGORIES = (
        "revenue",
        "net_income",
        "operating_income",
        "diluted_eps",
        "operating_cash_flow",
        "cash",
        "assets",
        "liabilities",
    )

    SECTION_HEADINGS = {
        "md&a": "Operating and MD&A Highlights",
        "liquidity_capital": "Liquidity and Capital",
        "capital_activity": "Liquidity and Capital",
        "legal": "Legal, Regulatory and Risk",
        "risk": "Legal, Regulatory and Risk",
        "market_risk": "Legal, Regulatory and Risk",
        "controls": "Controls and Other Material Matters",
        "other_material": "Controls and Other Material Matters",
        "8k_event": "Material Events",
        "other": "Other Material Matters",
    }

    SECTION_ORDER = (
        "Operating and MD&A Highlights",
        "Liquidity and Capital",
        "Legal, Regulatory and Risk",
        "Controls and Other Material Matters",
        "Material Events",
        "Other Material Matters",
    )

    NO_MATERIAL_SUMMARY = "NO_MATERIAL_SUMMARY"

    def compose(
        self,
        *,
        ticker: str,
        form: str,
        filing_date,
        financial_facts: Iterable[SelectedFinancialFact],
        section_summaries: Iterable[SectionSummary],
    ) -> str:

        normalized_form = (
            str(form or "")
            .strip()
            .upper()
        )

        parts: list[str] = []

        header = self._build_header(
            ticker=ticker,
            form=normalized_form,
            filing_date=filing_date,
        )

        parts.append(header)

        facts = tuple(
            financial_facts or ()
        )

        sections = tuple(
            section_summaries or ()
        )

        financial_block = (
            self._build_financial_block(
                form=normalized_form,
                facts=facts,
            )
        )

        if financial_block:
            parts.append(
                financial_block
            )

        narrative_blocks = (
            self._build_narrative_blocks(
                form=normalized_form,
                section_summaries=sections,
            )
        )

        parts.extend(
            narrative_blocks
        )

        if len(parts) == 1:
            parts.append(
                "No material summary information was identified."
            )

        return "\n\n".join(
            part.strip()
            for part in parts
            if part and part.strip()
        ).strip()

    def _build_header(
        self,
        *,
        ticker: str,
        form: str,
        filing_date,
    ) -> str:

        ticker = (
            str(ticker or "")
            .strip()
            .upper()
        )

        form = (
            str(form or "")
            .strip()
            .upper()
        )

        display_date = (
            self._date_string(
                filing_date
            )
        )

        pieces = [
            value
            for value in (
                ticker,
                form,
            )
            if value
        ]

        title = " — ".join(
            pieces
        )

        if not title:
            title = "SEC Filing Summary"

        if display_date:
            return (
                f"{title}\n"
                f"Filed: {display_date}"
            )

        return title

    def _build_financial_block(
        self,
        *,
        form: str,
        facts: tuple[SelectedFinancialFact, ...],
    ) -> str:

        # 8-Ks are fundamentally event-driven.
        #
        # Do not manufacture an XBRL headline-financial section
        # from cover-page facts. Earnings-release exhibits remain
        # available through narrative/document summarization.
        if form == "8-K":
            return ""

        if form == "10-Q":
            allowed_categories = (
                self.TEN_Q_FINANCIAL_CATEGORIES
            )

        elif form == "10-K":
            allowed_categories = (
                self.TEN_K_FINANCIAL_CATEGORIES
            )

        else:
            return ""

        facts_by_category = defaultdict(
            list
        )

        for fact in facts:
            if (
                fact.category
                not in allowed_categories
            ):
                continue

            facts_by_category[
                fact.category
            ].append(
                fact
            )

        lines = []

        for category in allowed_categories:
            category_facts = (
                facts_by_category.get(
                    category,
                    [],
                )
            )

            if not category_facts:
                continue

            lines.extend(
                self._financial_lines_for_category(
                    category=category,
                    facts=category_facts,
                )
            )

        if not lines:
            return ""

        return (
            "Financial Highlights\n"
            + "\n".join(lines)
        )

    def _financial_lines_for_category(
        self,
        *,
        category: str,
        facts: list[SelectedFinancialFact],
    ) -> list[str]:

        duration_facts = [
            fact
            for fact in facts
            if (
                fact.period_end is not None
                and fact.period_days is not None
            )
        ]

        instant_facts = [
            fact
            for fact in facts
            if fact.instant is not None
        ]

        lines = []

        if duration_facts:
            duration_groups = defaultdict(
                list
            )

            for fact in duration_facts:
                period_bucket = (
                    self._duration_bucket(
                        fact.period_days
                    )
                )

                duration_groups[
                    period_bucket
                ].append(
                    fact
                )

            for period_bucket in (
                "quarter",
                "six_month",
                "nine_month",
                "annual",
                "other",
            ):
                period_facts = (
                    duration_groups.get(
                        period_bucket,
                        [],
                    )
                )

                if not period_facts:
                    continue

                line = (
                    self._format_duration_comparison(
                        category=category,
                        facts=period_facts,
                    )
                )

                if line:
                    lines.append(
                        line
                    )

        if instant_facts:
            instant_facts.sort(
                key=lambda fact: (
                    fact.instant
                    or date.min
                ),
                reverse=True,
            )

            latest = instant_facts[0]

            lines.append(
                (
                    f"• {latest.label}: "
                    f"{self._format_value(latest)} "
                    f"({latest.period_label})."
                )
            )

        return lines

    def _format_duration_comparison(
        self,
        *,
        category: str,
        facts: list[SelectedFinancialFact],
    ) -> str:

        ordered = sorted(
            facts,
            key=lambda fact: (
                fact.period_end
                or date.min
            ),
            reverse=True,
        )

        if not ordered:
            return ""

        current = ordered[0]

        prior = self._find_prior_comparable(
            current=current,
            candidates=ordered[1:],
        )

        current_value = (
            self._format_value(
                current
            )
        )

        if prior is None:
            return (
                f"• {current.label}: "
                f"{current_value} "
                f"({current.period_label})."
            )

        prior_value = (
            self._format_value(
                prior
            )
        )

        return (
            f"• {current.label}: "
            f"{current_value} "
            f"for the {current.period_label}; "
            f"{prior_value} "
            f"for the {prior.period_label}."
        )

    @staticmethod
    def _find_prior_comparable(
        *,
        current: SelectedFinancialFact,
        candidates: list[SelectedFinancialFact],
    ) -> SelectedFinancialFact | None:

        if (
            current.period_end is None
            or current.period_days is None
        ):
            return None

        compatible = [
            fact
            for fact in candidates
            if (
                fact.period_end is not None
                and fact.period_days is not None
                and fact.period_end
                < current.period_end
                and abs(
                    fact.period_days
                    - current.period_days
                )
                <= 14
            )
        ]

        if not compatible:
            return None

        compatible.sort(
            key=lambda fact: (
                abs(
                    (
                        current.period_end
                        - fact.period_end
                    ).days
                    - 365
                ),
                abs(
                    current.period_days
                    - fact.period_days
                ),
            )
        )

        return compatible[0]

    def _build_narrative_blocks(
        self,
        *,
        form: str,
        section_summaries: tuple[SectionSummary, ...],
    ) -> list[str]:

        grouped = defaultdict(
            list
        )

        for section in section_summaries:
            summary = (
                str(
                    section.summary
                    or ""
                )
                .strip()
            )

            if not summary:
                continue

            if (
                summary
                == self.NO_MATERIAL_SUMMARY
            ):
                continue

            heading = (
                self.SECTION_HEADINGS.get(
                    section.category,
                    "Other Material Matters",
                )
            )

            if form == "8-K":
                if section.category in {
                    "8k_event",
                    "other",
                    "other_material",
                    "capital_activity",
                    "legal",
                }:
                    heading = (
                        "Material Events"
                    )

            cleaned = (
                self._clean_section_summary(
                    summary
                )
            )

            if cleaned:
                grouped[
                    heading
                ].append(
                    cleaned
                )

        blocks = []

        for heading in self.SECTION_ORDER:
            summaries = grouped.get(
                heading,
                [],
            )

            if not summaries:
                continue

            unique_summaries = list(
                dict.fromkeys(
                    summaries
                )
            )

            blocks.append(
                (
                    f"{heading}\n"
                    + "\n".join(
                        unique_summaries
                    )
                )
            )

        return blocks

    @staticmethod
    def _clean_section_summary(
        summary: str,
    ) -> str:

        lines = []

        for raw_line in (
            summary.splitlines()
        ):
            line = (
                raw_line.strip()
            )

            if not line:
                continue

            # Normalize Markdown bullet variants without
            # rewriting the factual content.
            if line.startswith(
                ("-", "*")
            ):
                line = (
                    line[1:]
                    .strip()
                )

            # Remove Markdown bold markers only.
            line = line.replace(
                "**",
                "",
            )

            if not line:
                continue

            if not line.startswith(
                "•"
            ):
                line = (
                    f"• {line}"
                )

            lines.append(
                line
            )

        return "\n".join(
            lines
        )

    @staticmethod
    def _duration_bucket(
        period_days: int | None,
    ) -> str:

        if period_days is None:
            return "other"

        if 70 <= period_days <= 110:
            return "quarter"

        if 150 <= period_days <= 200:
            return "six_month"

        if 230 <= period_days <= 300:
            return "nine_month"

        if 330 <= period_days <= 385:
            return "annual"

        return "other"

    def _format_value(
        self,
        fact: SelectedFinancialFact,
    ) -> str:

        value = fact.value

        if fact.category in {
            "diluted_eps",
            "basic_eps",
        }:
            return (
                f"${self._format_decimal(value)}"
            )

        unit = (
            str(
                fact.unit_ref
                or ""
            )
            .strip()
            .lower()
        )

        if (
            "usd" in unit
            or fact.category
            in {
                "revenue",
                "net_income",
                "operating_income",
                "gross_profit",
                "operating_cash_flow",
                "cash",
                "assets",
                "liabilities",
            }
        ):
            return self._format_currency(
                value
            )

        return self._format_decimal(
            value
        )

    @staticmethod
    def _format_currency(
        value: Decimal,
    ) -> str:

        absolute = abs(
            value
        )

        trillion = Decimal(
            "1000000000000"
        )

        billion = Decimal(
            "1000000000"
        )

        million = Decimal(
            "1000000"
        )

        if absolute >= trillion:
            scaled = (
                value / trillion
            )

            return (
                f"${SummaryComposer._format_decimal(scaled)}T"
            )

        if absolute >= billion:
            scaled = (
                value / billion
            )

            return (
                f"${SummaryComposer._format_decimal(scaled)}B"
            )

        if absolute >= million:
            scaled = (
                value / million
            )

            return (
                f"${SummaryComposer._format_decimal(scaled)}M"
            )

        return (
            "$"
            + SummaryComposer._format_decimal(
                value
            )
        )

    @staticmethod
    def _format_decimal(
        value: Decimal,
    ) -> str:

        normalized = format(
            value,
            "f",
        )

        if "." in normalized:
            normalized = (
                normalized
                .rstrip("0")
                .rstrip(".")
            )

        return normalized

    @staticmethod
    def _date_string(
        value,
    ) -> str:

        if value is None:
            return ""

        if hasattr(
            value,
            "isoformat",
        ):
            return str(
                value.isoformat()
            )

        return str(
            value
        ).strip()