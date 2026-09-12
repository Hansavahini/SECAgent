import os

import requests


class EmbeddingServiceError(Exception):
    """Raised when an embedding cannot be generated."""


class OllamaEmbeddingService:
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    DEFAULT_MODEL = "nomic-embed-text:latest"
    DEFAULT_DIMENSIONS = 768

    def __init__(
        self,
        *,
        base_url=None,
        model_name=None,
        dimensions=None,
        session=None,
    ):
        self.base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")

        self.model_name = (
            model_name
            or os.getenv("OLLAMA_EMBED_MODEL")
            or self.DEFAULT_MODEL
        )

        self.dimensions = int(
            dimensions
            or os.getenv("OLLAMA_EMBED_DIMENSIONS")
            or self.DEFAULT_DIMENSIONS
        )

        self.session = session or requests.Session()

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        if not texts:
            raise EmbeddingServiceError(
                "At least one text is required."
            )

        cleaned = []

        for text in texts:
            if not isinstance(text, str):
                raise EmbeddingServiceError(
                    "Embedding input must be text."
                )

            value = text.strip()

            if not value:
                raise EmbeddingServiceError(
                    "Embedding input cannot be empty."
                )

            cleaned.append(value)

        try:
            response = self.session.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model_name,
                    "input": cleaned,
                },
                timeout=(5, 120),
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            raise EmbeddingServiceError(
                f"Ollama embedding request failed: {exc}"
            ) from exc

        payload = response.json()
        embeddings = payload.get("embeddings")

        if not isinstance(embeddings, list):
            raise EmbeddingServiceError(
                "Ollama response contains no embeddings."
            )

        if len(embeddings) != len(cleaned):
            raise EmbeddingServiceError(
                "Embedding count does not match input count."
            )

        for vector in embeddings:
            if len(vector) != self.dimensions:
                raise EmbeddingServiceError(
                    "Embedding dimension mismatch: "
                    f"expected {self.dimensions}, "
                    f"received {len(vector)}."
                )

        return embeddings