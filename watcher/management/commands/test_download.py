from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ImproperlyConfigured

from watcher.services.ticker_resolver import TickerResolver
from watcher.services.filing_search import FilingSearch
from watcher.services.filing_downloader import FilingDownloader
from watcher.services.download_registry import DownloadRegistry


class Command(BaseCommand):
    help = (
        "Search SEC EDGAR full text search for 8-K filings containing "
        "'Market' and download every matching document."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "identifier",
            nargs="?",
            default=None,
            help="Ticker or CIK. Prompted for when omitted.",
        )

    def handle(self, *args, **options):
        identifier = options.get("identifier")

        if not identifier:
            identifier = input("Enter ticker/CIK: ").strip()

        if not identifier:
            raise CommandError("No ticker or CIK supplied.")

        # -------------------------------------------------------------
        # 1. Resolve ticker / CIK
        # -------------------------------------------------------------
        try:
            company = TickerResolver().resolve(identifier)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc))

        cik = company["cik"]

        self.stdout.write(
            f"Company: {company['name']} | "
            f"Ticker: {company['ticker']} | "
            f"CIK: {cik}"
        )

        # -------------------------------------------------------------
        # 2. SEC full text search
        #    The search itself carries q="Market", so there is NO local
        #    "Market" filter afterwards and nothing gets deleted for
        #    failing a second keyword check.
        # -------------------------------------------------------------
        filings = FilingSearch().search(cik)

        if not filings:
            self.stdout.write("No matching filings found.")
            return

        self.stdout.write(f"SEC search results: {len(filings)}")

        downloader = FilingDownloader()
        registry = DownloadRegistry()

        downloaded = 0
        skipped = 0
        failed = 0

        # -------------------------------------------------------------
        # 3. Process EVERY search result row separately.
        #    Identity is CIK + accession + sequence, so different
        #    sequences inside one accession are downloaded separately.
        # -------------------------------------------------------------
        for filing in filings:

            accession = filing["accession_number"]
            sequence = filing.get("sequence")
            file_type = filing.get("file_type")
            form = filing.get("form")
            filing_date = filing.get("filing_date")
            hint_filename = filing.get("document_filename")

            label = (
                f"{accession} | sequence={sequence} | {file_type}"
            )

            try:
                # Cheap duplicate check first when the search already
                # gave us a sequence.
                if sequence not in (None, "") and registry.is_downloaded(
                    cik, accession, sequence
                ):
                    skipped += 1
                    self.stdout.write(f"- Already downloaded: {label}")
                    continue

                # Resolve the exact SEC document. The filename comes
                # from the filing's own header, not from any local name.
                base_url, document = downloader.resolve_document(
                    cik,
                    accession,
                    file_type=file_type,
                    sequence=sequence,
                    hint_filename=hint_filename,
                )

                resolved_sequence = document.get("sequence") or sequence

                if registry.is_downloaded(cik, accession, resolved_sequence):
                    skipped += 1
                    self.stdout.write(
                        f"- Already downloaded: {accession} | "
                        f"sequence={resolved_sequence} | "
                        f"{file_type or document.get('type')}"
                    )
                    continue

                local_name = downloader.build_filename(
                    form,
                    file_type or document.get("type"),
                    filing_date,
                    document["filename"],
                )

                result = downloader.download(
                    cik,
                    accession,
                    file_type=file_type,
                    sequence=resolved_sequence,
                    local_filename=local_name,
                    document=document,
                    base_url=base_url,
                )

                # Only now is the document genuinely on disk under its
                # final name.
                registry.mark_downloaded(cik, accession, resolved_sequence)

                downloaded += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK Downloaded: {result['local_filename']}"
                    )
                )
                self.stdout.write(
                    f"   SEC source:  {result['sec_filename']}"
                )
                self.stdout.write(
                    f"   SEC URL:     {result['url']}"
                )

            except Exception as exc:
                failed += 1

                self.stdout.write(
                    self.style.ERROR(f"FAILED: {label} | {exc}")
                )

        self.stdout.write("")
        self.stdout.write(
            f"Downloaded: {downloaded} | "
            f"Skipped: {skipped} | "
            f"Failed: {failed}"
        )
        self.stdout.write(
            f"Files: {downloader.download_dir}"
        )
