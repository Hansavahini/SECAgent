from django.db import transaction
from django.utils import timezone

from watcher.knowledge_base.models import Company, Filing, IngestionJob


class FilingRegistrationService:
    """
    Registers a successfully downloaded SEC filing in PostgreSQL.

    This does not perform parsing/chunking.
    It only creates the durable DB record and queues it for ingestion.
    """

    @transaction.atomic
    def register(
        self,
        *,
        ticker,
        cik,
        company_name,
        form,
        accession_number,
        sequence,
        filing_date,
        primary_document,
        local_path,
        source_url,
    ):
        ticker = str(ticker).strip().upper()
        cik = str(cik).strip()
        accession_number = str(accession_number).strip()
        sequence = int(sequence)

        company, _ = Company.objects.update_or_create(
            cik=cik,
            defaults={
                "ticker": ticker,
                "name": str(company_name or "").strip(),
            },
        )

        filing, created = Filing.objects.get_or_create(
            company=company,
            accession_number=accession_number,
            sequence=sequence,
            defaults={
                "form": form,
                "filing_date": filing_date,
                "primary_document": primary_document or "",
                "local_path": str(local_path),
                "source_url": source_url or "",
                "downloaded_at": timezone.now(),
                "ingestion_status": Filing.IngestionStatus.PENDING,
            },
        )

        if not created:
            filing.form = form
            filing.filing_date = filing_date
            filing.primary_document = primary_document or ""
            filing.local_path = str(local_path)
            filing.source_url = source_url or ""

            if filing.downloaded_at is None:
                filing.downloaded_at = timezone.now()

            filing.save(
                update_fields=[
                    "form",
                    "filing_date",
                    "primary_document",
                    "local_path",
                    "source_url",
                    "downloaded_at",
                    "updated_at",
                ]
            )

        IngestionJob.objects.get_or_create(
            filing=filing,
            defaults={
                "status": IngestionJob.Status.PENDING,
            },
        )

        return filing