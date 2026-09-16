import logging
import re

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


logger = logging.getLogger(__name__)


class FilingNotificationService:
    """
    Send an email notification after a NEW SEC filing has been:

        1. downloaded
        2. registered
        3. indexed
        4. summarized
        5. stored/reused in PostgreSQL

    IMPORTANT:
    This service does NOT generate summaries.

    It receives the summary already produced by FilingSummaryService
    and only prepares it for a clean, readable email.

    Internal chunk/citation markers are removed from the EMAIL ONLY.
    The original summary stored in PostgreSQL remains unchanged.
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
        Convert the internally generated SEC summary into a cleaner
        user-facing email version.

        Examples removed from email:
            [C1]
            [C25]
            [C625]
            [CHUNK 1]
            [Chunk 12]

        PostgreSQL content is NOT modified.
        """

        text = str(
            summary_text
            or ""
        ).strip()

        if not text:
            return ""

        # ---------------------------------------------------------
        # Remove internal citation labels such as:
        # [C1], [C25], [C625]
        # ---------------------------------------------------------
        text = re.sub(
            r"\s*\[C\d+\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # ---------------------------------------------------------
        # Remove explicit chunk labels such as:
        # [CHUNK 1]
        # [Chunk 12]
        # ---------------------------------------------------------
        text = re.sub(
            r"\s*\[\s*CHUNK\s+\d+\s*\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # ---------------------------------------------------------
        # Remove standalone prefixes such as:
        #
        # Chunk 1:
        # CHUNK 25:
        #
        # Only when they occur at the beginning of a line.
        # ---------------------------------------------------------
        text = re.sub(
            r"(?im)^\s*chunk\s+\d+\s*:\s*",
            "",
            text,
        )

        # ---------------------------------------------------------
        # Fix spaces left before punctuation after removing labels.
        #
        # Example:
        # "Revenue increased [C12]."
        # becomes:
        # "Revenue increased."
        # ---------------------------------------------------------
        text = re.sub(
            r"\s+([,.;:!?])",
            r"\1",
            text,
        )

        # Remove trailing spaces from lines.
        text = "\n".join(
            line.rstrip()
            for line in text.splitlines()
        )

        # ---------------------------------------------------------
        # Avoid excessive blank lines.
        # ---------------------------------------------------------
        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

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
    ):
        """
        Send the already-generated filing summary by email.

        Returns:
            True:
                email sent successfully

            False:
                notification disabled,
                configuration missing,
                summary missing,
                or email sending failed
        """

        # ---------------------------------------------------------
        # Email notification feature switch.
        # ---------------------------------------------------------
        if not self.enabled:
            logger.info(
                "SEC email notification disabled."
            )
            return False

        # ---------------------------------------------------------
        # Recipient must be configured.
        # ---------------------------------------------------------
        if not self.recipient:
            logger.warning(
                "SEC email notification skipped: "
                "SEC_ALERT_RECIPIENT_EMAIL is not configured."
            )
            return False

        # ---------------------------------------------------------
        # Clean ONLY the copy being sent through email.
        #
        # The original PostgreSQL summary remains untouched.
        # ---------------------------------------------------------
        clean_summary = (
            self._clean_summary_for_email(
                summary_text
            )
        )

        if not clean_summary:
            logger.warning(
                "SEC email notification skipped: "
                "summary is empty for ticker=%s "
                "accession=%s",
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
                "SEC email notification skipped: "
                "DEFAULT_FROM_EMAIL is empty."
            )
            return False

        sender = (
            f"{sender_name} "
            f"<{sender_email}>"
        )

        # ---------------------------------------------------------
        # Email subject.
        # ---------------------------------------------------------
        subject = (
            f"[SEC Filing Alert] "
            f"{ticker} - New {form_type} Filing"
        )

        # ---------------------------------------------------------
        # Human-readable email body.
        # ---------------------------------------------------------
        body = f"""
A new SEC filing has been detected and processed successfully.

COMPANY / FILING DETAILS
------------------------------------------------------------
Ticker: {ticker}
Form Type: {form_type}
Filing Date: {filing_date or "N/A"}
Accession Number: {accession_number or "N/A"}
Filename: {filename or "N/A"}


FILING SUMMARY
------------------------------------------------------------
{clean_summary}


SOURCE INFORMATION
------------------------------------------------------------
SEC Filing:
{sec_url or "N/A"}

Downloaded File:
{local_path or "N/A"}


This notification was generated automatically by the SEC Filing Watcher.
""".strip()

        # ---------------------------------------------------------
        # Optional reply-to address.
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
                    "SEC filing summary email sent "
                    "successfully: "
                    "ticker=%s form=%s accession=%s "
                    "recipient=%s",
                    ticker,
                    form_type,
                    accession_number,
                    self.recipient,
                )

                return True

            logger.warning(
                "SEC filing summary email returned "
                "unexpected send result: "
                "result=%s ticker=%s accession=%s",
                result,
                ticker,
                accession_number,
            )

            return False

        except Exception:
            # -----------------------------------------------------
            # Very important:
            #
            # SMTP/email failure must NEVER break:
            #   - downloading
            #   - indexing
            #   - PostgreSQL storage
            #   - summary generation
            #   - the remaining watcher process
            # -----------------------------------------------------
            logger.exception(
                "SEC filing summary email failed: "
                "ticker=%s form=%s accession=%s",
                ticker,
                form_type,
                accession_number,
            )

            return False