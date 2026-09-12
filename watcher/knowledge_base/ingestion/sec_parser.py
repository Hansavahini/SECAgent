import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SECSection:
    item_number: str
    title: str
    text: str
    char_start: int
    char_end: int


class SECParser:
    ITEM_PATTERN = re.compile(
        r"(?im)^[ \t]*"
        r"item[ \t]+"
        r"(?P<number>\d{1,2}(?:\.\d{2})?[A-Z]?)"
        r"[ \t]*[\.\:\-–—]?[ \t]*"
        r"(?P<title>[^\n]*)"
        r"$"
    )

    MIN_DUPLICATE_BODY_CHARS = 200

    def parse(self, text: str) -> list[SECSection]:
        if not isinstance(text, str):
            raise TypeError(
                "SEC filing text must be a string."
            )

        if not text.strip():
            return []

        matches = list(
            self.ITEM_PATTERN.finditer(text)
        )

        if not matches:
            return [
                SECSection(
                    item_number="",
                    title="",
                    text=text.strip(),
                    char_start=0,
                    char_end=len(text),
                )
            ]

        sections = []

        for index, match in enumerate(matches):
            start = match.start()

            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(text)
            )

            number = match.group("number").upper()

            title = (
                match.group("title") or ""
            ).strip()

            section_text = text[start:end].strip()

            if not section_text:
                continue

            sections.append(
                SECSection(
                    item_number=f"Item {number}",
                    title=title,
                    text=section_text,
                    char_start=start,
                    char_end=end,
                )
            )

        return self._remove_toc_duplicates(sections)

    def _remove_toc_duplicates(
        self,
        sections: list[SECSection],
    ) -> list[SECSection]:

        occurrence_counts = {}

        for section in sections:
            occurrence_counts[section.item_number] = (
                occurrence_counts.get(section.item_number, 0)
                + 1
            )

        cleaned = []

        for section in sections:
            duplicated = (
                occurrence_counts[section.item_number] > 1
            )

            if (
                duplicated
                and len(section.text)
                < self.MIN_DUPLICATE_BODY_CHARS
            ):
                continue

            cleaned.append(section)

        return cleaned