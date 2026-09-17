from django.test import SimpleTestCase

from watcher.knowledge_base.ingestion.sec_parser import SECParser


class SECParserWhitespaceTests(SimpleTestCase):

    def test_item_heading_whitespace_variants(self):
        parser = SECParser()

        cases = [
            "Item 8.01 Other Events",
            "ITEM 8.01 Other Events",
            "Item\t8.01 Other Events",
            "Item\u00a08.01 Other Events",
            "Item\u20098.01 Other Events",
            "Item\u202f8.01 Other Events",
            "Item  8.01 Other Events",
            "Item 8.01: Other Events",
            "Item 8.01 — Other Events",
        ]

        for text in cases:
            with self.subTest(text=repr(text)):
                sections = parser.parse(text)

                self.assertEqual(
                    [section.item_number for section in sections],
                    ["Item 8.01"],
                )

    def test_multiple_sec_items_with_unicode_spacing(self):
        parser = SECParser()

        text = (
            "Item\u20098.01 Other Events\n"
            "Some filing content here.\n\n"
            "Item\u202f9.01 Financial Statements and Exhibits\n"
            "Exhibit information here."
        )

        sections = parser.parse(text)

        self.assertEqual(
            [section.item_number for section in sections],
            [
                "Item 8.01",
                "Item 9.01",
            ],
        )