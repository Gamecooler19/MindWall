"""Unit tests for app.analysis.ollama_client.

Uses httpx mock transport — no real network access.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.analysis.ollama_client import OllamaClient, OllamaError, OllamaResponse

# ---------------------------------------------------------------------------
# Helpers: mock HTTPX transports
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """Return a fixed response for any request."""

    def __init__(self, status_code: int, body: dict | str) -> None:
        self._status = status_code
        self._body = json.dumps(body) if isinstance(body, dict) else body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=self._status,
            content=self._body.encode(),
            headers={"content-type": "application/json"},
        )


class _TimeoutTransport(httpx.AsyncBaseTransport):
    """Always raises a timeout."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)


class _ConnectErrorTransport(httpx.AsyncBaseTransport):
    """Always raises a connect error."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)


def _client_with_transport(transport: httpx.AsyncBaseTransport) -> OllamaClient:
    """Create an OllamaClient with an injected async HTTPX transport."""
    http = httpx.AsyncClient(transport=transport, base_url="http://localhost:11434")
    return OllamaClient(
        base_url="http://localhost:11434",
        model="test-model",
        timeout=30.0,
        _http=http,
    )


# ---------------------------------------------------------------------------
# Localhost enforcement
# ---------------------------------------------------------------------------


def test_localhost_url_accepted():
    c = OllamaClient(base_url="http://localhost:11434", model="m")
    assert c is not None


def test_127_url_accepted():
    c = OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    assert c is not None


def test_non_localhost_url_rejected():
    with pytest.raises(ValueError, match="localhost"):
        OllamaClient(base_url="https://external-api.example.com", model="m")


def test_cloud_url_rejected():
    with pytest.raises(ValueError):
        OllamaClient(base_url="https://api.openai.com", model="gpt-4")


# ---------------------------------------------------------------------------
# generate — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_success():
    response_body = {"response": '{"foo": "bar"}', "model": "llama3.1:8b", "done": True}
    client = _client_with_transport(_MockTransport(200, response_body))
    result = await client.generate("Test prompt")
    assert isinstance(result, OllamaResponse)
    assert result.model == "llama3.1:8b"
    assert result.done is True
    assert "foo" in result.raw_text


# ---------------------------------------------------------------------------
# generate — HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_http_error_raises_ollama_error():
    client = _client_with_transport(_MockTransport(503, {"error": "service unavailable"}))
    with pytest.raises(OllamaError):
        await client.generate("Test prompt")


# ---------------------------------------------------------------------------
# generate — timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_timeout_raises_ollama_error():
    client = _client_with_transport(_TimeoutTransport())
    with pytest.raises(OllamaError, match=r"[Tt]imeout|timed"):
        await client.generate("Test prompt")


# ---------------------------------------------------------------------------
# generate — connect error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_connect_error_raises_ollama_error():
    client = _client_with_transport(_ConnectErrorTransport())
    with pytest.raises(OllamaError):
        await client.generate("Test prompt")


# ---------------------------------------------------------------------------
# health_check — returns True on 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_true_on_200():
    client = _client_with_transport(_MockTransport(200, {"models": []}))
    result = await client.health_check()
    assert result is True


# ---------------------------------------------------------------------------
# health_check — returns False on error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_false_on_timeout():
    client = _client_with_transport(_TimeoutTransport())
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_false_on_connect_error():
    client = _client_with_transport(_ConnectErrorTransport())
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_false_on_500():
    client = _client_with_transport(_MockTransport(500, {"error": "internal server error"}))
    result = await client.health_check()
    assert result is False
