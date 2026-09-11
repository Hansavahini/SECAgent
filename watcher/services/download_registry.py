import json
from pathlib import Path

from django.conf import settings


class DownloadRegistry:
    """
    Tracks which SEC DOCUMENTS have already been downloaded.

    Identity is CIK + accession number + sequence, never the accession
    number alone. Two sequences inside one accession are two different
    documents and both must be downloaded.
    """

    def __init__(self, path=None):
        if path is not None:
            self.path = Path(path)
        else:
            base = getattr(
                settings,
                "SEC_DOWNLOAD_DIR",
                Path(settings.BASE_DIR) / "downloads",
            )

            self.path = Path(base) / "downloaded_files.json"

        self.path.parent.mkdir(parents=True, exist_ok=True)

        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    @staticmethod
    def make_key(cik, accession_number, sequence):
        cik_text = str(cik).strip()
        accession_text = str(accession_number).strip()

        sequence_text = (
            str(sequence).strip()
            if sequence not in (None, "")
            else ""
        )

        return f"{cik_text}_{accession_text}_{sequence_text}"

    def _load(self):
        try:
            records = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

        if not isinstance(records, list):
            return []

        return [str(record) for record in records]

    def is_downloaded(self, cik, accession_number, sequence):
        return self.make_key(cik, accession_number, sequence) in self._load()

    def mark_downloaded(self, cik, accession_number, sequence):
        """
        Call this ONLY after the document has been downloaded and
        written under its final local name.
        """
        key = self.make_key(cik, accession_number, sequence)

        records = self._load()

        if key in records:
            return

        records.append(key)

        self.path.write_text(
            json.dumps(records, indent=4),
            encoding="utf-8",
        )
