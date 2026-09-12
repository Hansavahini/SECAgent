from dataclasses import dataclass

from watcher.knowledge_base.ingestion.sec_parser import SECSection


@dataclass(frozen=True)
class FilingChunkData:
    chunk_index: int
    item_number: str
    section_title: str
    text: str
    char_start: int | None
    char_end: int | None


class FilingChunker:
    def __init__(
        self,
        max_chars: int = 4000,
        overlap_chars: int = 400,
    ):
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than 0.")

        if overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative.")

        if overlap_chars >= max_chars:
            raise ValueError(
                "overlap_chars must be smaller than max_chars."
            )

        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk_sections(
        self,
        sections: list[SECSection],
    ) -> list[FilingChunkData]:
        chunks = []
        chunk_index = 0

        for section in sections:
            section_text = section.text.strip()

            if not section_text:
                continue

            if len(section_text) <= self.max_chars:
                chunks.append(
                    FilingChunkData(
                        chunk_index=chunk_index,
                        item_number=section.item_number,
                        section_title=section.title,
                        text=section_text,
                        char_start=section.char_start,
                        char_end=section.char_end,
                    )
                )
                chunk_index += 1
                continue

            start = 0

            while start < len(section_text):
                end = min(
                    start + self.max_chars,
                    len(section_text),
                )

                chunk_text = section_text[start:end].strip()

                if chunk_text:
                    absolute_start = (
                        section.char_start + start
                        if section.char_start is not None
                        else None
                    )

                    absolute_end = (
                        section.char_start + end
                        if section.char_start is not None
                        else None
                    )

                    chunks.append(
                        FilingChunkData(
                            chunk_index=chunk_index,
                            item_number=section.item_number,
                            section_title=section.title,
                            text=chunk_text,
                            char_start=absolute_start,
                            char_end=absolute_end,
                        )
                    )

                    chunk_index += 1

                if end >= len(section_text):
                    break

                start = end - self.overlap_chars

        return chunks