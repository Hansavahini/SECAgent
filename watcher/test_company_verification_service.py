from django.test import SimpleTestCase

from watcher.services.company_verification_service import (
    CompanyVerificationService,
)


class CompanyVerificationServiceTests(
    SimpleTestCase
):
    def setUp(self):
        self.service = (
            CompanyVerificationService()
        )

    def test_same_cik_matches_with_leading_zeros(self):
        result = self.service.verify(
            expected_cik="0001018724",
            sec_cik="1018724",
            expected_company_name=(
                "AMAZON COM INC"
            ),
            sec_company_name=(
                "Amazon.com, Inc."
            ),
            expected_ticker="AMZN",
            sec_tickers=(
                "AMZN",
            ),
        )

        self.assertEqual(
            result.status,
            "MATCH",
        )

        self.assertTrue(
            result.cik_match
        )

        self.assertTrue(
            result.name_match
        )

        self.assertTrue(
            result.ticker_match
        )

    def test_different_cik_is_mismatch(self):
        result = self.service.verify(
            expected_cik="0001018724",
            sec_cik="0000000001",
            expected_ticker="AMZN",
            sec_tickers=(
                "AMZN",
            ),
        )

        self.assertEqual(
            result.status,
            "MISMATCH",
        )

        self.assertFalse(
            result.cik_match
        )

    def test_missing_sec_cik_is_unavailable(self):
        result = self.service.verify(
            expected_cik="0001018724",
            sec_cik="",
        )

        self.assertEqual(
            result.status,
            "UNAVAILABLE",
        )

        self.assertIsNone(
            result.cik_match
        )