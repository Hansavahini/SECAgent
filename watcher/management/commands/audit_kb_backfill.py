from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from watcher.models import (
    ChunkEmbedding,
    Company,
    Filing,
    FilingChunk,
    FilingDocument,
)


class Command(BaseCommand):
    help = (
        "Read-only audit of ticker-file companies against "
        "downloaded SEC files and the indexed knowledge base."
    )

    SUPPORTED_EXTENSIONS = {
        ".htm",
        ".html",
        ".txt",
    }

    def handle(self, *args, **options):
        ticker_file = Path(
            settings.SEC_TICKER_FILE
        )

        download_root = Path(
            settings.SEC_DOWNLOAD_DIR
        )

        if not ticker_file.is_file():
            self.stderr.write(
                self.style.ERROR(
                    f"Ticker file not found: {ticker_file}"
                )
            )
            return

        if not download_root.is_dir():
            self.stderr.write(
                self.style.ERROR(
                    f"SEC download directory not found: "
                    f"{download_root}"
                )
            )
            return

        tickers = self._read_tickers(
            ticker_file
        )

        totals = {
            "ready": 0,
            "partial": 0,
            "downloaded_not_indexed": 0,
            "no_local_files": 0,
        }

        self.stdout.write("")
        self.stdout.write(
            "KNOWLEDGE BASE BACKFILL AUDIT"
        )
        self.stdout.write(
            "=" * 95
        )

        for ticker in tickers:
            ticker_dir = (
                download_root / ticker
            )

            local_files = (
                self._count_local_files(
                    ticker_dir
                )
            )

            company_exists = (
                Company.objects
                .filter(ticker=ticker)
                .exists()
            )

            filings = (
                Filing.objects
                .filter(
                    company__ticker=ticker
                )
                .count()
            )

            documents = (
                FilingDocument.objects
                .filter(
                    filing__company__ticker=ticker
                )
                .count()
            )

            chunks = (
                FilingChunk.objects
                .filter(
                    filing__company__ticker=ticker
                )
                .count()
            )

            embeddings = (
                ChunkEmbedding.objects
                .filter(
                    chunk__filing__company__ticker=ticker
                )
                .count()
            )

            status = self._classify(
                company_exists=company_exists,
                local_files=local_files,
                filings=filings,
                documents=documents,
                chunks=chunks,
                embeddings=embeddings,
            )

            totals[status] += 1

            label = {
                "ready": "READY",
                "partial": "PARTIAL",
                "downloaded_not_indexed": (
                    "DOWNLOADED_NOT_INDEXED"
                ),
                "no_local_files": (
                    "NO_LOCAL_FILES"
                ),
            }[status]

            self.stdout.write(
                f"{ticker:<7} "
                f"| {label:<23} "
                f"| files={local_files:<4} "
                f"| filings={filings:<4} "
                f"| docs={documents:<4} "
                f"| chunks={chunks:<5} "
                f"| embeddings={embeddings:<5}"
            )

        self.stdout.write("")
        self.stdout.write(
            "=" * 95
        )
        self.stdout.write(
            "AUDIT SUMMARY"
        )
        self.stdout.write(
            f"Unique tickers: "
            f"{len(tickers)}"
        )
        self.stdout.write(
            f"READY: "
            f"{totals['ready']}"
        )
        self.stdout.write(
            f"PARTIAL: "
            f"{totals['partial']}"
        )
        self.stdout.write(
            "DOWNLOADED_NOT_INDEXED: "
            f"{totals['downloaded_not_indexed']}"
        )
        self.stdout.write(
            "NO_LOCAL_FILES: "
            f"{totals['no_local_files']}"
        )

        total_chunks = (
            FilingChunk.objects.count()
        )

        total_embeddings = (
            ChunkEmbedding.objects.count()
        )

        self.stdout.write("")
        self.stdout.write(
            f"Global chunks: "
            f"{total_chunks}"
        )
        self.stdout.write(
            f"Global embeddings: "
            f"{total_embeddings}"
        )
        self.stdout.write(
            f"Global missing embeddings: "
            f"{max(total_chunks - total_embeddings, 0)}"
        )

    def _read_tickers(
        self,
        ticker_file: Path,
    ):
        seen = set()
        tickers = []

        for raw_line in (
            ticker_file
            .read_text(
                encoding="utf-8"
            )
            .splitlines()
        ):
            ticker = (
                raw_line
                .strip()
                .upper()
            )

            if (
                not ticker
                or ticker.startswith("#")
                or ticker in seen
            ):
                continue

            seen.add(ticker)
            tickers.append(ticker)

        return tickers

    def _count_local_files(
        self,
        ticker_dir: Path,
    ):
        if not ticker_dir.is_dir():
            return 0

        return sum(
            1
            for path in (
                ticker_dir.rglob("*")
            )
            if (
                path.is_file()
                and path.suffix.lower()
                in self.SUPPORTED_EXTENSIONS
            )
        )

    def _classify(
        self,
        *,
        company_exists,
        local_files,
        filings,
        documents,
        chunks,
        embeddings,
    ):
        if (
            company_exists
            and filings > 0
            and documents > 0
            and chunks > 0
            and embeddings == chunks
        ):
            return "ready"

        if (
            company_exists
            or filings > 0
            or documents > 0
            or chunks > 0
            or embeddings > 0
        ):
            return "partial"

        if local_files > 0:
            return (
                "downloaded_not_indexed"
            )

        return "no_local_files"