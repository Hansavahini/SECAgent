from .sec_client import SECClient


class TickerResolver:
    def __init__(self):
        self.client = SECClient()

    def resolve(self, identifier):
        identifier = identifier.strip().upper()

        # CIK entered directly
        if identifier.isdigit():
            cik = identifier.zfill(10)

            url = f"https://data.sec.gov/submissions/CIK{cik}.json"

            response = self.client.get(url)
            data = response.json()

            return {
                "ticker": data["tickers"][0] if data.get("tickers") else "",
                "cik": cik,
                "name": data["name"],
            }

        # SEC ticker mapping
        url = "https://www.sec.gov/files/company_tickers.json"

        response = self.client.get(url)
        companies = response.json()

        for company in companies.values():
            ticker = str(company.get("ticker", "")).strip().upper()

            if ticker == identifier:
                return {
                    "ticker": ticker,
                    "cik": str(company["cik_str"]).zfill(10),
                    "name": company["title"],
                }

        # Temporary fallback for BK
        fallback = {
            "BK": {
                "ticker": "BK",
                "cik": "0001390777",
                "name": "The Bank of New York Mellon Corporation",
            },
        }

        if identifier in fallback:
            return fallback[identifier]

        raise ValueError(f"Company not found: {identifier}")