import os

import requests


class GenerationServiceError(Exception):
    """Raised when Ollama cannot generate a response."""


class OllamaGenerationService:
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"
    DEFAULT_MODEL = "llama3.1:8b"

    def __init__(
        self,
        *,
        base_url=None,
        model_name=None,
        session=None,
    ):
        self.base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")

        self.model_name = (
            model_name
            or os.getenv("OLLAMA_GENERATION_MODEL")
            or self.DEFAULT_MODEL
        )

        self.session = session or requests.Session()

    def generate(
        self,
        prompt: str,
        *,
        temperature=0.0,
        max_tokens=512,
    ) -> str:

        prompt = str(prompt or "").strip()

        if not prompt:
            raise GenerationServiceError(
                "Generation prompt cannot be empty."
            )

        try:
            response = self.session.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,

                    # Qwen3 should answer directly rather than
                    # spending time on an internal thinking pass.
                    "think": False,

                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                        "num_ctx": 16384,
                    },
                },
                timeout=(5, 300),
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            raise GenerationServiceError(
                f"Ollama generation request failed: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise GenerationServiceError(
                "Ollama returned invalid JSON."
            ) from exc

        answer = payload.get("response")

        if not isinstance(answer, str):
            raise GenerationServiceError(
                "Ollama response contains no generated text."
            )

        answer = answer.strip()

        if not answer:
            raise GenerationServiceError(
                "Ollama returned an empty response."
            )

        return answer