from pathlib import Path


class MarketFilter:
    def contains_market(self, file_path):
        text = Path(file_path).read_text(
            encoding="utf-8",
            errors="ignore"
        )

        return "market" in text.lower()