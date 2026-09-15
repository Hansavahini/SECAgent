from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lxml import etree


class FinancialFactExtractionError(Exception):
    """Raised when Inline XBRL facts cannot be extracted safely."""


@dataclass(frozen=True)
class XBRLDimension:
    axis: str
    member: str

    def __str__(self) -> str:
        return f"{self.axis}={self.member}"


@dataclass(frozen=True)
class XBRLContext:
    context_id: str
    start_date: date | None
    end_date: date | None
    instant: date | None
    dimensions: tuple[XBRLDimension, ...]

    @property
    def period_days(self) -> int | None:
        if self.start_date and self.end_date:
            return (
                self.end_date - self.start_date
            ).days + 1

        return None

    @property
    def period_label(self) -> str:
        if self.instant:
            return f"as of {self.instant.isoformat()}"

        days = self.period_days

        if days is None or self.end_date is None:
            return "period not identified"

        # SEC companies commonly use fiscal calendars such as
        # 13-week quarters and 52/53-week years, so use ranges
        # rather than assuming exactly 90/180/270/365 days.
        if 70 <= days <= 110:
            return (
                "three months ended "
                f"{self.end_date.isoformat()}"
            )

        if 150 <= days <= 200:
            return (
                "six months ended "
                f"{self.end_date.isoformat()}"
            )

        if 230 <= days <= 300:
            return (
                "nine months ended "
                f"{self.end_date.isoformat()}"
            )

        if 330 <= days <= 385:
            return (
                "year ended "
                f"{self.end_date.isoformat()}"
            )

        return (
            f"{days}-day period ended "
            f"{self.end_date.isoformat()}"
        )

    @property
    def is_dimensional(self) -> bool:
        return bool(self.dimensions)


@dataclass(frozen=True)
class FinancialFact:
    concept: str
    context_id: str
    unit_ref: str
    raw_text: str
    numeric_value: Decimal | None

    start_date: date | None
    end_date: date | None
    instant: date | None

    period_days: int | None
    period_label: str

    dimensions: tuple[XBRLDimension, ...]

    @property
    def is_dimensional(self) -> bool:
        return bool(self.dimensions)

    @property
    def dimension_count(self) -> int:
        return len(self.dimensions)

    @property
    def dimension_label(self) -> str:
        if not self.dimensions:
            return "CONSOLIDATED"

        return "; ".join(
            str(dimension)
            for dimension in self.dimensions
        )

    @property
    def source_key(self) -> str:
        dimension_key = "|".join(
            str(dimension)
            for dimension in self.dimensions
        )

        return (
            f"{self.concept}|"
            f"{self.context_id}|"
            f"{self.unit_ref}|"
            f"{self.raw_text}|"
            f"{dimension_key}"
        )


class FinancialFactExtractor:
    """
    Extract numeric facts, reporting periods and XBRL
    dimensions from an Inline XBRL SEC filing.

    This class is completely company-independent.

    It does NOT decide which facts should appear in a
    summary. It only produces reliable evidence for the
    later fact-selection layer.
    """

    DATE_RE = re.compile(
        r"^\d{4}-\d{2}-\d{2}$"
    )

    NUMBER_RE = re.compile(
        r"""
        ^\s*
        (?P<sign>[+-]?)
        (?P<number>
            (?:\d{1,3}(?:,\d{3})+)
            |
            (?:\d+)
        )
        (?:\.(?P<decimal>\d+))?
        \s*$
        """,
        re.VERBOSE,
    )

    def extract_from_document(
        self,
        document,
    ) -> tuple[FinancialFact, ...]:
        local_path = str(
            getattr(
                document,
                "local_path",
                "",
            )
            or ""
        ).strip()

        if not local_path:
            return ()

        return self.extract_from_path(
            local_path
        )

    def extract_from_path(
        self,
        path: str | Path,
    ) -> tuple[FinancialFact, ...]:

        file_path = Path(path)

        if not file_path.exists():
            raise FinancialFactExtractionError(
                "SEC document does not exist: "
                f"{file_path}"
            )

        try:
            parser = etree.XMLParser(
                recover=True,
                huge_tree=True,
                resolve_entities=False,
                no_network=True,
            )

            tree = etree.parse(
                str(file_path),
                parser,
            )

            root = tree.getroot()

        except (
            etree.XMLSyntaxError,
            OSError,
            ValueError,
        ) as exc:
            raise FinancialFactExtractionError(
                "Unable to parse Inline XBRL document "
                f"{file_path}: {exc}"
            ) from exc

        contexts = self._extract_contexts(
            root
        )

        if not contexts:
            return ()

        facts = []
        seen = set()

        for element in root.iter():
            if (
                self._local_name(
                    element.tag
                )
                != "nonFraction"
            ):
                continue

            context_id = str(
                element.get("contextRef")
                or ""
            ).strip()

            if not context_id:
                continue

            context = contexts.get(
                context_id
            )

            if context is None:
                continue

            concept = str(
                element.get("name")
                or ""
            ).strip()

            if not concept:
                continue

            if self._is_nil(
                element
            ):
                continue

            raw_text = self._element_text(
                element
            )

            if not raw_text:
                continue

            numeric_value = (
                self._parse_numeric_value(
                    raw_text=raw_text,
                    scale=element.get(
                        "scale"
                    ),
                    sign=element.get(
                        "sign"
                    ),
                )
            )

            unit_ref = str(
                element.get("unitRef")
                or ""
            ).strip()

            fact = FinancialFact(
                concept=concept,
                context_id=context_id,
                unit_ref=unit_ref,
                raw_text=raw_text,
                numeric_value=numeric_value,
                start_date=context.start_date,
                end_date=context.end_date,
                instant=context.instant,
                period_days=(
                    context.period_days
                ),
                period_label=(
                    context.period_label
                ),
                dimensions=(
                    context.dimensions
                ),
            )

            if fact.source_key in seen:
                continue

            seen.add(
                fact.source_key
            )

            facts.append(
                fact
            )

        return tuple(facts)

    def _extract_contexts(
        self,
        root,
    ) -> dict[str, XBRLContext]:

        contexts = {}

        for element in root.iter():
            if (
                self._local_name(
                    element.tag
                )
                != "context"
            ):
                continue

            context_id = str(
                element.get("id")
                or ""
            ).strip()

            if not context_id:
                continue

            start_date = None
            end_date = None
            instant = None

            dimensions = []

            for child in element.iter():
                local_name = (
                    self._local_name(
                        child.tag
                    )
                )

                value = (
                    child.text or ""
                ).strip()

                if (
                    local_name
                    == "startDate"
                ):
                    start_date = (
                        self._parse_date(
                            value
                        )
                    )

                elif (
                    local_name
                    == "endDate"
                ):
                    end_date = (
                        self._parse_date(
                            value
                        )
                    )

                elif (
                    local_name
                    == "instant"
                ):
                    instant = (
                        self._parse_date(
                            value
                        )
                    )

                elif (
                    local_name
                    == "explicitMember"
                ):
                    axis = str(
                        child.get(
                            "dimension"
                        )
                        or ""
                    ).strip()

                    member = value

                    if axis and member:
                        dimensions.append(
                            XBRLDimension(
                                axis=axis,
                                member=member,
                            )
                        )

                elif (
                    local_name
                    == "typedMember"
                ):
                    axis = str(
                        child.get(
                            "dimension"
                        )
                        or ""
                    ).strip()

                    typed_value = (
                        self._typed_member_value(
                            child
                        )
                    )

                    if axis and typed_value:
                        dimensions.append(
                            XBRLDimension(
                                axis=axis,
                                member=typed_value,
                            )
                        )

            dimensions = tuple(
                sorted(
                    dimensions,
                    key=lambda dimension: (
                        dimension.axis,
                        dimension.member,
                    ),
                )
            )

            contexts[
                context_id
            ] = XBRLContext(
                context_id=context_id,
                start_date=start_date,
                end_date=end_date,
                instant=instant,
                dimensions=dimensions,
            )

        return contexts

    def _typed_member_value(
        self,
        typed_member,
    ) -> str:

        values = []

        for child in typed_member.iter():
            if child is typed_member:
                continue

            text = (
                child.text
                or ""
            ).strip()

            if text:
                values.append(
                    text
                )

        return " ".join(
            values
        ).strip()

    @staticmethod
    def _local_name(
        tag,
    ) -> str:

        if not isinstance(
            tag,
            str,
        ):
            return ""

        if "}" in tag:
            return tag.rsplit(
                "}",
                1,
            )[-1]

        if ":" in tag:
            return tag.rsplit(
                ":",
                1,
            )[-1]

        return tag

    @staticmethod
    def _element_text(
        element,
    ) -> str:

        value = "".join(
            element.itertext()
        )

        return re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

    @staticmethod
    def _is_nil(
        element,
    ) -> bool:

        for key, value in (
            element.attrib.items()
        ):
            if (
                key.endswith(
                    "}nil"
                )
                or key == "xsi:nil"
            ):
                return (
                    str(value)
                    .strip()
                    .lower()
                    == "true"
                )

        return False

    def _parse_numeric_value(
        self,
        *,
        raw_text: str,
        scale,
        sign,
    ) -> Decimal | None:

        cleaned = (
            raw_text
            .replace("$", "")
            .replace("%", "")
            .replace("(", "-")
            .replace(")", "")
            .strip()
        )

        match = self.NUMBER_RE.match(
            cleaned
        )

        if match is None:
            return None

        normalized = (
            cleaned
            .replace(",", "")
        )

        try:
            value = Decimal(
                normalized
            )

        except InvalidOperation:
            return None

        if (
            str(sign or "")
            .strip()
            == "-"
            and value > 0
        ):
            value = -value

        if scale not in (
            None,
            "",
        ):
            try:
                scale_value = int(
                    str(scale).strip()
                )

                value *= (
                    Decimal(10)
                    ** scale_value
                )

            except (
                ValueError,
                InvalidOperation,
            ):
                pass

        return value

    @classmethod
    def _parse_date(
        cls,
        value: str,
    ) -> date | None:

        value = str(
            value
            or ""
        ).strip()

        if not cls.DATE_RE.match(
            value
        ):
            return None

        try:
            return date.fromisoformat(
                value
            )

        except ValueError:
            return None