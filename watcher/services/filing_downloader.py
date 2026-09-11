import html
import os
import re
import tempfile
from pathlib import Path

import requests
from django.conf import settings

from .sec_client import SECClient


class FilingDownloader:
    """
    Downloads one specific SEC DOCUMENT identified by
    accession number + sequence.

    Two things this class refuses to guess:

    1. The archive CIK.
       EDGAR archive paths are /Archives/edgar/data/<CIK>/<accession>/.
       The <CIK> can be the subject company OR the CIK encoded in the
       first block of the accession number (the submitter/filer agent,
       e.g. 0001141391-26-000037 -> 1141391). Which one works varies by
       filing, so both are probed and the first that answers 200 wins.

    2. The SEC filename.
       It is read from the filing's own SGML header
       (<TYPE>/<SEQUENCE>/<FILENAME> per <DOCUMENT>), never derived from
       the local display filename.
    """

    ARCHIVE_ROOT = "https://www.sec.gov/Archives/edgar/data"

    # Characters Windows will not accept in a filename.
    _ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

    def __init__(self, client=None):
        self.client = client or SECClient()

        # accession -> (base_url, [document dicts])
        self._filing_cache = {}

    # -----------------------------------------------------------------
    # Archive path resolution
    # -----------------------------------------------------------------

    @staticmethod
    def _accession_clean(accession_number):
        return str(accession_number).replace("-", "").strip()

    @staticmethod
    def _cik_from_accession(accession_number):
        """
        0001141391-26-000037 -> 1141391

        This is the CIK of the entity that submitted the filing, which
        is often a filing agent rather than the subject company.
        """
        head = str(accession_number).strip().split("-")[0]

        if not head.isdigit():
            return None

        return int(head)

    def _candidate_ciks(self, cik, accession_number):
        candidates = []

        for value in (cik, self._cik_from_accession(accession_number)):
            if value in (None, ""):
                continue

            try:
                numeric = int(str(value).strip())
            except (TypeError, ValueError):
                continue

            if numeric not in candidates:
                candidates.append(numeric)

        return candidates

    # -----------------------------------------------------------------
    # Filing header parsing
    # -----------------------------------------------------------------

    @staticmethod
    def _parse_documents(text):
        """
        Parse <DOCUMENT> stanzas out of an EDGAR submission header.

        Tags are unescaped first because some EDGAR header pages serve
        the SGML HTML-escaped inside <pre>.
        """
        unescaped = html.unescape(text)

        documents = []

        for block in re.findall(
            r"<DOCUMENT>(.*?)(?:</DOCUMENT>|\Z)",
            unescaped,
            re.DOTALL | re.IGNORECASE,
        ):

            def field(name):
                match = re.search(
                    rf"<{name}>[ \t]*([^\r\n<]*)",
                    block,
                    re.IGNORECASE,
                )
                return match.group(1).strip() if match else ""

            filename = field("FILENAME")

            if not filename:
                continue

            documents.append(
                {
                    "type": field("TYPE"),
                    "sequence": field("SEQUENCE"),
                    "filename": filename,
                    "description": field("DESCRIPTION"),
                }
            )

        return documents

    def _load_filing(self, cik, accession_number):
        """
        Return (base_url, documents) for an accession, cached.
        """
        accession_number = str(accession_number).strip()

        cached = self._filing_cache.get(accession_number)

        if cached is not None:
            return cached

        accession_clean = self._accession_clean(accession_number)

        attempted = []
        last_error = None

        for candidate in self._candidate_ciks(cik, accession_number):

            base_url = f"{self.ARCHIVE_ROOT}/{candidate}/{accession_clean}"

            header_url = (
                f"{base_url}/{accession_number}-index-headers.html"
            )

            attempted.append(header_url)

            try:
                response = self.client.get(
                    header_url,
                    allow_status=(403, 404),
                )
            except requests.exceptions.RequestException as exc:
                last_error = exc
                continue

            if response.status_code != 200:
                continue

            documents = self._parse_documents(response.text)

            if not documents:
                # Header page reachable but unparsable: fall back to
                # the directory listing so at least the real filenames
                # are known.
                documents = self._documents_from_index_json(base_url)

            if documents:
                self._filing_cache[accession_number] = (base_url, documents)
                return base_url, documents

        # Header pages unavailable under every candidate CIK.
        # Last resort: the JSON directory listing.
        for candidate in self._candidate_ciks(cik, accession_number):

            base_url = f"{self.ARCHIVE_ROOT}/{candidate}/{accession_clean}"

            documents = self._documents_from_index_json(base_url)

            if documents:
                self._filing_cache[accession_number] = (base_url, documents)
                return base_url, documents

        message = (
            f"Could not locate SEC archive directory for accession "
            f"{accession_number} (tried: {', '.join(attempted) or 'none'})"
        )

        if last_error is not None:
            raise ValueError(message) from last_error

        raise ValueError(message)

    def _documents_from_index_json(self, base_url):
        """
        Directory listing fallback. Gives real filenames but no
        sequence/type, so entries are marked with empty metadata.
        """
        try:
            response = self.client.get(
                f"{base_url}/index.json",
                headers={"Accept": "application/json"},
                allow_status=(403, 404),
            )
        except requests.exceptions.RequestException:
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        items = (payload.get("directory") or {}).get("item") or []

        documents = []

        for item in items:
            name = str(item.get("name") or "").strip()

            if not name or name.endswith("/"):
                continue

            documents.append(
                {
                    "type": "",
                    "sequence": "",
                    "filename": name,
                    "description": "",
                }
            )

        return documents

    # -----------------------------------------------------------------
    # Document resolution
    # -----------------------------------------------------------------

    def resolve_document(
        self,
        cik,
        accession_number,
        file_type=None,
        sequence=None,
        hint_filename=None,
    ):
        """
        Resolve one document to (base_url, document dict).

        Matching order:
          1. exact TYPE + SEQUENCE
          2. SEQUENCE alone
          3. TYPE alone, when unambiguous
          4. the filename hint from EDGAR full text search
        """
        base_url, documents = self._load_filing(cik, accession_number)

        wanted_type = str(file_type or "").strip().upper()
        wanted_sequence = str(sequence).strip() if sequence not in (None, "") else ""
        wanted_hint = str(hint_filename or "").strip().lower()

        if wanted_type and wanted_sequence:
            for document in documents:
                if (
                    document["type"].upper() == wanted_type
                    and document["sequence"] == wanted_sequence
                ):
                    return base_url, document

        if wanted_sequence:
            for document in documents:
                if document["sequence"] == wanted_sequence:
                    return base_url, document

        if wanted_type:
            matches = [
                document
                for document in documents
                if document["type"].upper() == wanted_type
            ]

            if len(matches) == 1:
                return base_url, matches[0]

        if wanted_hint:
            for document in documents:
                if document["filename"].lower() == wanted_hint:
                    return base_url, document

        available = ", ".join(
            f"seq={document['sequence'] or '?'}/"
            f"{document['type'] or '?'}/"
            f"{document['filename']}"
            for document in documents
        ) or "none"

        raise ValueError(
            f"Document not found: type={file_type}, sequence={sequence}, "
            f"accession={accession_number}. Available: {available}"
        )

    def get_filename(
        self,
        cik,
        accession_number,
        file_type,
        sequence,
        hint_filename=None,
    ):
        """
        Exact SEC filename for a document. Kept for compatibility with
        the existing test_filename command.
        """
        _, document = self.resolve_document(
            cik,
            accession_number,
            file_type=file_type,
            sequence=sequence,
            hint_filename=hint_filename,
        )

        return document["filename"]

    # -----------------------------------------------------------------
    # Local filename
    # -----------------------------------------------------------------

    @classmethod
    def _sanitize(cls, value):
        cleaned = cls._ILLEGAL_FILENAME_CHARS.sub("-", str(value or "").strip())

        return cleaned.rstrip(". ").strip()

    def build_filename(
        self,
        form,
        file_type,
        filing_date,
        original_filename,
    ):
        """
        Build the local display filename, preserving the SEC extension.

        8-K + EX-99.1 + 2026-01-29
            -> "8-K (Current report)_EX-99.1_2026-01-29.htm"

        8-K + 8-K + 2026-06-02
            -> "8-K (Current report)_8-K_2026-06-02.htm"
        """
        extension = Path(str(original_filename or "")).suffix or ".htm"

        form_text = str(form or "").strip()

        if form_text == "8-K":
            display_form = "8-K (Current report)"
        elif form_text:
            display_form = form_text
        else:
            display_form = "Filing"

        parts = [self._sanitize(display_form)]

        if file_type:
            parts.append(self._sanitize(str(file_type).replace("/", "-")))

        parts.append(self._sanitize(filing_date))

        stem = "_".join(part for part in parts if part)

        return f"{stem}{extension}"

    # -----------------------------------------------------------------
    # Download
    # -----------------------------------------------------------------

    @property
    def download_dir(self):
        base = getattr(
            settings,
            "SEC_DOWNLOAD_DIR",
            Path(settings.BASE_DIR) / "downloads",
        )

        return Path(base) / "filings"

    def _unique_path(self, directory, filename, accession_number):
        """
        Never silently overwrite a different document that happens to
        map to the same display name. On collision the accession number
        is folded into the stem.
        """
        path = directory / filename

        if not path.exists():
            return path

        stem = Path(filename).stem
        extension = Path(filename).suffix

        tagged = directory / f"{stem}_{self._sanitize(accession_number)}{extension}"

        if not tagged.exists():
            return tagged

        counter = 2

        while True:
            candidate = directory / (
                f"{stem}_{self._sanitize(accession_number)}"
                f"_{counter}{extension}"
            )

            if not candidate.exists():
                return candidate

            counter += 1

    def download(
        self,
        cik,
        accession_number,
        file_type=None,
        sequence=None,
        local_filename=None,
        hint_filename=None,
        document=None,
        base_url=None,
    ):
        """
        Download one SEC document and write it under its local display
        name in a single atomic step.

        Returns a dict:
            path            Path of the written file
            local_filename  local display filename
            sec_filename    the actual SEC filename
            url             the SEC URL that was downloaded
            sequence        resolved sequence
            file_type       resolved SEC document type
        """
        if document is None or base_url is None:
            base_url, document = self.resolve_document(
                cik,
                accession_number,
                file_type=file_type,
                sequence=sequence,
                hint_filename=hint_filename,
            )

        sec_filename = document["filename"]

        file_url = f"{base_url}/{sec_filename}"

        response = self.client.get(file_url)

        directory = self.download_dir
        directory.mkdir(parents=True, exist_ok=True)

        target_name = self._sanitize(local_filename) or sec_filename

        target_path = self._unique_path(
            directory,
            target_name,
            accession_number,
        )

        # Write to a temp file in the same directory, then replace.
        # A partial download can never leave a half-written file behind
        # under the final name, so "downloaded" is all-or-nothing.
        handle, temp_name = tempfile.mkstemp(
            dir=str(directory),
            prefix=".sec-",
            suffix=".part",
        )

        try:
            with os.fdopen(handle, "wb") as temp_file:
                temp_file.write(response.content)

            os.replace(temp_name, target_path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

        return {
            "path": target_path,
            "local_filename": target_path.name,
            "sec_filename": sec_filename,
            "url": file_url,
            "sequence": document.get("sequence") or (
                str(sequence) if sequence not in (None, "") else ""
            ),
            "file_type": document.get("type") or (file_type or ""),
        }
