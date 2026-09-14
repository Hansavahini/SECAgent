from pathlib import Path


class FilingDocumentSelector:
    """
    Decides which SEC filing documents are useful
    for the text knowledge base.

    Included:
        - primary filing documents
        - human-readable SEC exhibits such as:
            EX-99.1
            EX-10.1
            EX-4.1
            EX-31.1
            EX-32.1

    Excluded:
        - XBRL support documents
        - images
        - JSON
        - ZIP
        - JavaScript
        - CSS
    """

    SUPPORTED_TEXT_EXTENSIONS = {
        ".htm",
        ".html",
        ".txt",
    }

    EXCLUDED_TYPES = {
        "GRAPHIC",
        "XML",
        "JSON",
        "ZIP",
    }

    EXCLUDED_TYPE_PREFIXES = (
        "EX-101.",
    )

    def is_supported_document(
        self,
        document,
        *,
        primary_filename=None,
    ):
        document_type = str(
            document.get("type") or ""
        ).strip().upper()

        filename = str(
            document.get("filename") or ""
        ).strip()

        if not filename:
            return False

        extension = (
            Path(filename)
            .suffix
            .lower()
        )

        if extension not in self.SUPPORTED_TEXT_EXTENSIONS:
            return False

        if (
            primary_filename
            and filename.lower()
            == str(primary_filename).strip().lower()
        ):
            return True

        if document_type in self.EXCLUDED_TYPES:
            return False

        if document_type.startswith(
            self.EXCLUDED_TYPE_PREFIXES
        ):
            return False

        if document_type.startswith("EX-"):
            return True

        return False

    def select_exhibits(
        self,
        documents,
        *,
        primary_filename=None,
    ):
        selected = []

        for document in documents:
            if (
                primary_filename
                and str(
                    document.get("filename") or ""
                ).lower()
                == str(primary_filename).lower()
            ):
                continue

            if self.is_supported_document(
                document,
                primary_filename=primary_filename,
            ):
                selected.append(
                    dict(document)
                )

        return selected