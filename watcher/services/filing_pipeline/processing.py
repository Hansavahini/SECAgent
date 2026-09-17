from pathlib import Path


class FilingProcessingService:
    """
    Process exactly one discovered filing.

    Stage order is intentionally unchanged:

        resolve document
        -> duplicate check
        -> download
        -> registry mark
        -> metadata
        -> registration
        -> post-processing
    """

    def __init__(
        self,
        *,
        downloader,
        registry,
        registration_service,
        metadata_service,
        post_processing_service,
        output_service,
    ):
        self.downloader = downloader
        self.registry = registry
        self.registration_service = registration_service
        self.metadata_service = metadata_service
        self.post_processing = post_processing_service
        self.output = output_service

    @staticmethod
    def _result():
        return {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "indexed": 0,
            "index_failed": 0,
            "errors": [],
        }

    @staticmethod
    def _merge_post_result(result, post_result):
        result["indexed"] += post_result["indexed"]
        result["index_failed"] += post_result["index_failed"]
        result["errors"].extend(post_result["errors"])

    def process(
        self,
        *,
        company,
        ticker,
        cik,
        form,
        filing,
    ):
        result = self._result()

        accession_number = filing["accession_number"]
        primary_document = filing["primary_document"]
        filing_date = filing["filing_date"]

        try:
            base_url, document = self.downloader.resolve_document(
                cik=cik,
                accession_number=accession_number,
                file_type=form,
                hint_filename=primary_document,
            )

            sequence = str(
                document.get("sequence") or ""
            ).strip()

            if not sequence:
                raise ValueError(
                    "SEC document sequence could not be resolved"
                )

            if self.registry.is_downloaded(
                cik,
                accession_number,
                sequence,
            ):
                result["skipped"] = 1
                return result

            file_type = document.get("type") or form
            original_filename = document["filename"]

            local_filename = self.downloader.build_filename(
                form=form,
                file_type=file_type,
                filing_date=filing_date,
                original_filename=original_filename,
            )

            download = self.downloader.download(
                cik=cik,
                accession_number=accession_number,
                file_type=file_type,
                sequence=sequence,
                local_filename=local_filename,
                hint_filename=primary_document,
                document=document,
                base_url=base_url,
                ticker=ticker,
                form=form,
            )

            downloaded_sequence = str(
                download.get("sequence") or sequence
            ).strip()

            # Preserve existing semantics: mark only after the file
            # has been written successfully.
            self.registry.mark_downloaded(
                cik,
                accession_number,
                downloaded_sequence,
            )
            result["downloaded"] = 1

            saved_path = str(download["path"])
            saved_filename = Path(saved_path).name
            resolved_form_type = str(
                document.get("type") or form
            ).strip()
            source_url = str(
                download.get("url") or ""
            ).strip()

            metadata = self.metadata_service.prepare(
                filing=filing,
                expected_cik=cik,
                expected_company_name=company.get("name", ""),
                expected_ticker=ticker,
            )

            self.output.new_filing(
                ticker=ticker,
                form_type=resolved_form_type,
                filing_date=filing_date,
                accession_number=accession_number,
                sequence=downloaded_sequence,
                filename=saved_filename,
                saved_path=saved_path,
                source_url=source_url,
            )

            try:
                registered_filing = self.registration_service.register(
                    ticker=ticker,
                    cik=cik,
                    company_name=company.get("name", ""),
                    form=form,
                    accession_number=accession_number,
                    sequence=downloaded_sequence,
                    filing_date=filing_date,
                    primary_document=primary_document,
                    local_path=download["path"],
                    source_url=download["url"],
                    accepted_at=metadata.accepted_at,
                    entry_session=metadata.entry_session,
                )

                self.output.status("REGISTRATION", "SUCCESS")
                self.output.metadata(
                    metadata=metadata,
                    form=form,
                )
            except Exception as exc:
                result["errors"].append(
                    "Knowledge-base registration failed for "
                    f"{accession_number}: {exc}"
                )
                self.output.status(
                    "REGISTRATION",
                    f"FAILED ({exc})",
                )
                return result

            post_result = self.post_processing.run(
                registered_filing=registered_filing,
                form=form,
                accession_number=accession_number,
                metadata=metadata,
                filename=saved_filename,
                saved_path=saved_path,
                source_url=source_url,
            )

            self._merge_post_result(result, post_result)
            return result

        except Exception as exc:
            result["failed"] = 1
            result["errors"].append(
                f"{accession_number}: {exc}"
            )
            return result
