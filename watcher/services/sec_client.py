import os
import requests
from dotenv import load_dotenv

load_dotenv()


class SECClient:
    BASE_URL = "https://data.sec.gov"

    def __init__(self):
        self.headers = {
            "User-Agent": os.getenv("SEC_USER_AGENT"),
            "Accept-Encoding": "gzip, deflate",
        }

    def get(self, url, params=None):
        response = requests.get(
            url,
            headers=self.headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response