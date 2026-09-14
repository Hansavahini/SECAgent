import hashlib
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from watcher.knowledge_base.ingestion.document_selector import (
    FilingDocumentSelector,
)
from watcher.models import FilingDocument
from watcher.services.filing_downloader import FilingDownloader


class ExhibitDownloadService:
    """
    Discovers, downloads and registers human-readable
    SEC exhibits for one already-registered Filing.

    Existing primary filing documents are not changed.
    """

    def __init__(
        self,
        *,
        downloader=None,
        selector=None,
    ):
        self.downloader = (
            downloader
            or FilingDownloader()
        )

        self.selector = (
            selector
            or FilingDocumentSelector()
        )

    def download_for_filing(
        self,
        filing,
    ):
        base_url, documents = (
            self.downloader.list_documents(
                filing.company.cik,
                filing.accession_number,
            )
        )

        selected = (
            self.selector.select_exhibits(
                documents,
                primary_filename=(
                    filing.primary_document
                ),
            )
        )

        results = []

        for document in selected:
            result = self._download_one(
                filing=filing,
                base_url=base_url,
                document=document,
            )

            results.append(result)

        return results

    def _download_one(
        self,
        *,
        filing,
        base_url,
        document,
    ):
        sequence = str(
            document.get("sequence") or ""
        ).strip()

        document_type = str(
            document.get("type") or ""
        ).strip()

        document_name = str(
            document.get("filename") or ""
        ).strip()

        description = str(
            document.get("description") or ""
        ).strip()

        existing = (
            FilingDocument.objects
            .filter(
                filing=filing,
                sequence=sequence,
                document_name=document_name,
            )
            .first()
        )

        if (
            existing
            and existing.local_path
            and Path(existing.local_path).is_file()
        ):
            return {
                "document": existing,
                "downloaded": False,
                "reason": "already_exists",
            }

        local_filename = (
            self.downloader.build_filename(
                filing.form,
                document_type,
                filing.filing_date,
                document_name,
            )
        )

        download_result = (
            self.downloader.download(
                filing.company.cik,
                filing.accession_number,
                file_type=document_type,
                sequence=sequence,
                local_filename=local_filename,
                document=document,
                base_url=base_url,
                ticker=filing.company.ticker,
                form=filing.form,
            )
        )

        path = Path(
            download_result["path"]
        )

        try:
            content_sha256 = (
                self._sha256(path)
            )

            file_size = (
                path.stat().st_size
            )

            with transaction.atomic():
                filing_document, _ = (
                    FilingDocument.objects
                    .update_or_create(
                        filing=filing,
                        sequence=sequence,
                        document_name=document_name,
                        defaults={
                            "document_type": (
                                document_type
                            ),
                            "description": (
                                description
                            ),
                            "source_url": (
                                download_result["url"]
                            ),
                            "local_path": str(path),
                            "content_sha256": (
                                content_sha256
                            ),
                            "file_size": (
                                file_size
                            ),
                            "is_primary": False,
                            "downloaded_at": (
                                timezone.now()
                            ),
                        },
                    )
                )

        except Exception:
            try:
                path.unlink()
            except OSError:
                pass

            raise

        return {
            "document": filing_document,
            "downloaded": True,
            "reason": "downloaded",
        }

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()

        with Path(path).open("rb") as handle:
            for block in iter(
                lambda: handle.read(
                    1024 * 1024
                ),
                b"",
            ):
                digest.update(block)

        return digest.hexdigest()