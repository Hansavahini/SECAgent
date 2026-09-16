from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal


class MarketSessionService:
    """
    Maps an EDGAR acceptance datetime to the correct next
    tradeable U.S. equity market session.

    Rules:

        Trading session + acceptance before market close:
            same session

        Trading session + acceptance at/after market close:
            next trading session

        Weekend:
            next trading session

        NYSE holiday:
            next trading session

        Early-close session:
            use the actual NYSE market close from the exchange
            calendar rather than assuming 4:00 PM ET.

    This service is deterministic.

    It does NOT:
        - download SEC filings
        - modify the download registry
        - write to PostgreSQL
        - generate summaries
        - send email
    """

    UTC = ZoneInfo(
        "UTC"
    )

    EASTERN = ZoneInfo(
        "America/New_York"
    )

    CALENDAR_NAME = "NYSE"

    def __init__(
        self,
        calendar=None,
    ):
        self.calendar = (
            calendar
            or mcal.get_calendar(
                self.CALENDAR_NAME
            )
        )

    @classmethod
    def _normalize_datetime(
        cls,
        value,
    ):
        """
        Require a timezone-aware datetime and normalize it to UTC.

        MarketSessionService intentionally does not guess the timezone
        of naive datetimes.
        """

        if not isinstance(
            value,
            datetime,
        ):
            raise TypeError(
                "accepted_at must be a datetime instance."
            )

        if value.tzinfo is None:
            raise ValueError(
                "accepted_at must include timezone information."
            )

        return value.astimezone(
            cls.UTC
        )

    def _schedule_from(
        self,
        start_date,
    ):
        """
        Get enough NYSE sessions to locate the current or next
        tradeable session.

        A 14-calendar-day window comfortably crosses normal
        weekends and exchange holidays while keeping the lookup
        small.

        If no sessions are returned, fail explicitly rather than
        inventing a trading date.
        """

        end_date = (
            start_date
            + timedelta(
                days=14,
            )
        )

        schedule = self.calendar.schedule(
            start_date=(
                start_date.isoformat()
            ),
            end_date=(
                end_date.isoformat()
            ),
        )

        if schedule.empty:
            raise RuntimeError(
                "NYSE calendar returned no trading sessions "
                f"between {start_date} and {end_date}."
            )

        return schedule

    def entry_session(
        self,
        accepted_at,
    ):
        """
        Return the date of the tradeable session associated with
        an EDGAR acceptance datetime.

        Returns:
            datetime.date

        Example:

            accepted Monday 3:30 PM ET
                -> Monday

            accepted Monday 4:45 PM ET
                -> Tuesday

            accepted Saturday
                -> next NYSE trading day
        """

        accepted_utc = (
            self._normalize_datetime(
                accepted_at
            )
        )

        accepted_eastern = (
            accepted_utc.astimezone(
                self.EASTERN
            )
        )

        local_date = (
            accepted_eastern.date()
        )

        schedule = self._schedule_from(
            local_date
        )

        sessions = list(
            schedule.iterrows()
        )

        for index, (
            session_label,
            row,
        ) in enumerate(
            sessions
        ):
            session_date = (
                session_label.date()
            )

            # -----------------------------------------------------
            # If the first available NYSE session occurs after the
            # acceptance calendar date, then acceptance happened
            # during a weekend/holiday/market closure.
            #
            # That first session is therefore the entry session.
            # -----------------------------------------------------
            if session_date > local_date:
                return session_date

            if session_date != local_date:
                continue

            # -----------------------------------------------------
            # The acceptance date itself is a trading session.
            #
            # pandas_market_calendars provides the actual market
            # close, including shortened sessions.
            # -----------------------------------------------------
            market_close = (
                row[
                    "market_close"
                ]
                .to_pydatetime()
            )

            market_close_utc = (
                market_close.astimezone(
                    self.UTC
                )
            )

            # -----------------------------------------------------
            # Client requirement:
            #
            # BEFORE market close
            #     -> same session
            #
            # AT or AFTER market close
            #     -> next tradeable session
            #
            # Using "<" rather than "<=" means exactly at the close
            # belongs to the next entry session.
            # -----------------------------------------------------
            if accepted_utc < market_close_utc:
                return session_date

            # -----------------------------------------------------
            # Acceptance occurred at/after the market close.
            # Return the next schedule row.
            # -----------------------------------------------------
            if (
                index + 1
                < len(sessions)
            ):
                next_label = (
                    sessions[
                        index + 1
                    ][0]
                )

                return (
                    next_label.date()
                )

            break

        raise RuntimeError(
            "Unable to determine an NYSE entry session "
            f"for acceptance datetime {accepted_at!r}."
        )