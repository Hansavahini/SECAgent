import re
from dataclasses import dataclass

from django.db.models import Q

from watcher.knowledge_base.models import (
    Filing,
    FilingChunk,
)


@dataclass(frozen=True)
class ItemVerificationResult:
    status: str
    sec_items: tuple[str, ...]
    parsed_items: tuple[str, ...]
    missing_from_parser: tuple[str, ...]
    extra_in_parser: tuple[str, ...]

    @property
    def matched(self):
        return self.status == "MATCH"


class ItemVerificationService:
    """
    Compare SEC structured 8-K item codes with item numbers
    extracted by the existing SEC parser.

    This service never modifies filing/chunk data.
    """

    ITEM_PATTERN = re.compile(
        r"^(?:ITEM\s+)?"
        r"(?P<number>\d{1,2}(?:\.\d{2})?[A-Z]?)$",
        re.IGNORECASE,
    )

    @classmethod
    def normalize_item_code(cls, value):
        if value is None:
            return ""

        text = str(value).strip().upper()

        if not text:
            return ""

        text = re.sub(
            r"\s+",
            " ",
            text,
        )

        match = cls.ITEM_PATTERN.match(text)

        if not match:
            return ""

        return match.group("number").upper()

    @classmethod
    def normalize_item_codes(cls, values):
        result = []
        seen = set()

        for value in values or ():
            item = cls.normalize_item_code(value)

            if not item:
                continue

            if item in seen:
                continue

            seen.add(item)
            result.append(item)

        return tuple(result)

    def get_parsed_items(self, filing):
        if not isinstance(filing, Filing):
            raise TypeError(
                "filing must be a Filing instance."
            )

        values = (
            FilingChunk.objects
            .filter(filing=filing)
            .filter(
                Q(document__is_primary=True)
                | Q(document__isnull=True)
            )
            .exclude(item_number="")
            .values_list(
                "item_number",
                flat=True,
            )
        )

        return self.normalize_item_codes(values)

    def verify(
        self,
        filing,
        sec_item_codes,
    ):
        sec_items = self.normalize_item_codes(
            sec_item_codes
        )

        parsed_items = self.get_parsed_items(
            filing
        )

        if not sec_items:
            return ItemVerificationResult(
                status="SEC_ITEMS_NOT_AVAILABLE",
                sec_items=(),
                parsed_items=parsed_items,
                missing_from_parser=(),
                extra_in_parser=(),
            )

        sec_set = set(sec_items)
        parsed_set = set(parsed_items)

        missing = tuple(
            item
            for item in sec_items
            if item not in parsed_set
        )

        extra = tuple(
            item
            for item in parsed_items
            if item not in sec_set
        )

        status = (
            "MATCH"
            if not missing and not extra
            else "MISMATCH"
        )

        return ItemVerificationResult(
            status=status,
            sec_items=sec_items,
            parsed_items=parsed_items,
            missing_from_parser=missing,
            extra_in_parser=extra,
        )