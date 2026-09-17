from django.db import transaction
from django.utils import timezone

from watcher.knowledge_base.models import (
    Company,
    Filing,
    IngestionJob,
)


class FilingRegistrationService:
    """
    Registers a successfully downloaded SEC filing in PostgreSQL.

    This does not perform parsing/chunking.
    It only creates the durable DB record and queues it for ingestion.

    accepted_at is optional so all existing callers remain compatible.
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
        accepted_at=None,
        entry_session=None,
    ):
        ticker = str(
            ticker
        ).strip().upper()

        cik = str(
            cik
        ).strip()

        accession_number = str(
            accession_number
        ).strip()

        sequence = int(
            sequence
        )

        company, _ = (
            Company.objects.update_or_create(
                cik=cik,
                defaults={
                    "ticker": ticker,
                    "name": str(
                        company_name
                        or ""
                    ).strip(),
                },
            )
        )

        # ---------------------------------------------------------
        # Preserve all existing creation behavior.
        #
        # accepted_at is additive only.
        # If no EDGAR acceptance timestamp was supplied, it remains
        # NULL just as it did previously.
        # ---------------------------------------------------------
        filing, created = (
            Filing.objects.get_or_create(
                company=company,
                accession_number=(
                    accession_number
                ),
                sequence=sequence,
                defaults={
                    "form": form,
                    "filing_date": filing_date,
                    "accepted_at": accepted_at,
                    "entry_session": entry_session,
                    "primary_document": (
                        primary_document
                        or ""
                    ),
                    "local_path": str(
                        local_path
                    ),
                    "source_url": (
                        source_url
                        or ""
                    ),
                    "downloaded_at": (
                        timezone.now()
                    ),
                    "ingestion_status": (
                        Filing
                        .IngestionStatus
                        .PENDING
                    ),
                },
            )
        )

        if not created:
            filing.form = form

            filing.filing_date = (
                filing_date
            )

            filing.primary_document = (
                primary_document
                or ""
            )

            filing.local_path = str(
                local_path
            )

            filing.source_url = (
                source_url
                or ""
            )

            # -----------------------------------------------------
            # IMPORTANT:
            # Never erase an existing accepted_at value simply
            # because an old caller did not provide the new field.
            #
            # Only update accepted_at when a real value is supplied.
            # -----------------------------------------------------
            update_fields = [
                "form",
                "filing_date",
                "primary_document",
                "local_path",
                "source_url",
            ]

            if accepted_at is not None:
                filing.accepted_at = (
                    accepted_at
                )

                update_fields.append(
                    "accepted_at"
                )
            if entry_session is not None:
                filing.entry_session = (
                    entry_session
                )

                update_fields.append(
                    "entry_session"
                )
            if filing.downloaded_at is None:
                filing.downloaded_at = (
                    timezone.now()
                )

                update_fields.append(
                    "downloaded_at"
                )

            # auto_now fields need to be explicitly included when
            # update_fields is supplied.
            update_fields.append(
                "updated_at"
            )

            filing.save(
                update_fields=(
                    update_fields
                )
            )

        IngestionJob.objects.get_or_create(
            filing=filing,
            defaults={
                "status": (
                    IngestionJob
                    .Status
                    .PENDING
                ),
            },
        )

        return filing