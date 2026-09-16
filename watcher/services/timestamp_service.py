from datetime import datetime
from zoneinfo import ZoneInfo


class TimestampService:
    """
    Handles EDGAR acceptance timestamps.

    Responsibilities:
        1. Parse the acceptanceDateTime returned by the SEC
           submissions API.
        2. Preserve the timestamp to the second.
        3. Normalize the stored datetime to UTC.
        4. Convert it to U.S. Eastern Time when needed for
           display and market-session calculations.

    This service does NOT:
        - calculate trading sessions
        - modify filing dates
        - write to PostgreSQL
        - download filings
        - generate summaries
        - send emails
    """

    UTC = ZoneInfo("UTC")

    EASTERN = ZoneInfo(
        "America/New_York"
    )

    @classmethod
    def parse_acceptance(
        cls,
        value,
    ):
        """
        Parse an SEC acceptanceDateTime value.

        Expected SEC submissions API example:

            2026-09-14T20:45:41.000Z

        Returns:
            timezone-aware datetime normalized to UTC

        Returns None when the input is empty.

        Raises:
            ValueError if a non-empty value cannot be parsed or
            does not contain timezone information.
        """

        if value is None:
            return None

        # ---------------------------------------------------------
        # Allow an already-created datetime to be reused safely.
        # ---------------------------------------------------------
        if isinstance(
            value,
            datetime,
        ):
            parsed = value

        else:
            text = str(
                value
            ).strip()

            if not text:
                return None

            # -----------------------------------------------------
            # Python datetime.fromisoformat() understands +00:00.
            #
            # SEC commonly returns UTC using a trailing Z:
            #
            #     2026-09-14T20:45:41.000Z
            #
            # Convert only that timezone marker.
            # -----------------------------------------------------
            if text.endswith(
                (
                    "Z",
                    "z",
                )
            ):
                text = (
                    text[:-1]
                    + "+00:00"
                )

            try:
                parsed = (
                    datetime.fromisoformat(
                        text
                    )
                )

            except ValueError as exc:
                raise ValueError(
                    "Invalid EDGAR acceptance datetime: "
                    f"{value!r}"
                ) from exc

        # ---------------------------------------------------------
        # Never silently guess a timezone.
        #
        # The SEC submissions acceptanceDateTime provides timezone
        # information. If it is missing, failing explicitly is safer
        # than treating a local timestamp as UTC or Eastern.
        # ---------------------------------------------------------
        if parsed.tzinfo is None:
            raise ValueError(
                "EDGAR acceptance datetime must include "
                "timezone information."
            )

        # ---------------------------------------------------------
        # PostgreSQL/Django should receive one canonical timestamp.
        #
        # Normalize to UTC.
        # ---------------------------------------------------------
        return parsed.astimezone(
            cls.UTC
        )

    @classmethod
    def to_eastern(
        cls,
        value,
    ):
        """
        Convert an acceptance timestamp to America/New_York.

        This automatically handles EST/EDT daylight-saving
        transitions through Python's zoneinfo database.

        Returns None for an empty value.
        """

        if value is None:
            return None

        if isinstance(
            value,
            datetime,
        ):
            parsed = value

            if parsed.tzinfo is None:
                raise ValueError(
                    "Datetime must include timezone information."
                )

        else:
            parsed = cls.parse_acceptance(
                value
            )

        if parsed is None:
            return None

        return parsed.astimezone(
            cls.EASTERN
        )

    @classmethod
    def format_eastern(
        cls,
        value,
    ):
        """
        Produce a human-readable Eastern Time representation.

        Example:

            2026-09-14 16:45:41 EDT

        Intended for logs/email display only.

        The underlying database value should remain a real datetime,
        not this formatted string.
        """

        eastern = cls.to_eastern(
            value
        )

        if eastern is None:
            return ""

        return eastern.strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )