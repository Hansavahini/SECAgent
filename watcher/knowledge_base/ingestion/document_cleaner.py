import html
import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning


warnings.filterwarnings(
    "ignore",
    category=XMLParsedAsHTMLWarning,
)


class DocumentCleaningError(Exception):
    """Raised when filing content cannot be cleaned."""


class DocumentCleaner:
    BLOCK_TAGS = (
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "table",
        "tr",
        "li",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    )

    REMOVE_TAGS = (
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
    )

    def clean(self, raw_content: str) -> str:
        if not isinstance(raw_content, str):
            raise DocumentCleaningError(
                "Filing content must be a string."
            )

        if not raw_content.strip():
            raise DocumentCleaningError(
                "Filing content is empty."
            )

        try:
            soup = BeautifulSoup(
                raw_content,
                "lxml",
            )
        except Exception as exc:
            raise DocumentCleaningError(
                "Unable to parse filing HTML."
            ) from exc

        for tag_name in self.REMOVE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        for tag in soup.find_all(self.BLOCK_TAGS):
            tag.insert_before("\n")
            tag.insert_after("\n")

        text = soup.get_text(
            separator=" ",
            strip=False,
        )

        text = html.unescape(text)

        text = text.replace("\xa0", " ")

        text = re.sub(
            r"[ \t]+",
            " ",
            text,
        )

        text = re.sub(
            r" *\n *",
            "\n",
            text,
        )

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        text = text.strip()

        if not text:
            raise DocumentCleaningError(
                "No readable text remained after cleaning."
            )

        return text