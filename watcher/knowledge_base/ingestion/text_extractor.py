from pathlib import Path


class TextExtractionError(Exception):
    """Raised when a filing cannot be converted into text."""


class TextExtractor:
    SUPPORTED_SUFFIXES = {
        ".htm",
        ".html",
        ".txt",
    }

    def extract(self, file_path):
        path = Path(file_path)

        if not path.is_file():
            raise TextExtractionError(
                f"Filing file does not exist: {path}"
            )

        suffix = path.suffix.lower()

        if suffix not in self.SUPPORTED_SUFFIXES:
            raise TextExtractionError(
                f"Unsupported filing type: {suffix or '<none>'}"
            )

        try:
            raw = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise TextExtractionError(
                f"Unable to read filing: {path}"
            ) from exc

        if not raw.strip():
            raise TextExtractionError(
                f"Filing is empty: {path}"
            )

        return raw