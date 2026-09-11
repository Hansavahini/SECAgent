from datetime import date

from watcher.services.sec_client import SECClient


class FilingDiscovery:
    """
    Retrieves company filings from the SEC submissions API and filters
    them by filing form and filing date.
    """

    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

    SUPPORTED_FORMS = frozenset({
        "8-K",
        "10-K",
        "10-Q",
    })

    def __init__(self, client=None):
        self.client = client or SECClient()

    def list_filings(
        self,
        cik,
        form,
        start_date=None,
        end_date=None,
    ):
        form = str(form).strip().upper()

        if form not in self.SUPPORTED_FORMS:
            raise ValueError(
                f"Unsupported filing form: {form}. "
                f"Supported forms: {', '.join(sorted(self.SUPPORTED_FORMS))}"
            )

        today = date.today()

        if start_date is None:
            start_date = date(today.year, 1, 1)

        if end_date is None:
            end_date = today

        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")

        normalized_cik = str(cik).strip().zfill(10)

        url = self.SUBMISSIONS_URL.format(cik=normalized_cik)

        data = self.client.get_json(url)

        recent = data.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accession_numbers = recent.get("accessionNumber", [])
        primary_documents = recent.get("primaryDocument", [])
        primary_document_descriptions = recent.get(
            "primaryDocDescription",
            [],
        )

        filings = []

        row_count = min(
            len(forms),
            len(filing_dates),
            len(accession_numbers),
            len(primary_documents),
        )

        for index in range(row_count):
            filing_form = str(forms[index]).strip().upper()

            if filing_form != form:
                continue

            try:
                filing_date = date.fromisoformat(filing_dates[index])
            except (TypeError, ValueError):
                continue

            if not start_date <= filing_date <= end_date:
                continue

            description = (
                primary_document_descriptions[index]
                if index < len(primary_document_descriptions)
                else ""
            )

            filings.append(
                {
                    "cik": normalized_cik,
                    "form": filing_form,
                    "filing_date": filing_date.isoformat(),
                    "accession_number": accession_numbers[index],
                    "primary_document": primary_documents[index],
                    "description": description or "",
                }
            )

        filings.sort(
            key=lambda filing: (
                filing["filing_date"],
                filing["accession_number"],
            ),
            reverse=True,
        )

        return filings