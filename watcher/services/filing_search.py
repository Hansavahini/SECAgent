from datetime import date
from .sec_client import SECClient


class FilingSearch:
    def __init__(self):
        self.client = SECClient()

    def search(self, cik):
        today = date.today()

        params = {
            "q": "Market",
            "forms": "8-K",
            "startdt": f"{today.year}-01-01",
            "enddt": today.isoformat(),
            "ciks": cik,
        }

        response = self.client.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params,
        )

        data = response.json()

        results = []

        for hit in data["hits"]["hits"]:
            source = hit["_source"]

            print(source)

            results.append({
                "accession_number": source["adsh"],
                "filing_date": source["file_date"],
                "form": source["form"],
                "file_type": source.get("file_type"),
                "sequence": source.get("sequence"),
            })

        return results