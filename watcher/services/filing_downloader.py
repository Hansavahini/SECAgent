import re
from pathlib import Path

from .sec_client import SECClient


class FilingDownloader:
    def __init__(self):
        self.client = SECClient()

    def get_filename(self, cik, accession_number, file_type, sequence):
        accession_clean = accession_number.replace("-", "")

        txt_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession_clean}/"
            f"{accession_number}.txt"
        )

        response = self.client.get(txt_url)
        text = response.text

        documents = re.findall(
            r"<DOCUMENT>(.*?)</DOCUMENT>",
            text,
            re.DOTALL | re.IGNORECASE,
        )

        for document in documents:
            type_match = re.search(
                r"<TYPE>\s*(.+)",
                document,
                re.IGNORECASE,
            )

            sequence_match = re.search(
                r"<SEQUENCE>\s*(.+)",
                document,
                re.IGNORECASE,
            )

            filename_match = re.search(
                r"<FILENAME>\s*(.+)",
                document,
                re.IGNORECASE,
            )

            if not (type_match and sequence_match and filename_match):
                continue

            document_type = type_match.group(1).strip()
            document_sequence = sequence_match.group(1).strip()
            filename = filename_match.group(1).strip()

            if (
                document_type.upper() == str(file_type).upper()
                and document_sequence == str(sequence)
            ):
                return filename

        raise ValueError(
            f"Document not found: "
            f"type={file_type}, sequence={sequence}, "
            f"accession={accession_number}"
        )

    def download(self, cik, accession_number, file_type, sequence):
        accession_clean = accession_number.replace("-", "")

        base_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession_clean}"
        )

        filename = self.get_filename(
            cik,
            accession_number,
            file_type,
            sequence,
        )

        file_url = f"{base_url}/{filename}"

        response = self.client.get(file_url)

        download_dir = Path("downloads/filings")
        download_dir.mkdir(parents=True, exist_ok=True)

        file_path = download_dir / filename
        file_path.write_bytes(response.content)

        return file_path