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
       first block of the accession number.

    2. The SEC filename.
       It is read from the filing's own SGML header
       (<TYPE>/<SEQUENCE>/<FILENAME> per <DOCUMENT>), never derived from
       the local display filename.
    """

    ARCHIVE_ROOT = "https://www.sec.gov/Archives/edgar/data"

    _ILLEGAL_FILENAME_CHARS = re.compile(
        r'[<>:"/\\|?*\x00-\x1f]'
    )

    def __init__(self, client=None):
        self.client = client or SECClient()

        # accession -> (base_url, [document dicts])
        self._filing_cache = {}

    # -----------------------------------------------------------------
    # Archive path resolution
    # -----------------------------------------------------------------

    @staticmethod
    def _accession_clean(accession_number):
        return (
            str(accession_number)
            .replace("-", "")
            .strip()
        )

    @staticmethod
    def _cik_from_accession(accession_number):
        """
        Example:
            0001141391-26-000037 -> 1141391
        """

        head = (
            str(accession_number)
            .strip()
            .split("-")[0]
        )

        if not head.isdigit():
            return None

        return int(head)

    def _candidate_ciks(
        self,
        cik,
        accession_number,
    ):
        candidates = []

        for value in (
            cik,
            self._cik_from_accession(
                accession_number
            ),
        ):
            if value in (None, ""):
                continue

            try:
                numeric = int(
                    str(value).strip()
                )

            except (
                TypeError,
                ValueError,
            ):
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
        Parse <DOCUMENT> stanzas from an EDGAR filing header.
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

                return (
                    match.group(1).strip()
                    if match
                    else ""
                )

            filename = field("FILENAME")

            if not filename:
                continue

            documents.append(
                {
                    "type": field("TYPE"),
                    "sequence": field(
                        "SEQUENCE"
                    ),
                    "filename": filename,
                    "description": field(
                        "DESCRIPTION"
                    ),
                }
            )

        return documents

    def _load_filing(
        self,
        cik,
        accession_number,
    ):
        """
        Return:
            (base_url, documents)

        Results are cached by accession number.
        """

        accession_number = str(
            accession_number
        ).strip()

        cached = self._filing_cache.get(
            accession_number
        )

        if cached is not None:
            return cached

        accession_clean = (
            self._accession_clean(
                accession_number
            )
        )

        attempted = []
        last_error = None

        for candidate in self._candidate_ciks(
            cik,
            accession_number,
        ):

            base_url = (
                f"{self.ARCHIVE_ROOT}/"
                f"{candidate}/"
                f"{accession_clean}"
            )

            header_url = (
                f"{base_url}/"
                f"{accession_number}"
                f"-index-headers.html"
            )

            attempted.append(
                header_url
            )

            try:
                response = self.client.get(
                    header_url,
                    allow_status=(
                        403,
                        404,
                    ),
                )

            except (
                requests.exceptions
                .RequestException
            ) as exc:
                last_error = exc
                continue

            if response.status_code != 200:
                continue

            documents = (
                self._parse_documents(
                    response.text
                )
            )

            if not documents:
                documents = (
                    self._documents_from_index_json(
                        base_url
                    )
                )

            if documents:
                self._filing_cache[
                    accession_number
                ] = (
                    base_url,
                    documents,
                )

                return (
                    base_url,
                    documents,
                )

        # Fallback to SEC archive directory JSON.
        for candidate in self._candidate_ciks(
            cik,
            accession_number,
        ):

            base_url = (
                f"{self.ARCHIVE_ROOT}/"
                f"{candidate}/"
                f"{accession_clean}"
            )

            documents = (
                self._documents_from_index_json(
                    base_url
                )
            )

            if documents:
                self._filing_cache[
                    accession_number
                ] = (
                    base_url,
                    documents,
                )

                return (
                    base_url,
                    documents,
                )

        message = (
            "Could not locate SEC archive "
            "directory "
            f"for accession {accession_number} "
            f"(tried: "
            f"{', '.join(attempted) or 'none'})"
        )

        if last_error is not None:
            raise ValueError(
                message
            ) from last_error

        raise ValueError(message)

    def _documents_from_index_json(
        self,
        base_url,
    ):
        """
        Directory listing fallback.

        Gives real filenames when filing headers
        cannot be parsed.
        """

        try:
            response = self.client.get(
                f"{base_url}/index.json",
                headers={
                    "Accept": (
                        "application/json"
                    )
                },
                allow_status=(
                    403,
                    404,
                ),
            )

        except (
            requests.exceptions
            .RequestException
        ):
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()

        except ValueError:
            return []

        items = (
            (
                payload.get(
                    "directory"
                )
                or {}
            )
            .get("item")
            or []
        )

        documents = []

        for item in items:
            name = str(
                item.get("name") or ""
            ).strip()

            if not name:
                continue

            if name.endswith("/"):
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
    # Document discovery
    # -----------------------------------------------------------------

    def list_documents(
        self,
        cik,
        accession_number,
    ):
        """
        Return every SEC document belonging
        to one filing.

        Examples:
            primary 8-K
            EX-99.1
            EX-10.1
            graphics
            XBRL documents
        """

        base_url, documents = (
            self._load_filing(
                cik,
                accession_number,
            )
        )

        return (
            base_url,
            [
                dict(document)
                for document in documents
            ],
        )

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
        Matching order:

        1. TYPE + SEQUENCE
        2. SEQUENCE
        3. TYPE when unique
        4. filename hint
        """

        base_url, documents = (
            self._load_filing(
                cik,
                accession_number,
            )
        )

        wanted_type = str(
            file_type or ""
        ).strip().upper()

        wanted_sequence = (
            str(sequence).strip()
            if sequence not in (
                None,
                "",
            )
            else ""
        )

        wanted_hint = str(
            hint_filename or ""
        ).strip().lower()

        if (
            wanted_type
            and wanted_sequence
        ):
            for document in documents:

                if (
                    document[
                        "type"
                    ].upper()
                    == wanted_type
                    and document[
                        "sequence"
                    ]
                    == wanted_sequence
                ):
                    return (
                        base_url,
                        document,
                    )

        if wanted_sequence:

            for document in documents:

                if (
                    document[
                        "sequence"
                    ]
                    == wanted_sequence
                ):
                    return (
                        base_url,
                        document,
                    )

        if wanted_type:

            matches = [
                document
                for document
                in documents
                if (
                    document[
                        "type"
                    ].upper()
                    == wanted_type
                )
            ]

            if len(matches) == 1:
                return (
                    base_url,
                    matches[0],
                )

        if wanted_hint:

            for document in documents:

                if (
                    document[
                        "filename"
                    ].lower()
                    == wanted_hint
                ):
                    return (
                        base_url,
                        document,
                    )

        available = ", ".join(
            (
                f"seq="
                f"{document['sequence'] or '?'}/"
                f"{document['type'] or '?'}/"
                f"{document['filename']}"
            )
            for document in documents
        ) or "none"

        raise ValueError(
            "Document not found: "
            f"type={file_type}, "
            f"sequence={sequence}, "
            f"accession={accession_number}. "
            f"Available: {available}"
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
        Return the exact SEC filename for a document.
        """

        _, document = (
            self.resolve_document(
                cik,
                accession_number,
                file_type=file_type,
                sequence=sequence,
                hint_filename=(
                    hint_filename
                ),
            )
        )

        return document["filename"]

    # -----------------------------------------------------------------
    # Local filename
    # -----------------------------------------------------------------

    @classmethod
    def _sanitize(
        cls,
        value,
    ):
        cleaned = (
            cls._ILLEGAL_FILENAME_CHARS
            .sub(
                "-",
                str(value or "").strip(),
            )
        )

        return (
            cleaned
            .rstrip(". ")
            .strip()
        )

    def build_filename(
        self,
        form,
        file_type,
        filing_date,
        original_filename,
    ):
        """
        Build a readable local filename.
        """

        extension = (
            Path(
                str(
                    original_filename
                    or ""
                )
            ).suffix
            or ".htm"
        )

        form_text = str(
            form or ""
        ).strip()

        if form_text == "8-K":
            display_form = (
                "8-K (Current report)"
            )

        elif form_text:
            display_form = form_text

        else:
            display_form = "Filing"

        parts = [
            self._sanitize(
                display_form
            )
        ]

        if file_type:
            parts.append(
                self._sanitize(
                    str(
                        file_type
                    ).replace(
                        "/",
                        "-",
                    )
                )
            )

        parts.append(
            self._sanitize(
                filing_date
            )
        )

        stem = "_".join(
            part
            for part in parts
            if part
        )

        return (
            f"{stem}{extension}"
        )

    # -----------------------------------------------------------------
    # Download directory
    # -----------------------------------------------------------------

    @property
    def download_dir(self):
        """
        Existing legacy location.
        """

        base = getattr(
            settings,
            "SEC_DOWNLOAD_DIR",
            Path(
                settings.BASE_DIR
            )
            / "downloads",
        )

        return (
            Path(base)
            / "filings"
        )

    def get_download_dir(
        self,
        ticker=None,
        form=None,
    ):
        """
        Existing callers:
            <SEC_DOWNLOAD_DIR>/filings/

        Production watcher:
            <SEC_DOWNLOAD_DIR>/<TICKER>/<FORM>/
        """

        if not ticker or not form:
            return self.download_dir

        safe_ticker = self._sanitize(
            str(ticker).upper()
        )

        safe_form = self._sanitize(
            str(form).upper()
        )

        base = getattr(
            settings,
            "SEC_DOWNLOAD_DIR",
            Path(
                settings.BASE_DIR
            )
            / "downloads",
        )

        return (
            Path(base)
            / safe_ticker
            / safe_form
        )

    # -----------------------------------------------------------------
    # Duplicate filename protection
    # -----------------------------------------------------------------

    def _unique_path(
        self,
        directory,
        filename,
        accession_number,
    ):
        """
        Never overwrite an existing file.

        If the filename already exists,
        accession number is added.
        """

        path = (
            directory
            / filename
        )

        if not path.exists():
            return path

        stem = (
            Path(filename).stem
        )

        extension = (
            Path(filename).suffix
        )

        tagged = (
            directory
            / (
                f"{stem}_"
                f"{self._sanitize(accession_number)}"
                f"{extension}"
            )
        )

        if not tagged.exists():
            return tagged

        counter = 2

        while True:

            candidate = (
                directory
                / (
                    f"{stem}_"
                    f"{self._sanitize(accession_number)}"
                    f"_{counter}"
                    f"{extension}"
                )
            )

            if not candidate.exists():
                return candidate

            counter += 1

    # -----------------------------------------------------------------
    # Download
    # -----------------------------------------------------------------

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
        ticker=None,
        form=None,
    ):
        """
        Download one SEC document.

        Production folder structure:

            <SEC_DOWNLOAD_DIR>/
                AAPL/
                    8-K/
                    10-K/
                    10-Q/

        Existing callers remain compatible.
        """

        if (
            document is None
            or base_url is None
        ):

            base_url, document = (
                self.resolve_document(
                    cik,
                    accession_number,
                    file_type=file_type,
                    sequence=sequence,
                    hint_filename=(
                        hint_filename
                    ),
                )
            )

        sec_filename = document[
            "filename"
        ]

        file_url = (
            f"{base_url}/"
            f"{sec_filename}"
        )

        response = (
            self.client.get(
                file_url
            )
        )

        directory = (
            self.get_download_dir(
                ticker=ticker,
                form=form,
            )
        )

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        target_name = (
            self._sanitize(
                local_filename
            )
            or sec_filename
        )

        target_path = (
            self._unique_path(
                directory,
                target_name,
                accession_number,
            )
        )

        # Atomic temporary write.
        handle, temp_name = (
            tempfile.mkstemp(
                dir=str(directory),
                prefix=".sec-",
                suffix=".part",
            )
        )

        try:

            with os.fdopen(
                handle,
                "wb",
            ) as temp_file:

                temp_file.write(
                    response.content
                )

            os.replace(
                temp_name,
                target_path,
            )

        except BaseException:

            try:
                os.unlink(
                    temp_name
                )

            except OSError:
                pass

            raise

        return {
            "path": target_path,
            "local_filename": (
                target_path.name
            ),
            "sec_filename": (
                sec_filename
            ),
            "url": file_url,
            "sequence": (
                document.get(
                    "sequence"
                )
                or (
                    str(sequence)
                    if sequence
                    not in (
                        None,
                        "",
                    )
                    else ""
                )
            ),
            "file_type": (
                document.get(
                    "type"
                )
                or (
                    file_type
                    or ""
                )
            ),
        }