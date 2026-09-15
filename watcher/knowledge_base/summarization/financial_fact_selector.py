from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from watcher.knowledge_base.summarization.financial_fact_extractor import (
    FinancialFact,
)


@dataclass(frozen=True)
class SelectedFinancialFact:
    category: str
    label: str
    concept: str
    value: Decimal
    unit_ref: str
    period_label: str
    period_start: date | None
    period_end: date | None
    instant: date | None
    period_days: int | None
    context_id: str


class FinancialFactSelector:
    """
    Select a small, reliable set of consolidated headline
    financial facts from raw Inline XBRL facts.

    Important:
    - Company-independent.
    - Does not contain ticker-specific rules.
    - Does not choose dimensional segment/product facts for
      company headline metrics.
    - Does not generate prose.
    - Does not ask an LLM to determine reporting periods.
    """

    CATEGORY_CONCEPTS = {
        "revenue": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ),
        "net_income": (
            "NetIncomeLoss",
            "ProfitLoss",
        ),
        "operating_income": (
            "OperatingIncomeLoss",
        ),
        "gross_profit": (
            "GrossProfit",
        ),
        "diluted_eps": (
            "EarningsPerShareDiluted",
        ),
        "basic_eps": (
            "EarningsPerShareBasic",
        ),
        "operating_cash_flow": (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "cash": (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        "assets": (
            "Assets",
        ),
        "liabilities": (
            "Liabilities",
        ),
    }

    CATEGORY_LABELS = {
        "revenue": "Net sales / revenue",
        "net_income": "Net income",
        "operating_income": "Operating income",
        "gross_profit": "Gross profit",
        "diluted_eps": "Diluted EPS",
        "basic_eps": "Basic EPS",
        "operating_cash_flow": "Operating cash flow",
        "cash": "Cash and cash equivalents",
        "assets": "Total assets",
        "liabilities": "Total liabilities",
    }

    QUARTER_RANGE = (70, 110)
    SIX_MONTH_RANGE = (150, 200)
    NINE_MONTH_RANGE = (230, 300)
    ANNUAL_RANGE = (330, 385)

    def select(
        self,
        facts: tuple[FinancialFact, ...]
        | list[FinancialFact],
        *,
        form: str,
    ) -> tuple[SelectedFinancialFact, ...]:

        form = str(form or "").strip().upper()

        usable = [
            fact
            for fact in facts
            if (
                fact.numeric_value is not None
                and not fact.is_dimensional
            )
        ]

        if not usable:
            return ()

        if form == "10-Q":
            return self._select_10q(
                usable
            )

        if form == "10-K":
            return self._select_10k(
                usable
            )

        return self._select_general(
            usable
        )

    def _select_10q(
        self,
        facts: list[FinancialFact],
    ) -> tuple[SelectedFinancialFact, ...]:

        duration_facts = [
            fact
            for fact in facts
            if (
                fact.end_date is not None
                and fact.period_days is not None
            )
        ]

        if not duration_facts:
            return self._select_general(
                facts
            )

        current_period_end = max(
            fact.end_date
            for fact in duration_facts
            if fact.end_date is not None
        )

        selected = []

        for category in (
            "revenue",
            "net_income",
            "operating_income",
            "gross_profit",
            "diluted_eps",
        ):
            category_facts = (
                self._facts_for_category(
                    facts,
                    category,
                )
            )

            selected.extend(
                self._select_current_and_prior_duration(
                    category=category,
                    facts=category_facts,
                    current_period_end=current_period_end,
                    day_range=self.QUARTER_RANGE,
                )
            )

            selected.extend(
                self._select_current_and_prior_duration(
                    category=category,
                    facts=category_facts,
                    current_period_end=current_period_end,
                    day_range=self.NINE_MONTH_RANGE,
                )
            )

        for category in (
            "operating_cash_flow",
        ):
            category_facts = (
                self._facts_for_category(
                    facts,
                    category,
                )
            )

            selected.extend(
                self._select_current_and_prior_duration(
                    category=category,
                    facts=category_facts,
                    current_period_end=current_period_end,
                    day_range=self.NINE_MONTH_RANGE,
                )
            )

        for category in (
            "cash",
            "assets",
            "liabilities",
        ):
            category_facts = (
                self._facts_for_category(
                    facts,
                    category,
                )
            )

            selected.extend(
                self._select_latest_instant(
                    category=category,
                    facts=category_facts,
                    target_date=current_period_end,
                )
            )

        return self._deduplicate(
            selected
        )

    def _select_10k(
        self,
        facts: list[FinancialFact],
    ) -> tuple[SelectedFinancialFact, ...]:

        duration_facts = [
            fact
            for fact in facts
            if (
                fact.end_date is not None
                and fact.period_days is not None
            )
        ]

        if not duration_facts:
            return self._select_general(
                facts
            )

        current_period_end = max(
            fact.end_date
            for fact in duration_facts
            if fact.end_date is not None
        )

        selected = []

        for category in (
            "revenue",
            "net_income",
            "operating_income",
            "gross_profit",
            "diluted_eps",
            "operating_cash_flow",
        ):
            category_facts = (
                self._facts_for_category(
                    facts,
                    category,
                )
            )

            selected.extend(
                self._select_current_and_prior_duration(
                    category=category,
                    facts=category_facts,
                    current_period_end=current_period_end,
                    day_range=self.ANNUAL_RANGE,
                )
            )

        for category in (
            "cash",
            "assets",
            "liabilities",
        ):
            selected.extend(
                self._select_latest_instant(
                    category=category,
                    facts=self._facts_for_category(
                        facts,
                        category,
                    ),
                    target_date=current_period_end,
                )
            )

        return self._deduplicate(
            selected
        )

    def _select_general(
        self,
        facts: list[FinancialFact],
    ) -> tuple[SelectedFinancialFact, ...]:

        selected = []

        for category in self.CATEGORY_CONCEPTS:
            matches = self._facts_for_category(
                facts,
                category,
            )

            if not matches:
                continue

            matches = sorted(
                matches,
                key=self._recency_key,
                reverse=True,
            )

            fact = matches[0]

            selected.append(
                self._to_selected(
                    category,
                    fact,
                )
            )

        return self._deduplicate(
            selected
        )

    def _facts_for_category(
        self,
        facts: list[FinancialFact],
        category: str,
    ) -> list[FinancialFact]:

        accepted_names = (
            self.CATEGORY_CONCEPTS[
                category
            ]
        )

        exact_matches = []

        for fact in facts:
            local_name = (
                self._concept_local_name(
                    fact.concept
                )
            )

            if local_name in accepted_names:
                exact_matches.append(
                    fact
                )

        return exact_matches

    def _select_current_and_prior_duration(
        self,
        *,
        category: str,
        facts: list[FinancialFact],
        current_period_end: date,
        day_range: tuple[int, int],
    ) -> list[SelectedFinancialFact]:

        minimum_days, maximum_days = (
            day_range
        )

        candidates = [
            fact
            for fact in facts
            if (
                fact.end_date is not None
                and fact.period_days is not None
                and minimum_days
                <= fact.period_days
                <= maximum_days
            )
        ]

        if not candidates:
            return []

        current_candidates = [
            fact
            for fact in candidates
            if fact.end_date == current_period_end
        ]

        if not current_candidates:
            return []

        current = self._pick_preferred_fact(
            current_candidates
        )

        if current is None:
            return []

        selected = [
            self._to_selected(
                category,
                current,
            )
        ]

        prior_candidates = [
            fact
            for fact in candidates
            if (
                fact.end_date is not None
                and fact.end_date
                < current_period_end
                and self._is_comparable_period(
                    current,
                    fact,
                )
            )
        ]

        if prior_candidates:
            prior_candidates.sort(
                key=lambda fact: (
                    abs(
                        (
                            current_period_end
                            - fact.end_date
                        ).days
                        - 365
                    ),
                    abs(
                        (
                            current.period_days
                            or 0
                        )
                        - (
                            fact.period_days
                            or 0
                        )
                    ),
                )
            )

            prior = (
                self._pick_preferred_fact(
                    prior_candidates
                )
            )

            if prior is not None:
                selected.append(
                    self._to_selected(
                        category,
                        prior,
                    )
                )

        return selected

    def _select_latest_instant(
        self,
        *,
        category: str,
        facts: list[FinancialFact],
        target_date: date,
    ) -> list[SelectedFinancialFact]:

        candidates = [
            fact
            for fact in facts
            if (
                fact.instant is not None
                and fact.instant
                <= target_date
            )
        ]

        if not candidates:
            return []

        candidates.sort(
            key=lambda fact: fact.instant,
            reverse=True,
        )

        latest_date = (
            candidates[0].instant
        )

        same_date = [
            fact
            for fact in candidates
            if fact.instant == latest_date
        ]

        fact = self._pick_preferred_fact(
            same_date
        )

        if fact is None:
            return []

        return [
            self._to_selected(
                category,
                fact,
            )
        ]

    def _pick_preferred_fact(
        self,
        facts: list[FinancialFact],
    ) -> FinancialFact | None:

        if not facts:
            return None

        return sorted(
            facts,
            key=lambda fact: (
                self._concept_priority(
                    fact.concept
                ),
                fact.context_id,
            ),
        )[0]

    def _concept_priority(
        self,
        concept: str,
    ) -> int:

        local_name = (
            self._concept_local_name(
                concept
            )
        )

        priority = 0

        for names in (
            self.CATEGORY_CONCEPTS.values()
        ):
            if local_name in names:
                return priority

            priority += len(names)

        return 999

    @staticmethod
    def _is_comparable_period(
        current: FinancialFact,
        previous: FinancialFact,
    ) -> bool:

        if (
            current.period_days is None
            or previous.period_days is None
        ):
            return False

        return (
            abs(
                current.period_days
                - previous.period_days
            )
            <= 14
        )

    def _to_selected(
        self,
        category: str,
        fact: FinancialFact,
    ) -> SelectedFinancialFact:

        assert (
            fact.numeric_value
            is not None
        )

        return SelectedFinancialFact(
            category=category,
            label=self.CATEGORY_LABELS[
                category
            ],
            concept=fact.concept,
            value=fact.numeric_value,
            unit_ref=fact.unit_ref,
            period_label=fact.period_label,
            period_start=fact.start_date,
            period_end=fact.end_date,
            instant=fact.instant,
            period_days=fact.period_days,
            context_id=fact.context_id,
        )

    @staticmethod
    def _concept_local_name(
        concept: str,
    ) -> str:

        concept = str(
            concept or ""
        ).strip()

        if ":" in concept:
            return concept.rsplit(
                ":",
                1,
            )[-1]

        return concept

    @staticmethod
    def _recency_key(
        fact: FinancialFact,
    ) -> tuple:

        effective_date = (
            fact.instant
            or fact.end_date
            or date.min
        )

        return (
            effective_date,
            fact.period_days or 0,
        )

    @staticmethod
    def _deduplicate(
        facts: list[
            SelectedFinancialFact
        ],
    ) -> tuple[
        SelectedFinancialFact,
        ...
    ]:

        seen = set()
        result = []

        for fact in facts:
            key = (
                fact.category,
                fact.concept,
                fact.value,
                fact.period_label,
            )

            if key in seen:
                continue

            seen.add(key)
            result.append(fact)

        return tuple(result)