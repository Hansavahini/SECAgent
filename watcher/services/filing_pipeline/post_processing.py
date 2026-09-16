class FilingPostProcessingService:
    """
    Preserve the established post-registration flow:

        index -> item verification -> summary -> email
    """

    def __init__(
        self,
        *,
        auto_index,
        indexing_service,
        summary_service,
        metadata_service,
        email_service,
        notification_service,
        output_service,
    ):
        self.auto_index = bool(auto_index)
        self.indexing_service = indexing_service
        self.summary_service = summary_service
        self.metadata_service = metadata_service
        self.email_service = email_service
        self.notification_service = notification_service
        self.output = output_service

    @staticmethod
    def _result():
        return {
            "indexed": 0,
            "index_failed": 0,
            "errors": [],
        }

    def run(
        self,
        *,
        registered_filing,
        form,
        accession_number,
        metadata,
        filename,
        saved_path,
        source_url,
    ):
        result = self._result()

        if not self.auto_index:
            self.output.status("INDEXING", "DISABLED")
            self.output.status("SUMMARY", "NOT GENERATED")
            self.output.status("EMAIL", "NOT SENT")
            self.output.finish()
            return result

        if self.indexing_service is None:
            self.output.status("INDEXING", "NOT AVAILABLE")
            self.output.status("SUMMARY", "NOT GENERATED")
            self.output.status("EMAIL", "NOT SENT")
            self.output.finish()
            return result

        try:
            self.indexing_service.index_filing(registered_filing)
            result["indexed"] = 1
            self.output.status("INDEXING", "SUCCESS")
        except Exception as exc:
            result["index_failed"] = 1
            result["errors"].append(
                "Knowledge-base indexing failed for "
                f"{accession_number}: {exc}"
            )
            self.output.status("INDEXING", f"FAILED ({exc})")
            return result

        item_verification, verification_error = (
            self.metadata_service.verify_items(
                filing=registered_filing,
                form=form,
                sec_item_codes=metadata.sec_item_codes,
            )
        )

        self.output.item_verification(
            verification=item_verification,
            error=verification_error,
        )

        if self.summary_service is None:
            self.output.status("SUMMARY", "NOT AVAILABLE")
            self.output.status("EMAIL", "NOT SENT")
            self.output.finish()
            return result

        try:
            summary_result = self.summary_service.summarize_filing(
                registered_filing.id
            )
            self.output.status("SUMMARY", "GENERATED SUCCESSFULLY")
            self.output.status("POSTGRESQL", "SUMMARY STORED/REUSED")
        except Exception as exc:
            result["errors"].append(
                "Filing summary failed for "
                f"{accession_number}: {exc}"
            )
            self.output.status("SUMMARY", f"FAILED ({exc})")
            self.output.status("EMAIL", "NOT SENT")
            self.output.finish()
            return result

        try:
            email_sent = self.email_service.send(
                summary_result=summary_result,
                filename=filename,
                saved_path=saved_path,
                source_url=source_url,
                metadata=metadata,
                item_verification=item_verification,
            )
        except Exception as exc:
            self.output.status("EMAIL", f"FAILED ({exc})")
            self.output.finish()
            return result

        if email_sent:
            self.output.status("EMAIL", "SENT SUCCESSFULLY")
            recipient = getattr(
                self.notification_service,
                "recipient",
                "",
            )
            if recipient:
                self.output.status("Recipient", recipient)
        else:
            self.output.status(
                "EMAIL",
                "NOT SENT (check SMTP configuration/logs)",
            )

        self.output.finish()
        return result
