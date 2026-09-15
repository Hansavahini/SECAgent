import re
from collections import defaultdict
from dataclasses import dataclass

from watcher.models import Company, CompanyAlias


class CompanyResolverError(Exception):
    """Raised when a company cannot be resolved safely."""


@dataclass(frozen=True)
class CompanyResolution:
    ticker: str
    company_name: str
    cik: str
    matched_by: str


class CompanyResolver:
    """
    Deterministically resolves companies using database data.

    Resolution sources:
      1. ticker explicitly supplied by caller
      2. ticker written in the question
      3. active CompanyAlias records
      4. full Company.name
      5. unique prefixes derived from Company.name

    No LLM guessing and no company-specific mappings.
    """

    def __init__(
        self,
        *,
        min_name_match_chars: int = 4,
    ):
        self.min_name_match_chars = max(
            int(min_name_match_chars),
            1,
        )

    def resolve(
        self,
        question: str,
        *,
        ticker: str | None = None,
    ) -> CompanyResolution:

        question = str(question or "").strip()

        if not question:
            raise CompanyResolverError(
                "Question cannot be empty."
            )

        companies = list(
            Company.objects.all().only(
                "id",
                "ticker",
                "cik",
                "name",
            )
        )

        if not companies:
            raise CompanyResolverError(
                "No companies are available in the knowledge base."
            )

        explicit_company = None

        if ticker:
            explicit_company = self._find_ticker(
                companies,
                ticker,
            )

            if explicit_company is None:
                raise CompanyResolverError(
                    f"Unknown company ticker: "
                    f"{str(ticker).strip().upper()}"
                )

        question_matches = self._find_question_matches(
            question,
            companies,
        )

        matched_company_ids = set(
            question_matches.keys()
        )

        if len(matched_company_ids) > 1:
            matched_companies = [
                item["company"]
                for item in question_matches.values()
            ]

            tickers = ", ".join(
                sorted(
                    {
                        company.ticker
                        for company
                        in matched_companies
                    }
                )
            )

            raise CompanyResolverError(
                "The question refers to multiple "
                f"companies: {tickers}"
            )

        if len(matched_company_ids) == 1:
            match = next(
                iter(
                    question_matches.values()
                )
            )

            company = match["company"]

            if (
                explicit_company is not None
                and explicit_company.pk
                != company.pk
            ):
                raise CompanyResolverError(
                    "The supplied ticker conflicts "
                    "with the company mentioned "
                    "in the question."
                )

            return self._build_result(
                company,
                matched_by=match["matched_by"],
            )

        if explicit_company is not None:
            return self._build_result(
                explicit_company,
                matched_by="explicit_ticker",
            )

        raise CompanyResolverError(
            "Could not determine which company "
            "the question refers to."
        )

    def _find_question_matches(
        self,
        question,
        companies,
    ):
        matches = {}

        ticker_map = {
            company.ticker.upper(): company
            for company in companies
            if company.ticker
        }

        # Only uppercase-looking tokens are treated as
        # possible ticker symbols. They must also exist
        # in the Company table.
        ticker_tokens = re.findall(
            r"\$?[A-Z][A-Z0-9.\-]{0,15}",
            question,
        )

        for token in ticker_tokens:
            normalized = (
                token.lstrip("$")
                .upper()
            )

            company = ticker_map.get(
                normalized
            )

            if company is not None:
                self._add_match(
                    matches,
                    company,
                    matched_by="question_ticker",
                    score=100000,
                )

        normalized_question = self._normalize(
            question
        )

        # Optional aliases stored in the database.
        aliases = (
            CompanyAlias.objects
            .filter(is_active=True)
            .select_related("company")
        )

        valid_company_ids = {
            company.pk
            for company in companies
        }

        for alias in aliases:
            if (
                alias.company_id
                not in valid_company_ids
            ):
                continue

            normalized_alias = self._normalize(
                alias.normalized_alias
                or alias.alias
            )

            if (
                normalized_alias
                and self._contains_phrase(
                    normalized_question,
                    normalized_alias,
                )
            ):
                self._add_match(
                    matches,
                    alias.company,
                    matched_by="company_alias",
                    score=90000
                    + len(normalized_alias),
                )

        # Exact/full database company names.
        for company in companies:
            normalized_name = self._normalize(
                company.name
            )

            if (
                normalized_name
                and self._contains_phrase(
                    normalized_question,
                    normalized_name,
                )
            ):
                self._add_match(
                    matches,
                    company,
                    matched_by="company_name",
                    score=80000
                    + len(normalized_name),
                )

        # Build unique prefixes directly from current
        # Company rows.
        #
        # Example:
        # DB name: "Apple Inc."
        #
        # Database-derived prefixes:
        # "apple"
        # "apple inc"
        #
        # A prefix is accepted only when it uniquely
        # identifies one company in the current KB.
        prefix_owners = defaultdict(set)

        for company in companies:
            normalized_name = self._normalize(
                company.name
            )

            if not normalized_name:
                continue

            words = normalized_name.split()

            for length in range(
                1,
                len(words) + 1,
            ):
                prefix = " ".join(
                    words[:length]
                )

                if (
                    len(prefix)
                    < self.min_name_match_chars
                ):
                    continue

                prefix_owners[prefix].add(
                    company.pk
                )

        company_by_id = {
            company.pk: company
            for company in companies
        }

        for prefix, owners in prefix_owners.items():
            if len(owners) != 1:
                continue

            if not self._contains_phrase(
                normalized_question,
                prefix,
            ):
                continue

            company_id = next(
                iter(owners)
            )

            company = company_by_id[
                company_id
            ]

            self._add_match(
                matches,
                company,
                matched_by="unique_company_name",
                score=50000 + len(prefix),
            )

        return matches

    @staticmethod
    def _find_ticker(
        companies,
        ticker,
    ):
        normalized = str(
            ticker or ""
        ).strip().upper()

        for company in companies:
            if (
                str(company.ticker).upper()
                == normalized
            ):
                return company

        return None

    @staticmethod
    def _add_match(
        matches,
        company,
        *,
        matched_by,
        score,
    ):
        current = matches.get(
            company.pk
        )

        if (
            current is None
            or score > current["score"]
        ):
            matches[company.pk] = {
                "company": company,
                "matched_by": matched_by,
                "score": score,
            }

    @staticmethod
    def _normalize(
        value,
    ) -> str:
        value = str(
            value or ""
        ).lower()

        value = re.sub(
            r"[^a-z0-9]+",
            " ",
            value,
        )

        return re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

    @staticmethod
    def _contains_phrase(
        text,
        phrase,
    ) -> bool:
        if not text or not phrase:
            return False

        return (
            f" {phrase} "
            in f" {text} "
        )

    @staticmethod
    def _build_result(
        company,
        *,
        matched_by,
    ) -> CompanyResolution:
        return CompanyResolution(
            ticker=company.ticker,
            company_name=company.name or "",
            cik=company.cik,
            matched_by=matched_by,
        )