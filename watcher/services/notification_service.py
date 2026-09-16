import logging
import re

from django.conf import settings
from django.core.mail import (
    EmailMultiAlternatives,
)


logger = logging.getLogger(__name__)


class FilingNotificationService:
    """
    Sends an already-generated SEC filing summary.

    Summary generation and PostgreSQL storage happen elsewhere.
    Email failures never interrupt the watcher flow.
    """

    def __init__(self):
        self.enabled = getattr(
            settings,
            "SEC_EMAIL_NOTIFICATIONS_ENABLED",
            False,
        )

        self.recipient = getattr(
            settings,
            "SEC_ALERT_RECIPIENT_EMAIL",
            "",
        )

    @staticmethod
    def _clean_summary_for_email(
        summary_text,
    ):
        """
        Clean only the email copy of the generated summary.

        PostgreSQL summary content remains unchanged.
        """

        text = str(
            summary_text
            or ""
        ).strip()

        if not text:
            return ""

        # Remove internal citations:
        # [C1], [C25], [C625]
        text = re.sub(
            r"\s*\[C\d+\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # Remove chunk labels:
        # [CHUNK 1], [Chunk 20]
        text = re.sub(
            r"\s*\[\s*CHUNK\s+\d+\s*\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # Remove line prefixes:
        # Chunk 1:
        text = re.sub(
            r"(?im)^\s*chunk\s+\d+\s*:\s*",
            "",
            text,
        )

        # Fix spaces before punctuation.
        text = re.sub(
            r"\s+([,.;:!?])",
            r"\1",
            text,
        )

        # Remove trailing spaces.
        text = "\n".join(
            line.rstrip()
            for line in text.splitlines()
        )

        # Avoid excessive blank lines.
        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    @staticmethod
    def _format_item_codes(
        item_codes,
    ):
        values = []
        seen = set()

        for item in item_codes or ():
            value = str(
                item
            ).strip()

            if not value:
                continue

            if value in seen:
                continue

            seen.add(value)
            values.append(value)

        if not values:
            return "N/A"

        return ", ".join(values)

    @classmethod
    def _build_body(
        cls,
        *,
        ticker,
        form_type,
        filename,
        filing_date,
        accession_number,
        local_path,
        sec_url,
        clean_summary,
        accepted_at_display,
        entry_session,
        item_codes,
        item_verification_status,
        company_verification_status,
        manual_audit_status,
    ):
        """
        Build the plain-text SEC filing notification email.
        """

        details = [
            (
                "A new SEC filing has been detected "
                "and processed successfully."
            ),
            "",
            "COMPANY / FILING DETAILS",
            "-" * 60,
            f"Ticker: {ticker}",
            f"Form Type: {form_type}",
            (
                "Filing Date: "
                f"{filing_date or 'N/A'}"
            ),
        ]

        # ---------------------------------------------------------
        # EDGAR acceptance timestamp.
        # ---------------------------------------------------------

        if accepted_at_display:
            details.append(
                "EDGAR Accepted: "
                f"{accepted_at_display}"
            )

        # ---------------------------------------------------------
        # Calculated market entry session.
        # ---------------------------------------------------------

        if entry_session:
            details.append(
                "Entry Session: "
                f"{entry_session}"
            )

        # ---------------------------------------------------------
        # Structured 8-K metadata.
        # ---------------------------------------------------------

        if (
            str(form_type)
            .strip()
            .upper()
            == "8-K"
        ):
            details.append(
                "SEC Item Codes: "
                f"{cls._format_item_codes(item_codes)}"
            )

            details.append(
                "Item Verification: "
                f"{item_verification_status or 'NOT AVAILABLE'}"
            )

        # ---------------------------------------------------------
        # Company identity verification.
        # ---------------------------------------------------------

        if company_verification_status:
            details.append(
                "Company Verification: "
                f"{company_verification_status}"
            )

        # ---------------------------------------------------------
        # Independent/manual audit status.
        # ---------------------------------------------------------

        if manual_audit_status:
            details.append(
                "Manual EDGAR Audit: "
                f"{manual_audit_status}"
            )

        # ---------------------------------------------------------
        # Remaining filing details.
        # ---------------------------------------------------------

        details.extend(
            [
                (
                    "Accession Number: "
                    f"{accession_number or 'N/A'}"
                ),
                (
                    "Filename: "
                    f"{filename or 'N/A'}"
                ),
                "",
                "",
                "FILING SUMMARY",
                "-" * 60,
                clean_summary,
                "",
                "",
                "SOURCE INFORMATION",
                "-" * 60,
                "SEC Filing:",
                sec_url or "N/A",
                "",
                "Downloaded File:",
                local_path or "N/A",
                "",
                "",
                (
                    "This notification was generated "
                    "automatically by the SEC Filing Watcher."
                ),
            ]
        )

        return "\n".join(
            details
        ).strip()

    def send_new_filing_notification(
        self,
        *,
        ticker,
        form_type,
        filename,
        filing_date=None,
        accession_number=None,
        local_path=None,
        sec_url=None,
        summary_text=None,
        accepted_at_display=None,
        entry_session=None,
        item_codes=None,
        item_verification_status=None,
        company_verification_status=None,
        manual_audit_status=None,
    ):
        """
        Send one filing summary notification.

        All metadata parameters are optional so existing callers
        remain backward compatible.
        """

        # ---------------------------------------------------------
        # Feature switch.
        # ---------------------------------------------------------

        if not self.enabled:
            logger.info(
                "SEC email notification disabled."
            )
            return False

        # ---------------------------------------------------------
        # Recipient configuration.
        # ---------------------------------------------------------

        if not self.recipient:
            logger.warning(
                "SEC email skipped: recipient "
                "is not configured."
            )
            return False

        # ---------------------------------------------------------
        # Clean only the outgoing email copy.
        # ---------------------------------------------------------

        clean_summary = (
            self._clean_summary_for_email(
                summary_text
            )
        )

        if not clean_summary:
            logger.warning(
                "SEC email skipped: empty summary "
                "ticker=%s accession=%s",
                ticker,
                accession_number,
            )
            return False

        # ---------------------------------------------------------
        # Sender configuration.
        # ---------------------------------------------------------

        sender_name = getattr(
            settings,
            "SEC_EMAIL_SENDER_NAME",
            "SEC Filing Watcher",
        )

        sender_email = getattr(
            settings,
            "DEFAULT_FROM_EMAIL",
            "",
        )

        if not sender_email:
            logger.warning(
                "SEC email skipped: "
                "DEFAULT_FROM_EMAIL is empty."
            )
            return False

        sender = (
            f"{sender_name} "
            f"<{sender_email}>"
        )

        # ---------------------------------------------------------
        # Subject.
        # ---------------------------------------------------------

        subject = (
            "[SEC Filing Alert] "
            f"{ticker} - New {form_type} Filing"
        )

        # ---------------------------------------------------------
        # Body.
        # ---------------------------------------------------------

        body = self._build_body(
            ticker=ticker,
            form_type=form_type,
            filename=filename,
            filing_date=filing_date,
            accession_number=(
                accession_number
            ),
            local_path=local_path,
            sec_url=sec_url,
            clean_summary=clean_summary,
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
                manual_audit_status
            ),
        )

        # ---------------------------------------------------------
        # Optional reply-to.
        # ---------------------------------------------------------

        reply_to_email = getattr(
            settings,
            "SEC_REPLY_TO_EMAIL",
            "",
        )

        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=body,
                from_email=sender,
                to=[
                    self.recipient,
                ],
                reply_to=(
                    [
                        reply_to_email,
                    ]
                    if reply_to_email
                    else None
                ),
            )

            result = email.send(
                fail_silently=False,
            )

            if result == 1:
                logger.info(
                    "SEC filing email sent: "
                    "ticker=%s form=%s accession=%s "
                    "recipient=%s",
                    ticker,
                    form_type,
                    accession_number,
                    self.recipient,
                )

                return True

            logger.warning(
                "SEC email returned unexpected "
                "result=%s ticker=%s accession=%s",
                result,
                ticker,
                accession_number,
            )

            return False

        except Exception:
            # SMTP/email problems must never stop:
            #
            # - downloads
            # - registration
            # - indexing
            # - summary generation
            # - remaining watcher processing

            logger.exception(
                "SEC filing email failed: "
                "ticker=%s form=%s accession=%s",
                ticker,
                form_type,
                accession_number,
            )

            return False