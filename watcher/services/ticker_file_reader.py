from pathlib import Path
from typing import List

from django.conf import settings


class TickerFileError(Exception):
    """Raised when the configured ticker file cannot be read."""


class TickerFileReader:
    """Reads and normalizes ticker symbols from the configured ticker file."""

    def __init__(self, file_path: Path | str | None = None):
        self.file_path = Path(file_path or settings.SEC_TICKER_FILE)

    def read(self) -> List[str]:
        if not self.file_path.exists():
            raise TickerFileError(
                f"Ticker file does not exist: {self.file_path}"
            )

        if not self.file_path.is_file():
            raise TickerFileError(
                f"Ticker path is not a file: {self.file_path}"
            )

        try:
            lines = self.file_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise TickerFileError(
                f"Unable to read ticker file: {self.file_path}"
            ) from exc

        tickers = []
        seen = set()

        for line in lines:
            ticker = line.strip().upper()

            if not ticker:
                continue

            if ticker.startswith("#"):
                continue

            if ticker in seen:
                continue

            seen.add(ticker)
            tickers.append(ticker)

        if not tickers:
            raise TickerFileError(
                f"No ticker symbols found in: {self.file_path}"
            )

        return tickers