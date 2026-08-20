"""Unit coverage for optional embedding and token-counting paths."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.embeddings.openai_compat_embedder import OpenAICompatEmbedder
from app.core.embeddings import token_counter

pytestmark = pytest.mark.unit


class _EmbeddingClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = iter(responses)
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> httpx.Response:
        self.requests.append({"url": url, "headers": headers, "json": json})
        return next(self.responses)


def _response(payload: dict, url: str = "https://embeddings.test/v1/embeddings"):
    return httpx.Response(200, json=payload, request=httpx.Request("POST", url))


@pytest.mark.asyncio
async def test_openai_compat_embedder_batches_and_orders_results(monkeypatch):
    client = _EmbeddingClient(
        [
            _response(
                {
                    "data": [
                        {"index": 1, "embedding": [2.0, 3.0]},
                        {"index": 0, "embedding": [0.0, 1.0]},
                    ]
                }
            )
        ]
    )
    monkeypatch.setattr(
        "app.core.embeddings.openai_compat_embedder.httpx.AsyncClient",
        lambda **_: client,
    )
    monkeypatch.setattr(settings, "lemma_openai_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "lemma_openai_base_url", "https://embeddings.test/v1")

    embedder = OpenAICompatEmbedder(model="test-model", dimension=2)
    assert await embedder.embed_batch(["one", "two"]) == [[0.0, 1.0], [2.0, 3.0]]
    assert client.requests[0] == {
        "url": "https://embeddings.test/v1/embeddings",
        "headers": {"Authorization": "Bearer test-key"},
        "json": {
            "model": "test-model",
            "input": ["one", "two"],
            "dimensions": 2,
        },
    }


@pytest.mark.asyncio
async def test_openai_compat_embedder_rejects_missing_credentials(monkeypatch):
    monkeypatch.setattr(settings, "lemma_openai_api_key", None)
    assert await OpenAICompatEmbedder(dimension=2).embed_batch([]) == []
    with pytest.raises(RuntimeError, match="require LEMMA_OPENAI_API_KEY"):
        await OpenAICompatEmbedder(dimension=2).embed("text")


@pytest.mark.asyncio
async def test_openai_compat_embedder_rejects_wrong_dimensions(monkeypatch):
    client = _EmbeddingClient(
        [_response({"data": [{"index": 0, "embedding": [1.0]}]})]
    )
    monkeypatch.setattr(
        "app.core.embeddings.openai_compat_embedder.httpx.AsyncClient",
        lambda **_: client,
    )
    monkeypatch.setattr(settings, "lemma_openai_api_key", SecretStr("test-key"))

    with pytest.raises(ValueError, match="expected 2"):
        await OpenAICompatEmbedder(dimension=2).embed("text")


class _Encoding:
    def encode(self, text: str) -> list[int]:
        return list(range(len(text)))

    def decode(self, values: list[int]) -> str:
        return "".join(str(value) for value in values)


def test_token_counter_counts_and_truncates(monkeypatch):
    monkeypatch.setattr(token_counter, "_get_encoding", lambda _: _Encoding())

    assert token_counter.num_tokens_from_string("abcd") == 4
    assert token_counter.prefix_by_token("abcd", 4) == "abcd"
    truncated = token_counter.prefix_by_token("abcd", 2)
    assert truncated.startswith("01...")
    assert "2 tokens out of 4 tokens" in truncated


def test_token_counter_caches_encoding(monkeypatch):
    calls: list[str] = []

    def get_encoding(name: str):
        calls.append(name)
        return SimpleNamespace(encode=lambda text: [text], decode=lambda values: values[0])

    token_counter._get_encoding.cache_clear()
    monkeypatch.setattr(token_counter, "_get_encoding", __import__("functools").lru_cache()(get_encoding))
    token_counter.num_tokens_from_string("one", "test")
    token_counter.num_tokens_from_string("two", "test")
    assert calls == ["test"]
