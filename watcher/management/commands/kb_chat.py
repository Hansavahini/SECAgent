from django.core.management.base import BaseCommand

from watcher.knowledge_base.agents.company_resolver import (
    CompanyResolverError,
)
from watcher.knowledge_base.agents.knowledge_base_service import (
    KnowledgeBaseService,
    KnowledgeBaseServiceError,
)
from watcher.knowledge_base.change_detection.change_detection_service import (
    ChangeDetectionError,
)
from watcher.knowledge_base.qa.grounded_qa_service import (
    GroundedQAError,
)
from watcher.knowledge_base.summarization.company_summary_service import (
    CompanySummaryError,
)


class Command(BaseCommand):
    help = "Interactive natural-language SEC knowledge-base chat."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ticker",
            required=False,
            help=(
                "Optional starting company ticker. "
                "Normally the company is detected "
                "from the question automatically."
            ),
        )

        parser.add_argument(
            "--top-k",
            type=int,
            default=6,
            help="Hybrid retrieval candidate count.",
        )

    def handle(self, *args, **options):
        service = KnowledgeBaseService()

        top_k = options["top_k"]

        current_ticker = (
            str(options.get("ticker") or "")
            .strip()
            .upper()
            or None
        )

        if current_ticker:
            try:
                resolution = (
                    service.company_resolver.resolve(
                        f"Use {current_ticker}",
                        ticker=current_ticker,
                    )
                )

                current_ticker = resolution.ticker

            except CompanyResolverError as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f"Invalid starting ticker: {exc}"
                    )
                )
                return

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "SEC Knowledge Base Chat"
            )
        )

        self.stdout.write(
            "Ask questions naturally using a company "
            "name or ticker."
        )

        self.stdout.write(
            "Follow-up questions can use the current "
            "company context."
        )

        self.stdout.write(
            "Commands: /company, /ticker SYMBOL, "
            "/clear, exit"
        )

        if current_ticker:
            self.stdout.write(
                f"Starting company: {current_ticker}"
            )

        while True:
            self.stdout.write("")

            try:
                question = input("You > ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("")
                self.stdout.write("Exiting.")
                return

            if not question:
                continue

            if question.lower() in {
                "exit",
                "quit",
            }:
                self.stdout.write("Exiting.")
                return

            if question.lower() == "/company":
                if current_ticker:
                    self.stdout.write(
                        f"Current company: {current_ticker}"
                    )
                else:
                    self.stdout.write(
                        "No company selected yet."
                    )
                continue

            if question.lower() == "/clear":
                current_ticker = None

                self.stdout.write(
                    "Company context cleared."
                )
                continue

            if question.lower().startswith(
                "/ticker "
            ):
                requested_ticker = (
                    question.split(
                        maxsplit=1
                    )[1]
                    .strip()
                    .upper()
                )

                try:
                    resolution = (
                        service.company_resolver.resolve(
                            f"Use {requested_ticker}",
                            ticker=requested_ticker,
                        )
                    )

                    current_ticker = (
                        resolution.ticker
                    )

                    self.stdout.write(
                        "Current company: "
                        f"{resolution.company_name} "
                        f"({resolution.ticker})"
                    )

                except CompanyResolverError as exc:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Company error: {exc}"
                        )
                    )

                continue

            try:
                result = self._answer_question(
                    service=service,
                    question=question,
                    current_ticker=current_ticker,
                    top_k=top_k,
                )

                if result.resolved_company:
                    current_ticker = (
                        result.resolved_company.ticker
                    )

                self._print_result(
                    result
                )

            except (
                KnowledgeBaseServiceError,
                GroundedQAError,
                CompanySummaryError,
                ChangeDetectionError,
            ) as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f"Error: {exc}"
                    )
                )

    def _answer_question(
        self,
        *,
        service,
        question,
        current_ticker,
        top_k,
    ):
        """
        First try to resolve a company directly from
        the new question.

        If no company is mentioned, fall back to the
        successful company from the previous question.
        """

        try:
            return service.answer(
                question,
                top_k=top_k,
            )

        except KnowledgeBaseServiceError as exc:
            message = str(exc)

            no_company_found = (
                "Could not determine which company"
                in message
            )

            if (
                no_company_found
                and current_ticker
            ):
                return service.answer(
                    question,
                    ticker=current_ticker,
                    top_k=top_k,
                )

            raise

    def _print_result(
        self,
        result,
    ):
        company = result.resolved_company

        self.stdout.write("")

        if company:
            self.stdout.write(
                f"Company: "
                f"{company.company_name} "
                f"({company.ticker})"
            )

        self.stdout.write(
            f"Intent: {result.intent.value}"
        )

        payload = result.result

        self.stdout.write("")
        self.stdout.write("Assistant >")
        self.stdout.write("")

        if hasattr(
            payload,
            "answer",
        ):
            self.stdout.write(
                str(payload.answer)
            )

        elif hasattr(
            payload,
            "summary",
        ):
            self.stdout.write(
                str(payload.summary)
            )

        else:
            self.stdout.write(
                str(payload)
            )

        self._print_sources(
            payload
        )

    def _print_sources(
        self,
        payload,
    ):
        if hasattr(
            payload,
            "answer",
        ):
            citations = list(
                getattr(
                    payload,
                    "citations",
                    [],
                )
            )

        else:
            citations = list(
                getattr(
                    payload,
                    "used_citations",
                    [],
                )
            )

        if citations:
            self.stdout.write("")
            self.stdout.write("Sources:")

            for citation in citations:
                source_id = getattr(
                    citation,
                    "source_id",
                    "",
                )

                ticker = getattr(
                    citation,
                    "ticker",
                    "",
                )

                form = getattr(
                    citation,
                    "form",
                    "",
                )

                filing_date = getattr(
                    citation,
                    "filing_date",
                    "",
                )

                accession = getattr(
                    citation,
                    "accession_number",
                    "",
                )

                document_type = getattr(
                    citation,
                    "document_type",
                    "",
                )

                section = getattr(
                    citation,
                    "section_title",
                    "",
                )

                parts = [
                    str(value)
                    for value in (
                        ticker,
                        form,
                        filing_date,
                        accession,
                        document_type,
                        section,
                    )
                    if value
                ]

                self.stdout.write(
                    f"[{source_id}] "
                    + " | ".join(parts)
                )

            return

        source_urls = list(
            getattr(
                payload,
                "source_urls",
                [],
            )
        )

        if source_urls:
            self.stdout.write("")
            self.stdout.write(
                f"SEC source files: "
                f"{len(source_urls)}"
            )