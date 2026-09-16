import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CompanyVerificationResult:
    status: str

    expected_cik: str
    sec_cik: str

    expected_company_name: str
    sec_company_name: str

    expected_ticker: str
    sec_tickers: tuple[str, ...]

    cik_match: bool | None
    name_match: bool | None
    ticker_match: bool | None


class CompanyVerificationService:
    """
    Verify watcher company identity against official SEC
    company metadata.

    CIK is the primary identity check.

    Company name and ticker are secondary diagnostics.
    """

    @staticmethod
    def normalize_cik(value):
        digits = re.sub(
            r"\D",
            "",
            str(value or ""),
        )

        if not digits:
            return ""

        return digits.lstrip("0") or "0"

    @staticmethod
    def normalize_company_name(value):
        text = str(
            value
            or ""
        ).upper()

        text = re.sub(
            r"[^A-Z0-9]+",
            " ",
            text,
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        return text.strip()

    @staticmethod
    def normalize_ticker(value):
        return str(
            value
            or ""
        ).strip().upper()

    @classmethod
    def normalize_tickers(cls, values):
        if not values:
            return ()

        if isinstance(values, str):
            values = [values]

        result = []
        seen = set()

        for value in values:
            ticker = cls.normalize_ticker(
                value
            )

            if not ticker:
                continue

            if ticker in seen:
                continue

            seen.add(ticker)
            result.append(ticker)

        return tuple(result)

    def verify(
        self,
        *,
        expected_cik,
        sec_cik,
        expected_company_name="",
        sec_company_name="",
        expected_ticker="",
        sec_tickers=(),
    ):
        normalized_expected_cik = (
            self.normalize_cik(
                expected_cik
            )
        )

        normalized_sec_cik = (
            self.normalize_cik(
                sec_cik
            )
        )

        normalized_expected_name = (
            self.normalize_company_name(
                expected_company_name
            )
        )

        normalized_sec_name = (
            self.normalize_company_name(
                sec_company_name
            )
        )

        normalized_expected_ticker = (
            self.normalize_ticker(
                expected_ticker
            )
        )

        normalized_sec_tickers = (
            self.normalize_tickers(
                sec_tickers
            )
        )

        cik_match = None

        if (
            normalized_expected_cik
            and normalized_sec_cik
        ):
            cik_match = (
                normalized_expected_cik
                == normalized_sec_cik
            )

        name_match = None

        if (
            normalized_expected_name
            and normalized_sec_name
        ):
            name_match = (
                normalized_expected_name
                == normalized_sec_name
            )

        ticker_match = None

        if (
            normalized_expected_ticker
            and normalized_sec_tickers
        ):
            ticker_match = (
                normalized_expected_ticker
                in normalized_sec_tickers
            )

        # CIK is authoritative for automated identity
        # verification.
        if cik_match is True:
            status = "MATCH"

        elif cik_match is False:
            status = "MISMATCH"

        else:
            status = "UNAVAILABLE"

        return CompanyVerificationResult(
            status=status,
            expected_cik=normalized_expected_cik,
            sec_cik=normalized_sec_cik,
            expected_company_name=str(
                expected_company_name
                or ""
            ).strip(),
            sec_company_name=str(
                sec_company_name
                or ""
            ).strip(),
            expected_ticker=(
                normalized_expected_ticker
            ),
            sec_tickers=(
                normalized_sec_tickers
            ),
            cik_match=cik_match,
            name_match=name_match,
            ticker_match=ticker_match,
        )