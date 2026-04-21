"""Local-only Ollama HTTP client for Mindwall.

Design constraints:
  - All requests go to the configured local Ollama instance only.
  - No cloud fallback, no external API keys.
  - Explicit timeout prevents hung analysis pipelines.
  - JSON-mode request keeps output structured.
  - Failure is surfaced as OllamaError, never silently swallowed.
  - The interface is thin so tests can mock it cleanly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)

# Maximum characters we store from the raw LLM response (safety bound)
_MAX_RAW_RESPONSE_CHARS = 8_000


class OllamaError(Exception):
    """Raised when the Ollama client cannot complete a request."""


@dataclass
class OllamaResponse:
    """Parsed response from an Ollama generate call."""

    raw_text: str          # Full model output text
    model: str             # Model name echoed back by Ollama
    done: bool             # Whether the response was complete


class OllamaClient:
    """Thin async HTTP client for the local Ollama inference server.

    This client speaks to Ollama's /api/generate endpoint.  It does not
    use streaming — it waits for the full response so we get clean JSON.

    Usage::

        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1:8b")
        response = await client.generate(prompt="...")
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        *,
        _http: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith(("http://localhost", "http://127.", "http://[::1]")):
            # Enforce local-only posture — reject any non-local base URL
            raise ValueError(
                f"Ollama base URL must be a localhost address, got: {base_url!r}. "
                "Mindwall does not support remote LLM inference."
            )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        # An optional pre-built httpx client for testing (injected transport)
        self._http = _http

    async def generate(self, prompt: str) -> OllamaResponse:
        """Send a prompt to the local Ollama model and return the full response.

        Raises:
            OllamaError: If the request fails, times out, or Ollama returns a
                         non-200 status.
        """
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        log.debug(
            "ollama.request",
            model=self._model,
            prompt_chars=len(prompt),
        )

        try:
            if self._http is not None:
                resp = await self._http.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._base_url}/api/generate",
                        json=payload,
                    )
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama request timed out after {self._timeout}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is the Ollama service running?"
            ) from exc
        except httpx.RequestError as exc:
            raise OllamaError(f"Ollama request error: {exc}") from exc

        if resp.status_code != 200:
            raise OllamaError(
                f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise OllamaError(
                f"Ollama returned non-JSON response: {resp.text[:200]}"
            ) from exc

        raw_text = body.get("response", "")
        model_name = body.get("model", self._model)
        done = body.get("done", True)

        log.debug(
            "ollama.response",
            model=model_name,
            response_chars=len(raw_text),
            done=done,
        )

        return OllamaResponse(
            raw_text=raw_text[:_MAX_RAW_RESPONSE_CHARS],
            model=model_name,
            done=done,
        )

    async def health_check(self) -> bool:
        """Return True if the Ollama server is reachable."""
        try:
            if self._http is not None:
                resp = await self._http.get(f"{self._base_url}/api/tags")
            else:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self._base_url}/api/tags")
            return resp.status_code == 200
        except Exception:
            return False
