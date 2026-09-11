import threading
import time

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class SECClient:
    """
    HTTP client for SEC requests.

    Provides:
    - SEC-compliant User-Agent (required by SEC fair-access policy)
    - finite connect AND read timeouts (a request can never hang forever)
    - bounded retries with exponential backoff for transient failures
    - Retry-After support for 429 responses
    - a process-wide rate limiter (SEC allows roughly 10 req/sec)

    Deliberately does NOT set a Host header. The SEC uses three
    different hostnames:

        www.sec.gov     archives, company_tickers.json
        data.sec.gov    submissions API
        efts.sec.gov    full text search

    A hardcoded "Host: www.sec.gov" is sent to all three, which makes
    the CDN route the request to the wrong origin. That is what caused
    requests to hang. requests/urllib3 set the correct Host per URL.
    """

    DEFAULT_TIMEOUT = (10.0, 30.0)
    DEFAULT_MAX_RETRIES = 4
    DEFAULT_MIN_REQUEST_INTERVAL = 0.15
    MAX_BACKOFF_SECONDS = 16.0

    RETRY_STATUS_CODES = {
        429,
        500,
        502,
        503,
        504,
    }

    # Shared across every SECClient instance so that TickerResolver,
    # FilingSearch and FilingDownloader together stay inside the SEC
    # rate limit.
    _throttle_lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(
        self,
        user_agent=None,
        timeout=None,
        max_retries=None,
        min_request_interval=None,
        verbose=True,
    ):
        resolved_user_agent = (
            user_agent
            if user_agent is not None
            else getattr(settings, "SEC_USER_AGENT", "")
        )

        resolved_user_agent = (resolved_user_agent or "").strip()

        if not resolved_user_agent:
            raise ImproperlyConfigured(
                "SEC_USER_AGENT is not configured.\n"
                "SEC requires a descriptive User-Agent with contact info.\n"
                "Add this line to backend/.env (see .env.example):\n"
                "    SEC_USER_AGENT=Your Name your.email@example.com"
            )

        self.user_agent = resolved_user_agent
        self.verbose = verbose

        self.timeout = self._resolve_timeout(
            timeout
            if timeout is not None
            else getattr(settings, "SEC_REQUEST_TIMEOUT", self.DEFAULT_TIMEOUT)
        )

        self.max_retries = max(
            1,
            int(
                max_retries
                if max_retries is not None
                else getattr(
                    settings,
                    "SEC_MAX_RETRIES",
                    self.DEFAULT_MAX_RETRIES,
                )
            ),
        )

        self.min_request_interval = max(
            0.0,
            float(
                min_request_interval
                if min_request_interval is not None
                else getattr(
                    settings,
                    "SEC_MIN_REQUEST_INTERVAL",
                    self.DEFAULT_MIN_REQUEST_INTERVAL,
                )
            ),
        )

        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Connection": "keep-alive",
            }
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _resolve_timeout(value):
        """
        Always return a (connect, read) tuple with finite values.

        A single scalar timeout still lets a slow-trickling response
        stall, and None means "wait forever", which is the original bug.
        """
        if isinstance(value, (tuple, list)) and len(value) == 2:
            connect, read = value
        elif isinstance(value, (int, float)):
            connect = read = value
        else:
            connect, read = SECClient.DEFAULT_TIMEOUT

        try:
            connect = float(connect)
            read = float(read)
        except (TypeError, ValueError):
            connect, read = SECClient.DEFAULT_TIMEOUT

        if connect <= 0:
            connect = SECClient.DEFAULT_TIMEOUT[0]

        if read <= 0:
            read = SECClient.DEFAULT_TIMEOUT[1]

        return (connect, read)

    @classmethod
    def _throttle(cls, min_interval):
        """
        Keep at least min_interval seconds between SEC requests,
        across every SECClient instance in this process.
        """
        if min_interval <= 0:
            return

        with cls._throttle_lock:
            wait = cls._last_request_at + min_interval - time.monotonic()

            if wait > 0:
                time.sleep(wait)

            cls._last_request_at = time.monotonic()

    def _log(self, message):
        if self.verbose:
            print(message)

    @staticmethod
    def _retry_after_seconds(response, fallback):
        header = response.headers.get("Retry-After")

        if not header:
            return fallback

        try:
            return min(float(header), SECClient.MAX_BACKOFF_SECONDS)
        except (TypeError, ValueError):
            return fallback

    # -----------------------------------------------------------------
    # Requests
    # -----------------------------------------------------------------

    def get(
        self,
        url,
        params=None,
        timeout=None,
        headers=None,
        allow_status=(),
    ):
        """
        GET with finite timeout, bounded retries and rate limiting.

        allow_status:
            status codes returned to the caller instead of raising.
            Used to probe alternative EDGAR archive paths without
            treating a 404 as a fatal error.
        """

        request_timeout = (
            self._resolve_timeout(timeout)
            if timeout is not None
            else self.timeout
        )

        allow_status = set(allow_status or ())

        last_exception = None

        for attempt in range(1, self.max_retries + 1):

            backoff = min(
                2.0 ** (attempt - 1),
                self.MAX_BACKOFF_SECONDS,
            )

            self._throttle(self.min_request_interval)

            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=request_timeout,
                    headers=headers,
                    allow_redirects=True,
                )

            except requests.exceptions.Timeout as exc:
                last_exception = exc

                if attempt < self.max_retries:
                    self._log(
                        f"SEC request timed out ({url}). "
                        f"Retrying in {backoff:.1f}s "
                        f"[{attempt}/{self.max_retries}]"
                    )
                    time.sleep(backoff)
                    continue

                break

            except requests.exceptions.ConnectionError as exc:
                last_exception = exc

                if attempt < self.max_retries:
                    self._log(
                        f"SEC connection error ({url}). "
                        f"Retrying in {backoff:.1f}s "
                        f"[{attempt}/{self.max_retries}]"
                    )
                    time.sleep(backoff)
                    continue

                break

            except requests.exceptions.RequestException:
                raise

            if response.status_code in allow_status:
                return response

            if response.status_code in self.RETRY_STATUS_CODES:

                if attempt < self.max_retries:
                    wait = self._retry_after_seconds(response, backoff)

                    self._log(
                        f"SEC returned {response.status_code} ({url}). "
                        f"Retrying in {wait:.1f}s "
                        f"[{attempt}/{self.max_retries}]"
                    )

                    time.sleep(wait)
                    continue

            response.raise_for_status()

            return response

        if last_exception is not None:
            raise last_exception

        raise RuntimeError(
            f"SEC request failed after {self.max_retries} attempts: {url}"
        )

    def get_json(self, url, params=None, timeout=None):
        """
        GET a JSON endpoint and decode it, with a readable error when
        SEC returns HTML (an error page) instead of JSON.
        """
        response = self.get(
            url,
            params=params,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

        try:
            return response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")

            raise ValueError(
                f"SEC returned non-JSON from {response.url} "
                f"(status {response.status_code}): {snippet}"
            ) from exc
