from watcher.services.notification_service import (
    FilingNotificationService,
)


class FilingEmailService:
    """
    Connects completed filing processing metadata to the
    existing notification service.

    Responsibilities:
        - pass filing metadata to email
        - pass item verification status
        - pass company verification status
        - preserve the generated summary unchanged

    This service does not generate summaries.
    """

    MANUAL_AUDIT_PENDING = "NOT YET VERIFIED"

    def __init__(
        self,
        *,
        notification_service=None,
    ):
        self.notification_service = (
            notification_service
            or FilingNotificationService()
        )

    def send(
        self,
        *,
        summary_result,
        filename,
        saved_path,
        source_url,
        metadata=None,
        item_verification=None,
    ):
        summary_text = str(
            summary_result.summary
            or ""
        ).strip()

        if not summary_text:
            return False

        accepted_at_display = ""
        entry_session = None
        item_codes = ()
        company_verification_status = ""

        # ---------------------------------------------------------
        # Optional EDGAR metadata.
        # ---------------------------------------------------------

        if metadata is not None:
            accepted_at_display = str(
                getattr(
                    metadata,
                    "accepted_at_display",
                    "",
                )
                or ""
            ).strip()

            entry_session = getattr(
                metadata,
                "entry_session",
                None,
            )

            item_codes = tuple(
                getattr(
                    metadata,
                    "sec_item_codes",
                    (),
                )
                or ()
            )

            company_verification = getattr(
                metadata,
                "company_verification",
                None,
            )

            if company_verification is not None:
                company_verification_status = str(
                    getattr(
                        company_verification,
                        "status",
                        "",
                    )
                    or ""
                ).strip()

        # ---------------------------------------------------------
        # Structured 8-K item verification.
        # ---------------------------------------------------------

        item_verification_status = ""

        if item_verification is not None:
            item_verification_status = str(
                getattr(
                    item_verification,
                    "status",
                    "",
                )
                or ""
            ).strip()

        # ---------------------------------------------------------
        # Existing notification service.
        # ---------------------------------------------------------

        return (
            self.notification_service
            .send_new_filing_notification(
                ticker=(
                    summary_result.ticker
                ),
                form_type=(
                    summary_result.form
                ),
                filename=filename,
                filing_date=(
                    summary_result.filing_date
                ),
                accession_number=(
                    summary_result.accession_number
                ),
                local_path=saved_path,
                sec_url=source_url,
                summary_text=summary_text,
                accepted_at_display=(
                    accepted_at_display
                ),
                entry_session=(
                    entry_session
                ),
                item_codes=(
                    item_codes
                ),
                item_verification_status=(
                    item_verification_status
                ),
                company_verification_status=(
                    company_verification_status
                ),
                manual_audit_status=(
                    self.MANUAL_AUDIT_PENDING
                ),
            )
        )