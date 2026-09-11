from datetime import date

from django.conf import settings

from .sec_client import SECClient


class FilingSearch:
    """
    EDGAR full text search.

    The search itself performs the "Market" keyword match, so callers
    must NOT re-filter downloaded documents for the word locally.

    One row of the result set == one SEC DOCUMENT, not one filing.
    A single accession can therefore appear several times with
    different sequences (for example sequence 1 = 8-K and
    sequence 2 = EX-99.1). Both are returned and both are downloaded.
    """

    SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

    # EDGAR full text search returns 10 hits per page.
    PAGE_SIZE = 10

    def __init__(self, client=None):
        self.client = client or SECClient()

    def search(self, cik, query="Market", forms="8-K", start_date=None, end_date=None):
        today = date.today()

        start_date = start_date or date(today.year, 1, 1)
        end_date = end_date or today

        cik = str(cik).strip().zfill(10)

        max_results = int(
            getattr(settings, "SEC_SEARCH_MAX_RESULTS", 200)
        )

        results = []
        offset = 0
        total = None

        while True:
            params = {
                "q": query,
                "forms": forms,
                "dateRange": "custom",
                "startdt": start_date.isoformat(),
                "enddt": end_date.isoformat(),
                "ciks": cik,
                "from": offset,
            }

            payload = self.client.get_json(
                self.SEARCH_URL,
                params=params,
            )

            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(
                    f"EDGAR full text search error: {payload['error']}"
                )

            hits_block = (payload or {}).get("hits") or {}
            hits = hits_block.get("hits") or []

            if total is None:
                total_block = hits_block.get("total")

                if isinstance(total_block, dict):
                    total = total_block.get("value")
                elif isinstance(total_block, int):
                    total = total_block

            if not hits:
                break

            for hit in hits:
                parsed = self._parse_hit(hit, cik)

                if parsed is not None:
                    results.append(parsed)

            offset += self.PAGE_SIZE

            if offset >= max_results:
                break

            if total is not None and offset >= total:
                break

        return results

    # -----------------------------------------------------------------
    # Parsing
    # -----------------------------------------------------------------

    def _parse_hit(self, hit, requested_cik):
        source = hit.get("_source") or {}

        # _id looks like "0001141391-26-000037:ex991.htm"
        # The part after the colon is the real SEC filename for THIS
        # document. It is used as a verification hint only - the
        # downloader still confirms it against the filing header.
        hit_id = str(hit.get("_id") or "")

        id_accession, _, id_filename = hit_id.partition(":")

        accession_number = (
            str(source.get("adsh") or id_accession or "").strip()
        )

        if not accession_number:
            return None

        document_filename = id_filename.strip().split("/")[-1] or None

        root_forms = source.get("root_forms") or []

        if isinstance(root_forms, str):
            root_forms = [root_forms]

        form = (
            source.get("form")
            or (root_forms[0] if root_forms else None)
            or source.get("file_type")
            or ""
        )

        sequence = source.get("sequence")

        if sequence is not None:
            sequence = str(sequence).strip() or None

        ciks = source.get("ciks") or []

        if isinstance(ciks, str):
            ciks = [ciks]

        return {
            "accession_number": accession_number,
            "filing_date": source.get("file_date"),
            "file_date": source.get("file_date"),
            "form": str(form).strip(),
            "file_type": (
                str(source.get("file_type")).strip()
                if source.get("file_type")
                else None
            ),
            "sequence": sequence,
            "document_filename": document_filename,
            "description": source.get("file_description"),
            "cik": requested_cik,
            "ciks": [str(value).strip().zfill(10) for value in ciks],
        }
