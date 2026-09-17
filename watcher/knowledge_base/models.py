from django.db import models
from pgvector.django import VectorField


class Company(models.Model):
    ticker = models.CharField(
        max_length=16,
        unique=True,
    )

    cik = models.CharField(
        max_length=10,
        unique=True,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["ticker"]

    def __str__(self):
        return f"{self.ticker} ({self.cik})"


class Filing(models.Model):
    class IngestionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        CHUNKED = "chunked", "Chunked"
        INDEXED = "indexed", "Indexed"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="filings",
    )

    accession_number = models.CharField(
        max_length=32,
    )

    sequence = models.PositiveIntegerField(
        default=1,
    )

    form = models.CharField(
        max_length=10,
    )

    filing_date = models.DateField(
        null=True,
        blank=True,
    )

    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    entry_session = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )


    primary_document = models.CharField(
        max_length=255,
        blank=True,
    )

    source_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    local_path = models.TextField()

    content_sha256 = models.CharField(
        max_length=64,
        blank=True,
    )

    file_size = models.BigIntegerField(
        null=True,
        blank=True,
    )

    ingestion_status = models.CharField(
        max_length=20,
        choices=IngestionStatus.choices,
        default=IngestionStatus.PENDING,
        db_index=True,
    )

    downloaded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "accession_number",
                    "sequence",
                ],
                name="uq_filing_identity",
            )
        ]

        indexes = [
            models.Index(
                fields=[
                    "company",
                    "form",
                    "filing_date",
                ],
                name="idx_filing_lookup",
            ),
        ]

    def __str__(self):
        return (
            f"{self.company.ticker} "
            f"{self.form} "
            f"{self.accession_number}"
        )


class EmbeddingVersion(models.Model):
    provider = models.CharField(
        max_length=50,
        default="ollama",
    )

    model_name = models.CharField(
        max_length=255,
    )

    dimensions = models.PositiveIntegerField()

    chunker_version = models.CharField(
        max_length=50,
    )

    is_active = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "provider",
                    "model_name",
                    "dimensions",
                    "chunker_version",
                ],
                name="uq_embedding_version",
            )
        ]

    def __str__(self):
        return (
            f"{self.provider}:"
            f"{self.model_name} "
            f"({self.dimensions})"
        )


class FilingChunk(models.Model):
    filing = models.ForeignKey(
        Filing,
        on_delete=models.CASCADE,
        related_name="chunks",
    )

    chunk_index = models.PositiveIntegerField()

    item_number = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
    )

    section_title = models.CharField(
        max_length=500,
        blank=True,
    )

    text = models.TextField()

    content_sha256 = models.CharField(
        max_length=64,
        db_index=True,
    )

    char_start = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    char_end = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    document = models.ForeignKey(
        "FilingDocument",
        on_delete=models.CASCADE,
        related_name="chunks",
        null=True,
        blank=True,
    )
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "document",
                    "chunk_index",
                ],
                name="uq_document_chunk",
            )
        ]

        ordering = [
            "filing_id",
            "chunk_index",
        ]

    def __str__(self):
        return (
            f"{self.filing_id}:"
            f"{self.chunk_index}"
        )

class FilingDocument(models.Model):
    filing = models.ForeignKey(
        Filing,
        on_delete=models.CASCADE,
        related_name="documents",
    )

    sequence = models.CharField(
        max_length=32,
        blank=True,
    )

    document_type = models.CharField(
        max_length=32,
        blank=True,
        db_index=True,
    )

    document_name = models.CharField(
        max_length=255,
    )

    description = models.CharField(
        max_length=500,
        blank=True,
    )

    source_url = models.URLField(
        max_length=1000,
        blank=True,
    )

    local_path = models.TextField(
        blank=True,
    )

    content_sha256 = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
    )

    file_size = models.BigIntegerField(
        null=True,
        blank=True,
    )

    is_primary = models.BooleanField(
        default=False,
        db_index=True,
    )

    downloaded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "filing",
                    "sequence",
                    "document_name",
                ],
                name="uq_filing_document_identity",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "filing",
                    "document_type",
                ],
                name="idx_filing_document_type",
            ),
        ]

        ordering = [
            "filing_id",
            "sequence",
            "id",
        ]

    def __str__(self):
        return (
            f"{self.filing} "
            f"{self.document_type} "
            f"{self.document_name}"
        )
    
class ChunkEmbedding(models.Model):
    chunk = models.ForeignKey(
        FilingChunk,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )

    embedding_version = models.ForeignKey(
        EmbeddingVersion,
        on_delete=models.PROTECT,
        related_name="chunk_embeddings",
    )

    vector = VectorField(
        dimensions=768,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "chunk",
                    "embedding_version",
                ],
                name="uq_chunk_embedding_version",
            )
        ]

    def __str__(self):
        return (
            f"Chunk {self.chunk_id} - "
            f"{self.embedding_version.model_name}"
        )


class IngestionJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    filing = models.OneToOneField(
        Filing,
        on_delete=models.CASCADE,
        related_name="ingestion_job",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    attempt_count = models.PositiveIntegerField(
        default=0,
    )

    last_error = models.TextField(
        blank=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    next_retry_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return (
            f"{self.filing_id}:"
            f"{self.status}"
        )

class CompanyAlias(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="aliases",
    )

    alias = models.CharField(
        max_length=255,
    )

    normalized_alias = models.CharField(
        max_length=255,
        db_index=True,
    )

    source = models.CharField(
        max_length=50,
        default="system",
    )

    is_active = models.BooleanField(
        default=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "company",
                    "normalized_alias",
                ],
                name="uq_company_normalized_alias",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "normalized_alias",
                    "is_active",
                ],
                name="idx_company_alias_lookup",
            ),
        ]

        ordering = [
            "company_id",
            "normalized_alias",
        ]

    def __str__(self):
        return (
            f"{self.company.ticker}: "
            f"{self.alias}"
        )

class FilingSummaryCache(models.Model):
    filing = models.OneToOneField(
        Filing,
        on_delete=models.CASCADE,
        related_name="summary_cache",
    )

    content_signature = models.CharField(
        max_length=64,
        db_index=True,
    )

    model_name = models.CharField(
        max_length=255,
    )

    prompt_version = models.CharField(
        max_length=50,
    )

    summary = models.TextField()

    document_count = models.PositiveIntegerField(
        default=0,
    )

    chunk_count = models.PositiveIntegerField(
        default=0,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "model_name",
                    "prompt_version",
                ],
                name="idx_filing_summary_version",
            ),
        ]

    def __str__(self):
        return (
            f"{self.filing} "
            f"[{self.model_name} / "
            f"{self.prompt_version}]"
        )
class DocumentSummaryCache(models.Model):
    """
    Stores one concise generated summary for one SEC filing document.

    The original document text remains in FilingDocument/FilingChunk.
    This table stores only the derived summary and cache metadata.
    """

    document = models.OneToOneField(
        FilingDocument,
        on_delete=models.CASCADE,
        related_name="summary_cache",
    )

    content_signature = models.CharField(
        max_length=64,
        db_index=True,
    )

    model_name = models.CharField(
        max_length=255,
    )

    prompt_version = models.CharField(
        max_length=50,
    )

    summary = models.TextField()

    chunk_count = models.PositiveIntegerField(
        default=0,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        indexes = [
            models.Index(
                fields=[
                    "model_name",
                    "prompt_version",
                ],
                name="idx_document_summary_version",
            ),
        ]

    def __str__(self):
        return (
            f"{self.document} "
            f"[{self.model_name} / "
            f"{self.prompt_version}]"
        )