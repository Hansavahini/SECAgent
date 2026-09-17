class FilingOutputService:
    """Console output only; no filing business logic."""

    @staticmethod
    def status(label, value):
        print(f"{label:<13}: {value}")

    @staticmethod
    def separator():
        print("=" * 70)

    def new_filing(
        self,
        *,
        ticker,
        form_type,
        filing_date,
        accession_number,
        sequence,
        filename,
        saved_path,
        source_url,
    ):
        print()
        self.separator()
        print("NEW SEC FILING DETECTED")
        self.separator()
        self.status("Ticker", ticker)
        self.status("Form", form_type)
        self.status("Filing Date", filing_date)
        self.status("Accession", accession_number)
        self.status("Sequence", sequence)
        self.status("Filename", filename)
        self.status("Saved Path", saved_path)
        self.status("SEC URL", source_url)
        self.status("DOWNLOAD", "SUCCESS")
        self.separator()

    def metadata(self, *, metadata, form):
        if metadata.timestamp_error:
            self.status(
                "EDGAR TIME",
                f"UNAVAILABLE ({metadata.timestamp_error})",
            )

        if metadata.accepted_at is not None:
            self.status("EDGAR ACCEPT", metadata.accepted_at_display)
        else:
            self.status("EDGAR ACCEPT", "NOT AVAILABLE")

        if metadata.session_error:
            self.status(
                "ENTRY SESSION",
                f"UNAVAILABLE ({metadata.session_error})",
            )
        elif metadata.entry_session is not None:
            self.status("ENTRY SESSION", metadata.entry_session)
        else:
            self.status("ENTRY SESSION", "NOT AVAILABLE")

        if metadata.sec_item_codes:
            self.status(
                "SEC ITEMS",
                ", ".join(metadata.sec_item_codes),
            )
        elif str(form).strip().upper() == "8-K":
            self.status("SEC ITEMS", "NOT AVAILABLE")

        verification_error = getattr(
            metadata,
            "company_verification_error",
            "",
        )
        verification = getattr(
            metadata,
            "company_verification",
            None,
        )

        if verification_error:
            self.status(
                "COMPANY VERIFY",
                f"UNAVAILABLE ({verification_error})",
            )
        elif verification is not None:
            self.status("COMPANY VERIFY", verification.status)
            self.status(
                "SEC COMPANY",
                verification.sec_company_name or "NOT AVAILABLE",
            )
            self.status(
                "SEC CIK",
                verification.sec_cik or "NOT AVAILABLE",
            )

    def item_verification(self, *, verification, error):
        if error:
            self.status("ITEM VERIFY", f"UNAVAILABLE ({error})")
            return

        if verification is None:
            return

        self.status("ITEM VERIFY", verification.status)
        self.status(
            "SEC ITEMS",
            ", ".join(verification.sec_items) or "NOT AVAILABLE",
        )
        self.status(
            "PARSED ITEMS",
            ", ".join(verification.parsed_items) or "NOT AVAILABLE",
        )

        if verification.missing_from_parser:
            self.status(
                "ITEM MISSING",
                ", ".join(verification.missing_from_parser),
            )

        if verification.extra_in_parser:
            self.status(
                "ITEM EXTRA",
                ", ".join(verification.extra_in_parser),
            )

    def finish(self):
        self.separator()
        print()
